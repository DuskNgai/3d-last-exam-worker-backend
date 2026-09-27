"""Run an agent task inside a gateway-mode VM and retrieve its logs.

Flow: create an overlay VM, start the API relay on the host, deploy the software
bundles, upload the task and the codex harness, run the agent non-interactively,
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
from . import deploy as deploy_module
from .api_relay import load_env
from ..qga import QgaClient, QgaError
from .qemu_vm import DEFAULT_RUNS_ROOT, VmError, create_overlay, qga_socket_path, start_vm, stop_vm, vm_status, wait_ready

DEFAULT_OUTPUT_ROOT: Final = Path('artifacts/runs/agent-runs')
DEFAULT_DEPLOY_BUNDLES: Final = (
    ('python', 'artifacts/tools/python', 'install_python.sh', False),
    ('mujoco', 'artifacts/tools/mujoco', 'install_mujoco.sh', True),
)
CODEX_ARCHIVE: Final = Path('artifacts/harnesses/codex/0.156.1.tar.gz')
DEFAULT_TIMEOUT: Final = 14400.0
AGENT_POLL_INTERVAL: Final = 15.0
UPLOAD_CHUNK_BYTES: Final = 67108864
GUESTFWD_BASE_URL: Final = 'http://10.0.2.100:8443/v1'
GATEWAY_CHECK_TIMEOUT: Final = 60.0

END_COMPLETED: Final = 'completed'
END_HARNESS_ERROR: Final = 'harness_error'
END_TIMEOUT: Final = 'timeout'
END_ENV_FAILURE: Final = 'environment_failure'


class AgentRunError(RuntimeError):
    """The run could not reach or finish the agent stage."""


def _guest_tar_upload(qga: QgaClient, host_path: Path, guest_dir: str) -> None:
    """Upload a local file into the guest in chunks and move it into place.

    `qga` (QgaClient): Guest agent client.
    `host_path` (Path): Local file to send.
    `guest_dir` (str): Guest directory that receives the file under its basename.

    Raises:
        AgentRunError: The transfer or the guest-side placement failed.
    """
    result = qga.run(['/bin/bash', '-c', f'mkdir -p {guest_dir}'], timeout=30.0)
    if result.exitcode != 0:
        raise AgentRunError(f'guest dir setup failed for {guest_dir}: {result.stderr.decode()[:300]}')
    data = host_path.read_bytes()
    parts = (data[offset:offset + UPLOAD_CHUNK_BYTES] for offset in range(0, len(data), UPLOAD_CHUNK_BYTES))
    try:
        qga.upload_large(f'{guest_dir}/{host_path.name}', parts)
    except (QgaError, TimeoutError) as error:
        raise AgentRunError(f'upload failed for {host_path.name}: {error}') from error


def _write_guest_file(qga: QgaClient, remote_path: str, content: str, mode: str = '644', owner: str | None = None) -> None:
    """Write text to a guest file with mode and optional owner."""
    qga.upload(remote_path, content.encode())
    result = qga.run(['/bin/bash', '-c', f'chmod {mode} {remote_path}'], timeout=30.0)
    if result.exitcode != 0:
        raise AgentRunError(f'chmod failed for {remote_path}: {result.stderr.decode()[:300]}')
    if owner:
        result = qga.run(['/bin/bash', '-c', f'chown {owner} {remote_path}'], timeout=30.0)
        if result.exitcode != 0:
            raise AgentRunError(f'chown failed for {remote_path}: {result.stderr.decode()[:300]}')


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
        "r = urllib.request.urlopen('http://10.0.2.100:8443/v1/models', timeout=20); "
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


def _codex_config(model: str, effort: str) -> str:
    """Render the guest-side codex config that points at the guestfwd relay.

    codex ignores OPENAI_BASE_URL; a custom model provider in config.toml is the
    reliable route. `supports_websockets = false` keeps it on plain HTTPS SSE.
    """
    return f'''model = "{model}"
model_reasoning_effort = "{effort}"
model_provider = "relay"

[model_providers.relay]
name = "relay"
base_url = "{GUESTFWD_BASE_URL}"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false
'''


def _agent_script() -> str:
    """Render the guest-side agent wrapper script."""
    return '''#!/bin/bash
set -u
mkdir -p /task/output
export OPENAI_API_KEY=placeholder-managed-by-relay
export PATH=/opt/codex/bin:$PATH
cd /work
codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \\
  --disable responses_websockets --disable responses_websockets_v2 \\
  -C /work --json -o /task/output/last-message.txt "$(cat /task/instruction.md)" \\
  < /dev/null > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
echo -n $? > /task/output/exitcode
'''


def _classify(agent_exited: bool, exitcode: int | None, timed_out: bool) -> str:
    """Map the run outcome to an end_reason value."""
    if timed_out:
        return END_TIMEOUT
    if not agent_exited:
        return END_ENV_FAILURE
    if exitcode == 0:
        return END_COMPLETED
    return END_HARNESS_ERROR


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


def run_agent(task_dir: Path, out_dir: Path, run_dir: Path, env_file: Path, timeout: float = DEFAULT_TIMEOUT,
              smp: int = 4, memory: int = 16384, accel: str = 'kvm', keep_vm: bool = False) -> dict[str, object]:
    """Execute one agent run end to end.

    `task_dir` (Path): Directory with instruction.md and an optional input/ subdirectory.
    `out_dir` (Path): Host directory that receives logs, artifacts, and the run summary.
    `run_dir` (Path): VM run directory on local storage.
    `env_file` (Path): The .env with MODEL, EFFORT, BASE_URL, API_KEY.
    `timeout` (float): Wall-clock budget in seconds for the agent stage.
    `smp` (int): vCPU count.
    `memory` (int): RAM in MiB.
    `accel` (str): `kvm` or `tcg`.
    `keep_vm` (bool): Keep the run directory after the run.

    Returns:
        dict[str, object]: The run summary, also written to out_dir/run-summary.json.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    env = load_env(env_file.resolve())
    missing = [key for key in ('MODEL', 'EFFORT', 'BASE_URL', 'API_KEY') if not env.get(key)]
    if missing:
        raise AgentRunError(f'env file missing keys: {missing}')
    summary: dict[str, object] = {'run_dir': str(run_dir), 'task_dir': str(task_dir), 'timeout': timeout,
                     'started_at': datetime.datetime.now().isoformat(timespec='seconds'), 'end_reason': None}
    relay_command = f'{sys.executable} {Path(api_relay.__file__).resolve()} --stdio --env-file {env_file.resolve()}'
    try:
        create_overlay(run_dir)
        start_vm(run_dir, accel=accel, smp=smp, memory=memory, network='gateway', relay_command=relay_command)
        wait_ready(run_dir)
        qga = QgaClient(qga_socket_path(run_dir))
        summary['gateway_status'] = _check_gateway(qga)

        deploy_config = {'run_dir': str(run_dir),
                         'bundles': [{'name': name, 'path': str(Path(path).resolve()), 'script': script, 'checks': checks}
                                     for name, path, script, checks in DEFAULT_DEPLOY_BUNDLES],
                         'artifacts': []}
        config_path = run_dir / 'deploy-config.json'
        config_path.write_text(json.dumps(deploy_config, indent=2) + '\n')
        deploy_module.deploy(config_path, check_only=False)
        summary['deploy'] = 'ok'

        _guest_tar_upload(qga, CODEX_ARCHIVE.resolve(), '/opt/codex-dist')
        result = qga.run(['/bin/bash', '-c',
                          'mkdir -p /opt/codex && tar xzf /opt/codex-dist/0.156.1.tar.gz -C /opt/codex && '
                          'rm -rf /opt/codex-dist && /opt/codex/bin/codex --version'], timeout=600.0)
        if result.exitcode != 0:
            raise AgentRunError(f'codex extract failed: {result.stderr.decode()[:300]}')
        summary['codex_version'] = result.stdout.decode().strip()

        instruction = (task_dir / 'instruction.md').resolve()
        qga.upload('/task/instruction.md', instruction.read_bytes())
        if (task_dir / 'input').is_dir():
            _guest_tar_upload_dir(qga, task_dir / 'input', '/task/input')
        result = qga.run(['/bin/bash', '-c', 'mkdir -p /home/agent/.codex && chown agent:agent /home/agent/.codex'],
                         timeout=30.0)
        if result.exitcode != 0:
            raise AgentRunError(f'codex home setup failed: {result.stderr.decode()[:300]}')
        _write_guest_file(qga, '/home/agent/.codex/config.toml', _codex_config(env['MODEL'], env['EFFORT']),
                          owner='agent:agent')
        _write_guest_file(qga, '/opt/benchmark/run-agent.sh', _agent_script(), mode='755', owner='agent:agent')

        agent_started = time.monotonic()
        pid = qga.exec_start(['/usr/sbin/runuser', '-u', 'agent', '--', '/bin/bash', '/opt/benchmark/run-agent.sh'])
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
                qga.run(['/bin/bash', '-c', 'pkill -KILL -f codex || true; pkill -KILL -f run-agent || true'], timeout=30.0)
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
    except (AgentRunError, deploy_module.DeployError, VmError, QgaError, OSError) as error:
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
    tag = uuid.uuid4().hex[:8]
    qga.upload(f'/tmp/{tag}.tar.gz', buffer.getvalue())
    result = qga.run(['/bin/bash', '-c', f'mkdir -p {guest_dir} && tar xzf /tmp/{tag}.tar.gz -C {guest_dir} && rm -f /tmp/{tag}.tar.gz'],
                     timeout=600.0)
    if result.exitcode != 0:
        raise AgentRunError(f'guest-side extract failed for {guest_dir}: {result.stderr.decode()[:500]}')
