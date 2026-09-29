"""
Run an agent task inside a gateway-mode VM and retrieve its logs.

Flow: create an overlay VM, start the API relay on the host, deploy the software
bundles, install the selected harness, run the agent non-interactively,
then retrieve every artifact it produced. The model key never enters the guest;
completion is detected by polling the QGA exec session and cross-checked with an
exitcode file the wrapper writes inside the guest.
"""

import datetime
import json
from pathlib import Path
import shlex
import shutil
import sys
import time
from typing import Final

from . import api_relay
from ..qga import QgaClient, QgaError
from .agent_config import load_agent_config
from .credentials import find_profile, load_keys
from .deploy import BundleConfig, DeployError, install_bundles, parse_spec, upload_tree
from .guest import GuestError, write_file
from .harness import distribution_dir, get_harness, GUESTFWD_BASE_URL, HarnessError, install_script
from .qemu_vm import (
    create_overlay,
    qga_socket_path,
    RELAY_LOG_NAME,
    run_lock,
    start_vm,
    stop_vm,
    USAGE_NAME,
    vm_status,
    VmError,
    wait_ready,
)

AGENT_POLL_INTERVAL: Final = 15.0
GATEWAY_CHECK_TIMEOUT: Final = 60.0
# The card writes budget.maximum_tokens in millions of tokens.
TOKENS_PER_BUDGET_UNIT: Final = 1_000_000

END_COMPLETED: Final = 'completed'
END_HARNESS_ERROR: Final = 'harness_error'
END_TIME_BUDGET: Final = 'time_budget'
END_TOKEN_BUDGET: Final = 'token_budget'
END_ENV_FAILURE: Final = 'environment_failure'


class AgentRunError(RuntimeError):
    """The run could not reach or finish the agent stage."""


def _check_gateway(qga: QgaClient, attempts: int = 3) -> str:
    """
    Verify the guest can reach the model API through guestfwd and the relay.

    Args:
        `qga` (QgaClient): Guest agent client used to run the probe command in the guest.
        `attempts` (int): Probe retries; the first guestfwd-cmd spawn may need a moment.

    Returns:
        `status` (str): The HTTP status code seen by the guest.

    Raises:
        AgentRunError: Every probe failed or returned an unexpected status.
    """
    probe = (
        "python3 -c \"import urllib.request; "
        f"r = urllib.request.urlopen('{GUESTFWD_BASE_URL}/models', timeout=20); "
        "print(r.status)\""
    )
    last_error = ''
    for attempt in range(1, attempts + 1):
        result = qga.run(['/bin/bash', '-c', probe], timeout=GATEWAY_CHECK_TIMEOUT)
        status = result.stdout.decode().strip()
        if result.exitcode == 0 and status == '200':
            return status
        last_error = f'exit={result.exitcode} status={status!r} stderr={result.stderr.decode()[-1200:]}'
        time.sleep(5.0)
    raise AgentRunError(f'gateway probe failed after {attempts} attempts: {last_error}')


def _classify(agent_exited: bool, exitcode: int | None, time_exceeded: bool, token_exceeded: bool = False) -> str:
    """Map the run outcome to an end_reason value."""
    if token_exceeded:
        return END_TOKEN_BUDGET
    if time_exceeded:
        return END_TIME_BUDGET
    if not agent_exited:
        return END_ENV_FAILURE
    if exitcode == 0:
        return END_COMPLETED
    return END_HARNESS_ERROR


def _task_budget(task_dir: Path) -> tuple[float, int]:
    """
    Read the card's agent-stage budgets from task.json.

    The card writes `budget.maximum_minutes` in minutes and `budget.maximum_tokens` in millions of
    tokens; both cap the agent stage, its wall clock and its cumulative model tokens. Both are
    required: a card that states neither cannot be run, and that is settled before the VM starts.

    Args:
        `task_dir` (Path): Task directory holding task.json.

    Returns:
        `budget` (tuple[float, int]): The agent stage's (seconds, tokens).

    Raises:
        AgentRunError: The task metadata is unreadable, or a budget field is missing or malformed.
    """
    task_file = task_dir / 'task.json'
    try:
        task_data = json.loads(task_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentRunError(f'cannot read task metadata: {task_file}: {error}') from error
    budget = task_data.get('budget') if isinstance(task_data, dict) else None
    if not isinstance(budget, dict):
        raise AgentRunError(f'task metadata must contain a budget object: {task_file}')
    minutes = budget.get('maximum_minutes')
    tokens = budget.get('maximum_tokens')
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
        raise AgentRunError(f'task metadata holds a missing or malformed budget.maximum_minutes: {minutes!r}')
    if not isinstance(tokens, (int, float)) or isinstance(tokens, bool) or tokens <= 0:
        raise AgentRunError(f'task metadata holds a missing or malformed budget.maximum_tokens: {tokens!r}')
    return float(minutes) * 60.0, int(tokens) * TOKENS_PER_BUDGET_UNIT


def _usage_totals(usage_path: Path) -> tuple[int, int]:
    """
    Sum the token usage records a relay wrote for one run.

    Args:
        `usage_path` (Path): usage.jsonl the relay appended to; missing before the first response.

    Returns:
        `totals` (tuple[int, int]): (input, output) tokens over every recorded response.
    """
    if not usage_path.is_file():
        return 0, 0
    tokens_in = tokens_out = 0
    for line in usage_path.read_text(encoding='utf-8').splitlines():
        record = json.loads(line)
        tokens_in += int(record['input_tokens'])
        tokens_out += int(record['output_tokens'])
    return tokens_in, tokens_out


def _collect_run_records(run_dir: Path, out_dir: Path) -> None:
    """Copy the relay's request log and usage records next to the run summary, when the relay wrote them."""
    for name in (RELAY_LOG_NAME, USAGE_NAME):
        source = run_dir / name
        if source.is_file():
            shutil.copyfile(source, out_dir / name)


def _scan_for_key(out_dir: Path, api_key: str) -> list[str]:
    """
    Search retrieved files for the real API key.

    Args:
        `out_dir` (Path): Directory holding the retrieved artifacts and logs.
        `api_key` (str): The real model key, which must never appear in them.

    Returns:
        `hits` (list[str]): Relative paths that contain the key, empty when clean.
    """
    hits = []
    for path in sorted(out_dir.rglob('*')):
        if not path.is_file():
            continue
        if api_key.encode() in path.read_bytes():
            hits.append(str(path.relative_to(out_dir)))
    return hits


def _collect_output(qga: QgaClient, out_dir: Path) -> list[str]:
    """
    Download everything under /task/output into out_dir.

    Args:
        `qga` (QgaClient): Guest agent client used to list and download the files.
        `out_dir` (Path): Host directory that receives the files under output/.

    Returns:
        `retrieved` (list[str]): Guest paths that were retrieved.
    """
    listing = qga.run(['/bin/bash', '-c', 'cd /task/output 2>/dev/null && find . -type f -print0 | sort -z'], timeout=60.0)
    retrieved = []
    for relative in listing.stdout.decode().split('\0'):
        relative = relative.removeprefix('./')
        if not relative or '..' in relative.split('/'):
            continue
        target = out_dir / 'output' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(qga.download(f'/task/output/{relative}'))
        retrieved.append(f'/task/output/{relative}')
    return retrieved


def _task_tool_specs(task_dir: Path, tools_root: Path) -> list[tuple[str, str]]:
    """
    Read tool references from task.json and pair each with its version directory name.

    A reference is `<slug>@<version>`, the form a card stores (registry.parseToolRef), and the
    version names the tool directory that gets packed, so it is also the version string the
    install script receives and asserts against. A reference with no version takes the tool's
    only version directory, since the material set holds one version per tool.

    Args:
        `task_dir` (Path): Task directory holding task.json.
        `tools_root` (Path): Root holding <name>/install.sh and <name>/<version>/.

    Returns:
        `specs` (list[tuple[str, str]]): One (tool name, version directory name) pair per tool, python first.

    Raises:
        AgentRunError: The task metadata is unreadable or malformed, or a tool bundle is missing.
    """
    task_file = task_dir / 'task.json'
    try:
        task_data = json.loads(task_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentRunError(f'cannot read task metadata: {task_file}: {error}') from error
    refs = task_data.get('tools') if isinstance(task_data, dict) else None
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
        raise AgentRunError(f'task metadata must contain a tools array of <name>@<version> references: {task_file}')

    specs = []
    for ref in refs:
        # The split mirrors the dashboard's parseToolRef, which allows an empty version; `parse_spec`
        # requires both halves, so it does not fit here.
        name, separator, version = ref.partition('@')
        if not separator or not name:
            raise AgentRunError(f'tool reference must be <name>@<version>: {ref!r}')
        tool_dir = tools_root / name
        if not tool_dir.is_dir():
            raise AgentRunError(f'tool bundle not found: {tool_dir}')
        if not (tool_dir / 'install.sh').is_file():
            raise AgentRunError(f'tools install script not found: {tool_dir / "install.sh"}')
        if not version:
            version_dirs = sorted(path for path in tool_dir.iterdir() if path.is_dir())
            if len(version_dirs) != 1:
                raise AgentRunError(f'tool {name!r} must have exactly one version under {tool_dir}; found {[p.name for p in version_dirs]}')
            version = version_dirs[0].name
        elif not (tool_dir / version).is_dir():
            raise AgentRunError(f'tool version not found: {tool_dir / version}')
        specs.append((name, version))
    specs.sort(key=lambda item: item[0] != 'python')
    return specs


def run_agent(
    task_dir: Path,
    out_dir: Path,
    run_dir: Path,
    agent_config: Path,
    keys: Path,
    harness_root: Path,
    base_image: Path,
    tools_root: Path,
    time_budget: float | None = None,
    token_budget: int | None = None,
    smp: int = 4,
    memory: int = 16384,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False
) -> dict[str, object]:
    """
    Execute one agent run end to end.

    Args:
        `task_dir` (Path): Directory with task.json, instruction.md, and an optional input/ subdirectory.
        `out_dir` (Path): Host directory that receives logs, artifacts, and the run summary.
        `run_dir` (Path): VM run directory on local storage.
        `agent_config` (Path): JSON with `provider`, `model`, `effort`, and `harness` for this agent run.
        `keys` (Path): Credential table keyed by provider; the key itself never enters the guest.
        `harness_root` (Path): Directory of harness distributions, organized as <name>/<version>/<vendor distribution file>.
        `base_image` (Path): Frozen base qcow2 the overlay is created from.
        `tools_root` (Path): Root holding <name>/install.sh and <name>/<version>/ for every tool the task names.
        `time_budget` (float | None): Wall-clock budget in seconds for the agent stage; None takes the card's
            `budget.maximum_minutes`.
        `token_budget` (int | None): Cumulative model-token budget for the agent stage, in tokens; None takes
            the card's `budget.maximum_tokens`, which is written in millions.
        `smp` (int): vCPU count.
        `memory` (int): RAM in MiB.
        `accel` (str): `kvm` or `tcg`.
        `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live; a bare
            `:5` is pinned to loopback by start_vm.
        `keep_vm` (bool): Keep the run directory after the run.

    Returns:
        `summary` (dict[str, object]): The run summary, also written to out_dir/run-summary.json.

    Raises:
        AgentRunError: A tools bundle is missing.
        AgentConfigError: The agent configuration is unreadable or incomplete.
        CredentialsError: The provider has no entry in the credential table.
        HarnessError: The harness is unsupported or its distribution directory is invalid.
        DeployError: A tools or harness spec is malformed.
    """
    # QEMU spawns the relay from `/` after -daemonize, so the run directory is made absolute before any path is built from it.
    run_dir = run_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_agent_config(agent_config.resolve())
    profile = find_profile(load_keys(keys.resolve()), config.provider)
    harness_name, harness_version = parse_spec(config.harness)
    adapter = get_harness(harness_name)
    harness_dir = distribution_dir(harness_root, harness_name, harness_version)
    harness_script = install_script(harness_root)
    tools_root = tools_root.resolve()
    tool_specs = _task_tool_specs(task_dir, tools_root)
    tools = [f'{name}@{version}' for name, version in tool_specs]
    card_seconds, card_tokens = _task_budget(task_dir)
    if time_budget is None:
        time_budget = card_seconds
    if token_budget is None:
        token_budget = card_tokens
    if time_budget <= 0 or token_budget <= 0:
        raise AgentRunError(f'budgets must be positive: {time_budget}s / {token_budget} tokens')
    summary: dict[str, object] = {
        'run_dir': str(run_dir),
        'task_dir': str(task_dir),
        'time_budget': time_budget,
        'token_budget': token_budget,
        'tools': tools,
        'harness': harness_name,
        'harness_version': harness_version,
        'model': config.model,
        'effort': config.effort,
        'provider': config.provider,
        'started_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'end_reason': None,
        'stop_level': None
    }
    # QEMU runs this through /bin/sh, so every component is quoted: a checkout or key path containing a space
    # would otherwise split the command and leave the relay unable to start.
    relay_command = (
        f'{shlex.quote(sys.executable)} {shlex.quote(str(Path(api_relay.__file__).resolve()))} '
        f'--stdio --keys {shlex.quote(str(keys.resolve()))} --provider {shlex.quote(config.provider)} '
        f'--log {shlex.quote(str(run_dir / RELAY_LOG_NAME))} --usage {shlex.quote(str(run_dir / USAGE_NAME))}'
    )
    usage_path = run_dir / USAGE_NAME
    try:
        # Only the run holding the lock may touch the run directory: a second run pointed at the same
        # directory is refused here, before anything of the first one is stopped or removed.
        with run_lock(run_dir):
            try:
                create_overlay(run_dir, base_image)
                start_vm(run_dir, accel=accel, smp=smp, memory=memory, network='gateway', vnc=vnc, relay_command=relay_command)
                wait_ready(run_dir)
                qga = QgaClient(qga_socket_path(run_dir))
                summary['gateway_status'] = _check_gateway(qga)

                bundles = [
                    BundleConfig(
                        name=name, path=(tools_root / name).resolve(), script=(tools_root / name / 'install.sh').resolve(), version=version
                    ) for name, version in tool_specs
                ]
                bundles.append(BundleConfig(name=harness_name, path=harness_dir, script=harness_script))
                install_bundles(run_dir, bundles)
                summary['deploy'] = 'ok'

                workdir = qga.run(['/bin/bash', '-c', 'install -d -o agent -g agent /task/work /task/output'], timeout=30.0)
                if workdir.exitcode != 0:
                    raise AgentRunError(f'cannot create /task/work and /task/output: {workdir.stderr.decode()[:300]}')
                instruction = (task_dir / 'instruction.md').resolve()
                qga.upload('/task/instruction.md', instruction.read_bytes())
                if (task_dir / 'input').is_dir():
                    upload_tree(qga, task_dir / 'input', '/task/input', label='task input')
                adapter.configure(qga, {
                    'MODEL': config.model,
                    'EFFORT': config.effort
                })
                write_file(qga, '/task/run-agent.sh', adapter.run_script(), mode='755', owner='agent:agent')

                agent_started = time.monotonic()
                pid = qga.exec_start(['/usr/sbin/runuser', '-u', 'agent', '--', '/bin/bash', '/task/run-agent.sh'])
                summary['agent_pid'] = pid
                time_exceeded = False
                token_exceeded = False
                agent_exited = False
                while True:
                    status = qga.exec_status(pid)
                    if status is not None:
                        agent_exited = True
                        break
                    if time.monotonic() - agent_started > time_budget:
                        time_exceeded = True
                        # Kill by owner: the harness processes are `claude`, `codex`, `qwen`, and so on, which match
                        # neither the adapter name nor the `<name>@<version>` spec, and the wrapper runs as the agent user.
                        qga.run(['/bin/bash', '-c', 'pkill -KILL -u agent || true'], timeout=30.0)
                        break
                    if sum(_usage_totals(usage_path)) > token_budget:
                        token_exceeded = True
                        qga.run(['/bin/bash', '-c', 'pkill -KILL -u agent || true'], timeout=30.0)
                        break
                    if vm_status(run_dir)['pid'] is None:
                        raise AgentRunError('QEMU exited during the agent stage')
                    time.sleep(AGENT_POLL_INTERVAL)
                time.sleep(2.0)
                exitcode = None
                try:
                    exitcode = int(qga.download('/task/output/exitcode').decode().strip())
                except (QgaError, ValueError):
                    pass
                summary['agent_wall_seconds'] = round(time.monotonic() - agent_started, 1)
                summary['agent_exitcode'] = exitcode
                summary['end_reason'] = _classify(agent_exited, exitcode, time_exceeded, token_exceeded)
                summary['retrieved'] = _collect_output(qga, out_dir)
            finally:
                summary['stop_level'] = stop_vm(run_dir)
                _collect_run_records(run_dir, out_dir)
                if not keep_vm:
                    shutil.rmtree(run_dir, ignore_errors=True)
    except (AgentRunError, HarnessError, GuestError, DeployError, VmError, QgaError, OSError) as error:
        summary['end_reason'] = END_ENV_FAILURE
        summary['error'] = str(error)
    tokens_in, tokens_out = _usage_totals(out_dir / USAGE_NAME)
    summary['tokens_input'] = tokens_in
    summary['tokens_output'] = tokens_out
    summary['tokens_used'] = tokens_in + tokens_out
    leaks = _scan_for_key(out_dir, profile.api_key)
    summary['key_leak_hits'] = leaks
    summary['finished_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    (out_dir / 'run-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
