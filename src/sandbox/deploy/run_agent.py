"""Run an agent task inside a gateway-mode VM and retrieve its logs.

Flow: create an overlay VM, start the API relay on the host, deploy the software
bundles, install the selected harness, run the agent non-interactively,
then retrieve every artifact it produced. The model key never enters the guest;
completion is detected by polling the QGA exec session and cross-checked with an
exitcode file the wrapper writes inside the guest.
"""

import datetime
import io
import json
import shutil
import sys
import tarfile
import time
import uuid
from pathlib import Path
from typing import Final

from . import api_relay
from .api_relay import load_env
from .deploy import BundleConfig, DeployError, SCRIPTS_DIR, install_bundles
from .guest import GuestError, write_file
from .harness import GUESTFWD_BASE_URL, HarnessError, INSTALL_SCRIPT, distribution_dir, get_harness
from ..qga import QgaClient, QgaError
from .qemu_vm import VmError, create_overlay, qga_socket_path, start_vm, stop_vm, vm_status, wait_ready

DEFAULT_TIMEOUT: Final = 14400.0
AGENT_POLL_INTERVAL: Final = 15.0
GATEWAY_CHECK_TIMEOUT: Final = 60.0

END_COMPLETED: Final = 'completed'
END_HARNESS_ERROR: Final = 'harness_error'
END_TIMEOUT: Final = 'timeout'
END_ENV_FAILURE: Final = 'environment_failure'


class AgentRunError(RuntimeError):
    """The run could not reach or finish the agent stage."""


def _check_gateway(qga: QgaClient, attempts: int = 3) -> str:
    """Verify the guest can reach the model API through guestfwd and the relay.

    `attempts` (int): Probe retries; the first guestfwd-cmd spawn may need a moment.

    Returns:
        str: The HTTP status code seen by the guest.

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


def _classify(agent_exited: bool, exitcode: int | None, timed_out: bool) -> str:
    """Map the run outcome to an end_reason value."""
    if timed_out:
        return END_TIMEOUT
    if not agent_exited:
        return END_ENV_FAILURE
    if exitcode == 0:
        return END_COMPLETED
    return END_HARNESS_ERROR


def _parse_spec(spec: str) -> tuple[str, str]:
    """Split one `<name>@<version>` spec, used for tools and for the harness.

    Raises:
        AgentRunError: The spec is missing its name or its version.
    """
    name, separator, version = spec.partition('@')
    if not separator or not name or not version:
        raise AgentRunError(f'spec must be <name>@<version>: {spec!r}')
    return name, version


def _scan_for_key(out_dir: Path, api_key: str) -> list[str]:
    """Search retrieved files for the real API key.

    Returns:
        list[str]: Relative paths that contain the key, empty when clean.
    """
    hits = []
    for path in sorted(out_dir.rglob('*')):
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if api_key.encode() in content:
            hits.append(str(path.relative_to(out_dir)))
    return hits


def _collect_output(qga: QgaClient, out_dir: Path) -> list[str]:
    """Download everything under /task/output into out_dir.

    Returns:
        list[str]: Guest paths that were retrieved.
    """
    listing = qga.run(['/bin/bash', '-c', 'cd /task/output 2>/dev/null && find . -type f | sort'], timeout=60.0)
    retrieved = []
    for relative in listing.stdout.decode().split():
        relative = relative.removeprefix('./')
        if not relative or '..' in relative.split('/'):
            continue
        target = out_dir / 'task-output' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(qga.download(f'/task/output/{relative}'))
        retrieved.append(f'/task/output/{relative}')
    return retrieved


def run_agent(
    task_dir: Path,
    out_dir: Path,
    run_dir: Path,
    env_file: Path,
    tools_root: Path,
    tools: list[str],
    harness_root: Path,
    harness: str,
    base_image: Path,
    timeout: float = DEFAULT_TIMEOUT,
    smp: int = 4,
    memory: int = 16384,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False
) -> dict[str, object]:
    """Execute one agent run end to end.

    `task_dir` (Path): Directory with instruction.md and an optional input/ subdirectory.
    `out_dir` (Path): Host directory that receives logs, artifacts, and the run summary.
    `run_dir` (Path): VM run directory on local storage.
    `env_file` (Path): The .env with MODEL, EFFORT, BASE_URL, API_KEY.
    `tools_root` (Path): Directory of software bundles, organized as <name>/.
    `tools` (list[str]): Tool specs as `<name>@<version>`, installed in order; each resolves to
        tools_root/<name>/<version>/ and is installed by tools_root/<name>/install.sh.
    `harness_root` (Path): Directory of harness distributions, organized as <name>/<version>/<vendor archive>.
    `harness` (str): Harness spec as `<name>@<version>`; supported names are registered in
        sandbox.deploy.harness.
    `base_image` (Path): Frozen base qcow2 the overlay is created from.
    `timeout` (float): Wall-clock budget in seconds for the agent stage.
    `smp` (int): vCPU count.
    `memory` (int): RAM in MiB.
    `accel` (str): `kvm` or `tcg`.
    `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live; a bare
        `:5` is pinned to loopback by start_vm.
    `keep_vm` (bool): Keep the run directory after the run.

    Returns:
        dict[str, object]: The run summary, also written to out_dir/run-summary.json.

    Raises:
        AgentRunError: The env file is incomplete or a tools bundle is missing.
        HarnessError: The harness is unsupported or its distribution directory is invalid.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    env = load_env(env_file.resolve())
    missing = [key for key in ('MODEL', 'EFFORT', 'BASE_URL', 'API_KEY') if not env.get(key)]
    if missing:
        raise AgentRunError(f'env file missing keys: {missing}')
    harness_name, harness_version = _parse_spec(harness)
    adapter = get_harness(harness_name)
    harness_dir = distribution_dir(harness_root, harness_name, harness_version)
    tool_specs = [_parse_spec(spec) for spec in tools]
    for name, version in tool_specs:
        bundle_dir = tools_root / name / version
        if not bundle_dir.is_dir():
            raise AgentRunError(f'tools bundle not found: {bundle_dir}')
        script = tools_root / name / 'install.sh'
        if not script.is_file():
            raise AgentRunError(f'tools install script not found: {script}')
    summary: dict[str, object] = {
        'run_dir': str(run_dir),
        'task_dir': str(task_dir),
        'timeout': timeout,
        'tools': tools,
        'harness': harness_name,
        'harness_version': harness_version,
        'started_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'end_reason': None
    }
    relay_command = f'{sys.executable} {Path(api_relay.__file__).resolve()} --stdio --env-file {env_file.resolve()}'
    try:
        create_overlay(run_dir, base_image)
        start_vm(run_dir, accel=accel, smp=smp, memory=memory, network='gateway', vnc=vnc, relay_command=relay_command)
        wait_ready(run_dir)
        qga = QgaClient(qga_socket_path(run_dir))
        summary['gateway_status'] = _check_gateway(qga)

        bundles = [
            BundleConfig(
                name=name,
                path=(tools_root / name / version).resolve(),
                script=(tools_root / name / 'install.sh').resolve(),
                version=version
            ) for name, version in tool_specs
        ]
        bundles.append(BundleConfig(name=harness_name, path=harness_dir, script=SCRIPTS_DIR / INSTALL_SCRIPT))
        install_bundles(run_dir, bundles)
        summary['deploy'] = 'ok'

        workdir = qga.run(['/bin/bash', '-c', 'install -d -o agent -g agent /task/work /task/output'], timeout=30.0)
        if workdir.exitcode != 0:
            raise AgentRunError(f'cannot create /task/work and /task/output: {workdir.stderr.decode()[:300]}')
        instruction = (task_dir / 'instruction.md').resolve()
        qga.upload('/task/instruction.md', instruction.read_bytes())
        if (task_dir / 'input').is_dir():
            _guest_tar_upload_dir(qga, task_dir / 'input', '/task/input')
        adapter.configure(qga, env)
        write_file(qga, '/task/run-agent.sh', adapter.run_script(), mode='755', owner='agent:agent')

        agent_started = time.monotonic()
        pid = qga.exec_start(['/usr/sbin/runuser', '-u', 'agent', '--', '/bin/bash', '/task/run-agent.sh'])
        summary['agent_pid'] = pid
        timed_out = False
        agent_exited = False
        while True:
            status = qga.exec_status(pid)
            if status is not None:
                agent_exited = True
                break
            if time.monotonic() - agent_started > timeout:
                timed_out = True
                qga.run(['/bin/bash', '-c', f'pkill -KILL -f {harness} || true; pkill -KILL -f run-agent || true'], timeout=30.0)
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
        summary['end_reason'] = _classify(agent_exited, exitcode, timed_out)
        summary['retrieved'] = _collect_output(qga, out_dir)
    except (AgentRunError, HarnessError, GuestError, DeployError, VmError, QgaError, OSError) as error:
        summary['end_reason'] = END_ENV_FAILURE
        summary['error'] = str(error)
    finally:
        stop_level = stop_vm(run_dir)
        summary['stop_level'] = stop_level
        if not keep_vm:
            shutil.rmtree(run_dir, ignore_errors=True)
    leaks = _scan_for_key(out_dir, env['API_KEY'])
    summary['key_leak_hits'] = leaks
    summary['finished_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    (out_dir / 'run-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def _guest_tar_upload_dir(qga: QgaClient, host_dir: Path, guest_dir: str) -> None:
    """Upload a host directory tree into the guest as a tar stream."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        archive.add(host_dir, arcname='.')
    tag = uuid.uuid4().hex[: 8]
    qga.upload(f'/tmp/{tag}.tar.gz', buffer.getvalue())
    result = qga.run(['/bin/bash', '-c', f'mkdir -p {guest_dir} && tar xzf /tmp/{tag}.tar.gz -C {guest_dir} && rm -f /tmp/{tag}.tar.gz'],
                     timeout=600.0)
    if result.exitcode != 0:
        raise AgentRunError(f'guest-side extract failed for {guest_dir}: {result.stderr.decode()[:500]}')
