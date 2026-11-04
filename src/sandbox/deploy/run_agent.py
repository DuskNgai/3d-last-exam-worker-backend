"""
Run an agent task inside a gateway-mode VM and retrieve its logs.

Flow: create an overlay VM, start the API relay on the host, deploy the software
bundles, install the selected harness, run the agent non-interactively,
then retrieve every artifact it produced. The model key never enters the guest;
completion is detected by polling the QGA exec session and cross-checked with an
exitcode file the wrapper writes inside the guest.
"""

from contextlib import ExitStack
import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import sys
import time
from typing import Callable, Final

from . import api_relay
from ..qga import QgaClient, QgaError
from .agent_config import AgentConfigurationError, load_agent_config
from .credentials import CredentialsError, find_profile, load_keys
from .deploy import BundleConfig, DeployError, install_bundles, parse_spec, upload_tree
from .guest import GuestError, write_file
from .harness import distribution_dir, entry_path, get_harness, GUESTFWD_BASE_URL, HarnessError, install_script
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
END_CANCELLED: Final = 'cancelled'

SUMMARY_FIELDS: Final = (
    'run_dir', 'task_dir', 'started_at', 'finished_at',
    'gateway_status', 'deploy', 'agent_pid', 'agent_wall_seconds', 'agent_exitcode',
    'retrieved', 'agent_logs', 'stop_level',
    'tokens_input', 'tokens_output', 'tokens_cached_input', 'tokens_used',
    'end_reason', 'error',
)


class AgentRunError(RuntimeError):
    """The run could not reach or finish the agent stage."""


class AgentRunCancelled(AgentRunError):
    """The Master requested cancellation while the VM was running."""


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


def _signal_vm(run_dir: Path, signal_number: int) -> None:
    """Send one suspension signal to the QEMU process of a run, if it is alive."""
    status = vm_status(run_dir)
    pid = status['pid']
    if not isinstance(pid, int):
        return
    try:
        os.kill(pid, signal_number)
    except ProcessLookupError:
        return


def _hold_while_paused(run_dir: Path, pause_requested: Callable[[], bool] | None,
                       cancel_requested: Callable[[], bool] | None) -> bool:
    """Freeze the VM while a pause is requested; return False when cancellation wins."""
    if pause_requested is None or not pause_requested():
        return True
    _signal_vm(run_dir, signal.SIGSTOP)
    try:
        while pause_requested():
            if cancel_requested is not None and cancel_requested():
                return False
            time.sleep(1.0)
        return True
    finally:
        _signal_vm(run_dir, signal.SIGCONT)


def _budget_number(value: object, label: str) -> float:
    """Read one required non-negative finite budget value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentRunError(f'task metadata holds a missing or malformed budget.{label}: {value!r}')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise AgentRunError(f'task metadata holds a missing or malformed budget.{label}: {value!r}')
    return number


def _task_budget(task_dir: Path) -> tuple[float, int, int, int]:
    """
    Read the card's budget from task-card.json.

    The card writes `budget.maximum_minutes` in minutes, `budget.maximum_tokens` in millions of
    tokens, `budget.cpus` as a vCPU count, and `budget.memory_gb` as GiB. The VM needs seconds,
    tokens, whole vCPUs, and MiB, so the four values are converted here.

    Args:
        `task_dir` (Path): Task directory holding task-card.json.

    Returns:
        `budget` (tuple[float, int, int, int]): The agent stage's (seconds, tokens, vCPUs, MiB).

    Raises:
        AgentRunError: The task metadata is unreadable or a budget field is missing or malformed.
    """
    task_file = task_dir / 'task-card.json'
    try:
        task_data = json.loads(task_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentRunError(f'cannot read task metadata: {task_file}: {error}') from error
    budget = task_data.get('budget') if isinstance(task_data, dict) else None
    if not isinstance(budget, dict):
        raise AgentRunError(f'task metadata must contain a budget object: {task_file}')
    minutes = _budget_number(budget.get('maximum_minutes'), 'maximum_minutes')
    tokens = _budget_number(budget.get('maximum_tokens'), 'maximum_tokens')
    cpus = _budget_number(budget.get('cpus'), 'cpus')
    memory_gb = _budget_number(budget.get('memory_gb'), 'memory_gb')
    return (
        minutes * 60.0,
        round(tokens * TOKENS_PER_BUDGET_UNIT),
        math.ceil(cpus),
        math.ceil(memory_gb * 1024),
    )


def _usage_totals(usage_path: Path) -> tuple[int, int, int]:
    """
    Sum the token usage records a relay wrote for one run.

    Args:
        `usage_path` (Path): usage.jsonl the relay appended to; missing before the first response.

    Returns:
        `totals` (tuple[int, int, int]): Input, output, and cached input tokens over every response.
    """
    if not usage_path.is_file():
        return 0, 0, 0
    tokens_in = tokens_out = tokens_cached_input = 0
    for line in usage_path.read_text(encoding='utf-8').splitlines():
        record = json.loads(line)
        tokens_in += int(record['input_tokens'])
        tokens_out += int(record['output_tokens'])
        tokens_cached_input += int(record.get('cached_input_tokens', 0))
    return tokens_in, tokens_out, tokens_cached_input


def _write_deepseek_harness_usage(log_path: Path, usage_path: Path) -> None:
    """
    Convert DeepSeek Harness status usage events to the shared usage record format.

    DeepSeek Harness reports one usage object on each `status`/`step_end` event. Its input count
    excludes cached input, while the shared record stores total input and the cached portion separately.
    Existing Relay records take precedence so a request is never counted twice.

    Args:
        `log_path` (Path): Downloaded DeepSeek Harness standard output log.
        `usage_path` (Path): Worker usage record file to create when Relay produced no records.
    """
    if usage_path.is_file() and usage_path.read_text(encoding='utf-8').strip():
        return
    if not log_path.is_file():
        return

    records: list[dict[str, int | str]] = []
    for line in log_path.read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get('type') != 'status' or event.get('phase') != 'step_end':
            continue
        usage = event.get('usage')
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get('inputTokens')
        output_tokens = usage.get('outputTokens')
        cached_input_tokens = usage.get('cacheReadTokens', 0)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (input_tokens, output_tokens, cached_input_tokens)
        ):
            continue
        records.append({
            'path': 'deepseek-harness',
            'status': 200,
            'input_tokens': input_tokens + cached_input_tokens,
            'output_tokens': output_tokens,
            'cached_input_tokens': cached_input_tokens,
        })
    if not records:
        return
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(
        ''.join(json.dumps(record, separators=(',', ':')) + '\n' for record in records),
        encoding='utf-8',
    )


def _collect_run_records(run_dir: Path, out_dir: Path) -> None:
    """Copy the relay's request log and usage records next to the run summary, when the relay wrote them."""
    for name in (RELAY_LOG_NAME, USAGE_NAME):
        source = run_dir / name
        if source.is_file():
            shutil.copyfile(source, out_dir / name)


def _collect_output(qga: QgaClient, out_dir: Path) -> list[str]:
    """
    Download harness files and task files written to the workspace output directory.

    Args:
        `qga` (QgaClient): Guest agent client used to list and download the files.
        `out_dir` (Path): Host directory that receives the files under output/ and the
            output-list.json sidecar.

    Returns:
        `retrieved` (list[str]): Guest paths that were retrieved.
    """
    guest_directories = ('/task/output', '/task/work/output')
    (out_dir / 'output').mkdir(parents=True, exist_ok=True)
    guest_files: dict[str, str] = {}
    for guest_directory in guest_directories:
        listing = qga.run(
            [
                '/bin/bash',
                '-c',
                f'cd {shlex.quote(guest_directory)} 2>/dev/null && find . -type f -print0 | sort -z',
            ],
            timeout=60.0,
        )
        for relative in listing.stdout.decode().split('\0'):
            relative = relative.removeprefix('./')
            if relative and '..' not in relative.split('/'):
                guest_files[relative] = f'{guest_directory}/{relative}'

    retrieved = []
    output_list = []
    for relative, guest_path in sorted(guest_files.items()):
        target = out_dir / 'output' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = qga.download(guest_path)
        target.write_bytes(data)
        output_list.append({
            'path': relative,
            'size_bytes': len(data),
            'sha256': sha256(data).hexdigest(),
        })
        retrieved.append(guest_path)
    (out_dir / 'output-list.json').write_text(json.dumps(output_list, indent=2) + '\n', encoding='utf-8')
    return retrieved


def _collect_agent_logs(qga: QgaClient, out_dir: Path) -> list[str]:
    """Download the Agent process standard output and standard error logs."""
    guest_paths = (
        '/task/agent-logs/agent.stdout.log',
        '/task/agent-logs/agent.stderr.log',
    )
    local_directory = out_dir / 'agent-logs'
    local_directory.mkdir(parents=True, exist_ok=True)
    retrieved = []
    for guest_path in guest_paths:
        local_path = local_directory / Path(guest_path).name
        local_path.write_bytes(qga.download(guest_path))
        retrieved.append(guest_path)
    return retrieved


def _task_tool_specs(task_dir: Path, tools_root: Path) -> list[tuple[str, str]]:
    """
    Read tool references from task-card.json and pair each with its version directory name.

    A reference is `<name>@<version>`: the card names tools by registry slug alone, and Master
    resolves each entry's current version when it builds the dispatch package. The version names
    the tool directory that gets packed, so it is also the version string the install script
    receives and asserts against. A reference with no version takes the tool's only version
    directory, since the material set holds one version per tool.

    Args:
        `task_dir` (Path): Task directory holding task-card.json.
        `tools_root` (Path): Root holding <name>/install.sh and <name>/<version>/.

    Returns:
        `specs` (list[tuple[str, str]]): One (tool name, version directory name) pair per tool, python first.

    Raises:
        AgentRunError: The task metadata is unreadable or malformed, or a tool bundle is missing.
    """
    task_file = task_dir / 'task-card.json'
    try:
        task_data = json.loads(task_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentRunError(f'cannot read task metadata: {task_file}: {error}') from error
    refs = task_data.get('tool') if isinstance(task_data, dict) else None
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
        raise AgentRunError(f'task metadata must contain a tool array of <name>@<version> references: {task_file}')

    specs = []
    for ref in refs:
        # Master resolves the version before dispatch; an empty one falls back to the tool's
        # only version directory below. `parse_spec` requires both halves, so it does not fit here.
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


RUN_ERRORS: Final = (
    AgentRunError,
    AgentConfigurationError,
    CredentialsError,
    HarnessError,
    GuestError,
    DeployError,
    VmError,
    QgaError,
    OSError,
)


class AgentRun:
    """
    One VM-backed agent run.

    `prepare` deploys the VM and task environment, `execute` starts the agent and retrieves its output,
    and `close` stops the VM, collects the run records, and writes the summary.
    """

    def __init__(
        self,
        task_dir: Path,
        out_dir: Path,
        run_dir: Path,
        agent_config: Path,
        keys: Path,
        harness_root: Path,
        base_image: Path,
        tools_root: Path,
        accel: str = 'kvm',
        vnc: str | None = None,
        keep_vm: bool = False,
        cancel_requested: Callable[[], bool] | None = None,
        pause_requested: Callable[[], bool] | None = None,
    ) -> None:
        # QEMU spawns the relay from `/` after -daemonize, so the run directory is made absolute before any path is built from it.
        self.task_dir = task_dir
        self.out_dir = out_dir
        self.run_dir = run_dir.resolve()
        self.agent_config = agent_config
        self.keys = keys
        self.harness_root = harness_root
        self.base_image = base_image
        self.tools_root = tools_root
        self.accel = accel
        self.vnc = vnc
        self.keep_vm = keep_vm
        self.cancel_requested = cancel_requested
        self.pause_requested = pause_requested
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.summary: dict[str, object] = {
            'run_dir': str(self.run_dir),
            'task_dir': str(task_dir),
            'started_at': datetime.datetime.now().isoformat(timespec='seconds'),
            'end_reason': None,
            'stop_level': None
        }
        self._provider: str | None = None
        self._qga: QgaClient | None = None
        self._time_budget = 0.0
        self._token_budget = 0
        self._usage_path = self.run_dir / USAGE_NAME
        self._stack = ExitStack()
        self._locked = False
        self._closed = False

    def prepare(self) -> str | None:
        """
        Deploy the VM and task environment.

        Returns:
            `failure` (str | None): The failure reason, or None when the environment is ready.

        Raises:
            Exception: An unexpected deployment error; the run directory is cleaned up first.
        """
        try:
            self._prepare()
        except RUN_ERRORS as error:
            return self._fail(error)
        except Exception:
            self.close()
            raise
        return None

    def execute(self) -> str | None:
        """
        Run the agent in the prepared VM.

        Returns:
            `failure` (str | None): The failure reason, or None when the agent completed.

        Raises:
            Exception: An unexpected execution error; the run summary is still written.
        """
        try:
            self._execute()
        except RUN_ERRORS as error:
            return self._fail(error)
        finally:
            self.close()
        return None

    def close(self) -> None:
        """Stop the VM, collect the run records, and write the summary once."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._locked:
                self.summary['stop_level'] = stop_vm(self.run_dir)
                _collect_run_records(self.run_dir, self.out_dir)
        finally:
            try:
                self._stack.close()
            finally:
                if self._locked and not self.keep_vm:
                    shutil.rmtree(self.run_dir, ignore_errors=True)
                self._write_summary()

    def _prepare(self) -> None:
        config = load_agent_config(self.agent_config.resolve())
        find_profile(load_keys(self.keys.resolve()), config.provider)
        self._provider = config.provider
        harness_name, harness_version = parse_spec(config.harness)
        adapter = get_harness(harness_name)
        harness_entry = entry_path(harness_name)
        harness_dir = distribution_dir(self.harness_root, harness_name, harness_version)
        harness_script = install_script(self.harness_root)
        tools_root = self.tools_root.resolve()
        tool_specs = _task_tool_specs(self.task_dir, tools_root)
        self._time_budget, self._token_budget, cpus, memory_mb = _task_budget(self.task_dir)
        # QEMU runs this through /bin/sh, so every component is quoted: a checkout or key path containing a space
        # would otherwise split the command and leave the relay unable to start.
        relay_command = (
            f'{shlex.quote(sys.executable)} {shlex.quote(str(Path(api_relay.__file__).resolve()))} '
            f'--stdio --keys {shlex.quote(str(self.keys.resolve()))} --provider {shlex.quote(config.provider)} '
            f'--log {shlex.quote(str(self.run_dir / RELAY_LOG_NAME))} --usage {shlex.quote(str(self.run_dir / USAGE_NAME))}'
        )

        # Only the run holding the lock may touch the run directory: a second run pointed at the same
        # directory is refused here, before anything of the first one is stopped or removed.
        self._stack.enter_context(run_lock(self.run_dir))
        self._locked = True
        self._check_cancelled()
        create_overlay(self.run_dir, self.base_image)
        start_vm(self.run_dir, accel=self.accel, cpus=cpus, memory_mb=memory_mb, network='gateway', vnc=self.vnc,
                 relay_command=relay_command)
        wait_ready(self.run_dir, cancel_requested=self.cancel_requested)
        self._check_cancelled()
        if not _hold_while_paused(self.run_dir, self.pause_requested, self.cancel_requested):
            raise AgentRunCancelled('Stopped by Master')
        qga = QgaClient(qga_socket_path(self.run_dir))
        self._qga = qga
        self.summary['gateway_status'] = _check_gateway(qga)
        if not _hold_while_paused(self.run_dir, self.pause_requested, self.cancel_requested):
            raise AgentRunCancelled('Stopped by Master')

        bundles = [
            BundleConfig(
                name=name, path=(tools_root / name).resolve(), script=(tools_root / name / 'install.sh').resolve(), version=version
            ) for name, version in tool_specs
        ]
        bundles.append(BundleConfig(name=harness_name, path=harness_dir, script=harness_script))
        install_bundles(self.run_dir, bundles)
        self._check_cancelled()
        if not _hold_while_paused(self.run_dir, self.pause_requested, self.cancel_requested):
            raise AgentRunCancelled('Stopped by Master')
        self.summary['deploy'] = 'ok'

        workdir = qga.run([
            '/bin/bash', '-c',
            'install -d -o agent -g agent /task/work /task/output /task/agent-logs && '
            'install -d -m 0755 /task/input && '
            'ln -sfn /task/input /task/work/input && '
            'chown -h agent:agent /task/work/input'
        ], timeout=30.0)
        if workdir.exitcode != 0:
            raise AgentRunError(f'cannot create task work, input, output, or log directories: {workdir.stderr.decode()[:300]}')
        input_guard = qga.run(
            ['/bin/bash', '-c', 'test "$(stat -c %U /task/input)" = root && test "$(stat -c %a /task/input)" = 755'],
            timeout=30.0,
        )
        if input_guard.exitcode != 0:
            raise AgentRunError('task input directory must be root-owned with mode 755')
        instruction = (self.task_dir / 'instruction.md').resolve()
        qga.upload('/task/instruction.md', instruction.read_bytes())
        if (self.task_dir / 'input').is_dir():
            upload_tree(qga, self.task_dir / 'input', '/task/input', label='task input')
        adapter.configure(qga, {
            'MODEL': config.model,
            'EFFORT': config.effort
        }, harness_entry)
        self._check_cancelled()
        write_file(qga, '/task/run-agent.sh', adapter.run_script(), mode='755', owner='agent:agent')

    def _execute(self) -> None:
        qga = self._qga
        if qga is None:
            raise AgentRunError('agent run was not prepared')
        agent_started = time.monotonic()
        pid = qga.exec_start(['/usr/sbin/runuser', '-u', 'agent', '--', '/bin/bash', '/task/run-agent.sh'])
        self.summary['agent_pid'] = pid
        time_exceeded = False
        token_exceeded = False
        agent_exited = False
        cancelled = False
        paused_total = 0.0
        pause_started: float | None = None
        next_poll = time.monotonic()
        while True:
            if self.cancel_requested is not None and self.cancel_requested():
                if pause_started is not None:
                    _signal_vm(self.run_dir, signal.SIGCONT)
                    pause_started = None
                qga.run(['/bin/bash', '-c', 'pkill -KILL -u agent || true'], timeout=30.0)
                cancelled = True
                break
            if self.pause_requested is not None and self.pause_requested():
                if pause_started is None:
                    pause_started = time.monotonic()
                    _signal_vm(self.run_dir, signal.SIGSTOP)
                time.sleep(1.0)
                continue
            if pause_started is not None:
                paused_total += time.monotonic() - pause_started
                pause_started = None
                _signal_vm(self.run_dir, signal.SIGCONT)
            if time.monotonic() >= next_poll:
                status = qga.exec_status(pid)
                if status is not None:
                    agent_exited = True
                    break
                if time.monotonic() - agent_started - paused_total >= self._time_budget:
                    time_exceeded = True
                    # Kill by owner: the harness processes are `claude`, `codex`, `qwen`, and so on, which match
                    # neither the adapter name nor the `<name>@<version>` spec, and the wrapper runs as the agent user.
                    qga.run(['/bin/bash', '-c', 'pkill -KILL -u agent || true'], timeout=30.0)
                    break
                tokens_in, tokens_out, _ = _usage_totals(self._usage_path)
                if tokens_in + tokens_out >= self._token_budget:
                    token_exceeded = True
                    qga.run(['/bin/bash', '-c', 'pkill -KILL -u agent || true'], timeout=30.0)
                    break
                if vm_status(self.run_dir)['pid'] is None:
                    raise AgentRunError('QEMU exited during the agent stage')
                next_poll = time.monotonic() + AGENT_POLL_INTERVAL
            time.sleep(1.0)
        if pause_started is not None:
            paused_total += time.monotonic() - pause_started
            _signal_vm(self.run_dir, signal.SIGCONT)
        time.sleep(2.0)
        exitcode = None
        try:
            exitcode = int(qga.download('/task/output/exitcode').decode().strip())
        except (QgaError, ValueError):
            pass
        self.summary['agent_wall_seconds'] = round(time.monotonic() - agent_started - paused_total, 1)
        self.summary['agent_exitcode'] = exitcode
        self.summary['end_reason'] = END_CANCELLED if cancelled else _classify(agent_exited, exitcode, time_exceeded, token_exceeded)
        self.summary['retrieved'] = _collect_output(qga, self.out_dir)
        self.summary['agent_logs'] = _collect_agent_logs(qga, self.out_dir)

    def _check_cancelled(self) -> None:
        if self.cancel_requested is not None and self.cancel_requested():
            raise AgentRunCancelled('Stopped by Master')

    def _fail(self, error: Exception) -> str:
        self.summary['end_reason'] = END_CANCELLED if self.cancel_requested is not None and self.cancel_requested() else END_ENV_FAILURE
        self.summary['error'] = str(error)
        self.close()
        return str(error) or str(self.summary['end_reason'])

    def _write_summary(self) -> None:
        if self._provider == 'deepseek':
            _write_deepseek_harness_usage(
                self.out_dir / 'agent-logs' / 'agent.stdout.log',
                self.out_dir / USAGE_NAME,
            )
        tokens_in, tokens_out, tokens_cached_input = _usage_totals(self.out_dir / USAGE_NAME)
        self.summary['tokens_input'] = tokens_in
        self.summary['tokens_output'] = tokens_out
        self.summary['tokens_cached_input'] = tokens_cached_input
        self.summary['tokens_used'] = tokens_in + tokens_out
        if self.summary.get('end_reason') is None:
            cancelled = self.cancel_requested is not None and self.cancel_requested()
            self.summary['end_reason'] = END_CANCELLED if cancelled else END_ENV_FAILURE
            if not cancelled and 'error' not in self.summary:
                self.summary['error'] = 'agent run did not start'
        self.summary['finished_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        ordered = {name: self.summary[name] for name in SUMMARY_FIELDS if name in self.summary}
        (self.out_dir / 'run-summary.json').write_text(json.dumps(ordered, indent=2) + '\n')


def run_agent(
    task_dir: Path,
    out_dir: Path,
    run_dir: Path,
    agent_config: Path,
    keys: Path,
    harness_root: Path,
    base_image: Path,
    tools_root: Path,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
    pause_requested: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """
    Execute one agent run end to end for the debug CLI.

    Args:
        `task_dir` (Path): Directory with task-card.json, instruction.md, and an optional input/ subdirectory.
        `out_dir` (Path): Host directory that receives logs, artifacts, and the run summary.
        `run_dir` (Path): VM run directory on local storage.
        `agent_config` (Path): JSON with `provider`, `model`, `effort`, and `harness` for this agent run.
        `keys` (Path): Credential table keyed by provider; the key itself never enters the guest.
        `harness_root` (Path): Directory of harness distributions, organized as <name>/<version>/<vendor distribution file>.
        `base_image` (Path): Frozen base qcow2 the overlay is created from.
        `tools_root` (Path): Root holding <name>/install.sh and <name>/<version>/ for every tool the task names.
        `accel` (str): `kvm` or `tcg`.
        `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live; a bare
            `:5` is pinned to loopback by start_vm.
        `keep_vm` (bool): Keep the run directory after the run.
        `cancel_requested` (Callable[[], bool] | None): Whether the Master requested cancellation.
        `pause_requested` (Callable[[], bool] | None): Whether the Master requested a pause; the VM is frozen
            while it holds.

    Returns:
        `summary` (dict[str, object]): The run summary, also written to out_dir/run-summary.json. Expected run
            failures are recorded in `end_reason` and `error`.

    Raises:
        OSError: The output directory or run summary cannot be written.
    """
    run = AgentRun(
        task_dir,
        out_dir,
        run_dir,
        agent_config,
        keys,
        harness_root,
        base_image,
        tools_root,
        accel=accel,
        vnc=vnc,
        keep_vm=keep_vm,
        cancel_requested=cancel_requested,
        pause_requested=pause_requested,
    )
    failure = run.prepare()
    if failure is None:
        run.execute()
    return run.summary
