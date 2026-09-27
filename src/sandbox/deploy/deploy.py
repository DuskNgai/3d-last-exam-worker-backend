"""
Deploy software bundles into a running QEMU/KVM guest over the guest agent.

The config JSON uses this schema, with relative paths resolved against the
config file location:

    {
      "run_dir": "runs/demo",
      "bundles": [
        {"name": "python", "path": "bundles/python", "script": "install_python.sh"},
        {"name": "mujoco", "path": "bundles/mujoco", "script": "install_mujoco.sh", "checks": true}
      ],
      "artifacts": ["/work/software-check.json", "/work/render-check.json", "/work/render-check.png"]
    }

run_dir is the host run directory of a started VM and provides the QGA socket.
Bundles install in listed order. Each bundle directory is packed into a tar.gz
on the host, uploaded in 64 MiB parts, and extracted in the guest under
/opt/benchmark/bundles/<name>. The install script comes from the scripts
directory next to this file and runs in the guest as root with the extracted
bundle path as its only argument. With "checks": true the check scripts from
the scripts directory are added to the extracted bundle right before the
install script runs, since install_mujoco.sh copies check_*.py out of the
bundle into /opt/benchmark/checks. Artifacts are guest files downloaded into
deploy-artifacts/<timestamp> next to the config file after the install passes.

--check-only skips upload and installation, reruns every check_*.py found in
/opt/benchmark/checks as the agent user, and then downloads the artifacts.
"""

from collections.abc import Iterator
from dataclasses import dataclass
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
import time
from typing import Final

from .qemu_vm import qga_socket_path
from ..qga import QgaClient, QgaError

CHUNK_BYTES: Final = 64 * 1024 * 1024
GUEST_ROOT: Final = '/opt/benchmark'
GUEST_BUNDLES: Final = '/opt/benchmark/bundles'
GUEST_CHECKS: Final = '/opt/benchmark/checks'
PYTHON_BIN: Final = '/task/software/python/bin/python'
SCRIPTS_DIR: Final = Path(__file__).resolve().parent / 'scripts'
ARTIFACTS_DIR_NAME: Final = 'deploy-artifacts'
CHECK_SCRIPTS: Final = ('check_software.py', 'check_viewer.py', 'check_render.py')
QUICK_TIMEOUT: Final = 30.0
MERGE_TIMEOUT: Final = 600.0
CHECK_TIMEOUT: Final = 300.0
INSTALL_TIMEOUT: Final = 1800.0
SUMMARY_LIMIT: Final = 2000
BUNDLE_NAME_PATTERN: Final = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
CHECK_NAME_PATTERN: Final = re.compile(r'check_[A-Za-z0-9_]+\.py')
CHECK_ENVS: Final = {
    'check_render.py': {
        'MUJOCO_GL': 'egl',
        'EGL_PLATFORM': 'surfaceless'
    },
    'check_viewer.py': {
        'DISPLAY': ':0'
    },
}


class DeployError(RuntimeError):
    """The config is invalid or a deployment step failed."""


@dataclass
class BundleConfig:
    """One validated bundle entry; installation follows config list order."""

    name: str
    path: Path
    script: str
    checks: bool = False


@dataclass
class DeployConfig:
    """Validated deployment configuration."""

    run_dir: Path
    bundles: list[BundleConfig]
    artifacts: list[str]


def _summarize(data: bytes) -> str:
    """Return the tail of captured guest output as text for error messages."""
    text = data.decode(errors='replace').strip()
    if len(text) > SUMMARY_LIMIT:
        text = f'...{text[-SUMMARY_LIMIT:]}'
    return text or '<no output>'


def _parse_bundle(item: object, index: int, config_path: Path, check_only: bool) -> BundleConfig:
    """
    Validate one bundle entry and resolve its host directory.

    Raises:
        DeployError: The entry is malformed or its files are missing.
    """
    if not isinstance(item, dict):
        raise DeployError(f'bundle entry {index} must be a JSON object')
    name = item.get('name')
    if not isinstance(name, str) or not BUNDLE_NAME_PATTERN.fullmatch(name):
        raise DeployError(f'bundle entry {index} has an invalid "name"; use letters, digits, dot, dash, underscore')
    path = item.get('path')
    if not isinstance(path, str) or not path:
        raise DeployError(f'bundle {name}: "path" must be a non-empty string')
    script = item.get('script')
    if not isinstance(script, str) or Path(script).name != script:
        raise DeployError(f'bundle {name}: "script" must be a plain file name such as install_python.sh')
    checks = item.get('checks', False)
    if not isinstance(checks, bool):
        raise DeployError(f'bundle {name}: "checks" must be true or false')
    bundle_dir = (config_path.parent / path).resolve()
    if not check_only:
        if not bundle_dir.is_dir():
            raise DeployError(f'bundle {name}: directory not found: {bundle_dir}')
        if not (SCRIPTS_DIR / script).is_file():
            raise DeployError(f'bundle {name}: install script not found: {SCRIPTS_DIR / script}')
    return BundleConfig(name=name, path=bundle_dir, script=script, checks=checks)


def _load_config(config_path: Path, check_only: bool) -> DeployConfig:
    """
    Parse and validate the deployment config.

    Raises:
        DeployError: The JSON is malformed or a required field is missing.
    """
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError) as error:
        raise DeployError(f'cannot read config {config_path}: {error}') from error
    if not isinstance(config, dict):
        raise DeployError('config root must be a JSON object')
    run_dir = config.get('run_dir')
    if not isinstance(run_dir, str) or not run_dir:
        raise DeployError('config requires a non-empty "run_dir" string')
    raw_bundles = config.get('bundles', [])
    if not isinstance(raw_bundles, list):
        raise DeployError('"bundles" must be a list')
    bundles = [_parse_bundle(item, index, config_path, check_only) for index, item in enumerate(raw_bundles, start=1)]
    artifacts = config.get('artifacts', [])
    if not isinstance(artifacts, list) or not all(isinstance(item, str) and item for item in artifacts):
        raise DeployError('"artifacts" must be a list of guest file paths')
    return DeployConfig(run_dir=(config_path.parent / run_dir).resolve(), bundles=bundles, artifacts=artifacts)


def _remove_guest_parts(qga: QgaClient, name: str) -> None:
    """Best-effort removal of a leftover bundle tarball in the guest."""
    try:
        qga.run(['/bin/bash', '-c', f'rm -f /tmp/{name}.tar.gz'], timeout=QUICK_TIMEOUT)
    except (QgaError, TimeoutError, OSError):
        pass


def _stream_parts(stream: io.BufferedIOBase, name: str) -> Iterator[bytes]:
    """Yield a tar stream in CHUNK_BYTES pieces with per-part progress output."""
    index = 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            return
        yield chunk
        print(f'bundle {name}: uploaded part {index:03d}, {len(chunk)} bytes', flush=True)
        index += 1


def _upload_bundle(qga: QgaClient, name: str, bundle_dir: Path) -> None:
    """
    Pack a bundle directory, upload it in parts, and extract it in the guest.

    Raises:
        DeployError: Packing, upload, or guest-side extraction failed.
    """
    print(f'bundle {name}: packing {bundle_dir}', flush=True)
    try:
        with tempfile.TemporaryFile(prefix=f'deploy-{name}-', suffix='.tar.gz') as stream:
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                archive.add(bundle_dir, arcname='.')
            stream.seek(0)
            qga.upload_large(f'/tmp/{name}.tar.gz', _stream_parts(stream, name), timeout=MERGE_TIMEOUT)
        extract = f'mkdir -p {GUEST_BUNDLES}/{name} && tar -xzf /tmp/{name}.tar.gz -C {GUEST_BUNDLES}/{name} && rm -f /tmp/{name}.tar.gz'
        result = qga.run(['/bin/bash', '-c', extract], timeout=MERGE_TIMEOUT)
        if result.exitcode != 0:
            raise DeployError(f'bundle {name}: guest extraction failed: {_summarize(result.stderr)}')
    except (OSError, tarfile.TarError) as error:
        raise DeployError(f'bundle {name}: packing failed: {error}') from error
    except (QgaError, TimeoutError) as error:
        raise DeployError(f'bundle {name}: upload failed: {error}') from error
    finally:
        _remove_guest_parts(qga, name)
    print(f'bundle {name}: extracted to {GUEST_BUNDLES}/{name}', flush=True)


def _add_check_scripts(qga: QgaClient, name: str) -> None:
    """Copy the check scripts into an extracted bundle for the install script to pick up."""
    for script in CHECK_SCRIPTS:
        path = SCRIPTS_DIR / script
        if path.is_file():
            qga.upload(f'{GUEST_BUNDLES}/{name}/{script}', path.read_bytes())
            print(f'bundle {name}: added {script}', flush=True)


def _run_install_script(qga: QgaClient, name: str, script: str) -> None:
    """
    Upload the install script and run it in the guest as root.

    Raises:
        DeployError: The script exited nonzero or did not finish in time.
    """
    qga.upload(f'{GUEST_ROOT}/{script}', (SCRIPTS_DIR / script).read_bytes())
    print(f'bundle {name}: running {script}', flush=True)
    try:
        result = qga.run(['/bin/bash', f'{GUEST_ROOT}/{script}', f'{GUEST_BUNDLES}/{name}'], timeout=INSTALL_TIMEOUT)
    except TimeoutError as error:
        message = f'bundle {name}: {script} did not finish within {INSTALL_TIMEOUT}s; guest process may still be running'
        raise DeployError(message) from error
    sys.stdout.buffer.write(result.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(result.stderr)
    sys.stderr.buffer.flush()
    if result.exitcode != 0:
        raise DeployError(f'bundle {name}: {script} exited with code {result.exitcode}: {_summarize(result.stderr or result.stdout)}')


def _run_checks(qga: QgaClient) -> None:
    """
    Rerun every check script already installed in the guest as the agent user.

    Raises:
        DeployError: No checks are installed or one exited nonzero.
    """
    listing = qga.run(['/bin/ls', '-1', GUEST_CHECKS], timeout=QUICK_TIMEOUT)
    if listing.exitcode != 0:
        raise DeployError(f'cannot list {GUEST_CHECKS}: {_summarize(listing.stderr)}')
    names = sorted(name for name in listing.stdout.decode(errors='replace').split() if CHECK_NAME_PATTERN.fullmatch(name))
    if not names:
        raise DeployError(f'no check scripts in {GUEST_CHECKS}; run a full deployment first')
    for name in names:
        print(f'check: {name}', flush=True)
        env = ' '.join(f'{key}={value}' for key, value in CHECK_ENVS.get(name, {}).items())
        line = f'runuser -u agent -- {PYTHON_BIN} {GUEST_CHECKS}/{name}'
        if env:
            line = f'runuser -u agent -- env {env} {PYTHON_BIN} {GUEST_CHECKS}/{name}'
        try:
            result = qga.run(['/bin/bash', '-c', line], timeout=CHECK_TIMEOUT)
        except TimeoutError as error:
            raise DeployError(f'check {name} did not finish within {CHECK_TIMEOUT}s; guest process may still be running') from error
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
        if result.exitcode != 0:
            raise DeployError(f'check {name} exited with code {result.exitcode}: {_summarize(result.stderr or result.stdout)}')


def _fetch_artifacts(qga: QgaClient, artifacts: list[str], output_dir: Path) -> None:
    """
    Download guest files into a timestamped directory next to the config.

    `artifacts` (list[str]): Guest file paths, mirrored below output_dir.
    `output_dir` (Path): Host directory created for this run.

    Raises:
        DeployError: A guest path is unsafe or cannot be downloaded.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for guest_path in artifacts:
        parts = [part for part in PurePosixPath(guest_path).parts if part != '/']
        if not parts or '..' in parts:
            raise DeployError(f'unsafe artifact path: {guest_path}')
        target = output_dir.joinpath(*parts)
        try:
            data = qga.download(guest_path)
        except QgaError as error:
            raise DeployError(f'cannot download artifact {guest_path}: {error}') from error
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f'artifact: {guest_path} -> {target}, {len(data)} bytes', flush=True)


def deploy(config_path: Path, check_only: bool) -> None:
    """
    Run the full deployment or the check-only pass.

    `config_path` (Path): Deployment config JSON, resolved and readable.
    `check_only` (bool): Skip upload and installation, rerun guest checks only.

    Raises:
        DeployError: The config is invalid or a guest step failed.
        QgaError: The guest agent connection failed.
        TimeoutError: A short guest operation hung.
    """
    config = _load_config(config_path, check_only)
    socket_path = qga_socket_path(config.run_dir)
    if not socket_path.exists():
        raise DeployError(f'QGA socket not found: {socket_path}; start the VM first')
    qga = QgaClient(socket_path)
    print(f'connecting to guest agent: {socket_path}', flush=True)
    qga.ping()
    print('guest agent ready', flush=True)
    if check_only:
        _run_checks(qga)
    else:
        if not config.bundles:
            raise DeployError('config lists no bundles; nothing to install')
        for position, bundle in enumerate(config.bundles, start=1):
            print(f'== bundle {position}/{len(config.bundles)}: {bundle.name}', flush=True)
            _upload_bundle(qga, bundle.name, bundle.path)
            if bundle.checks:
                _add_check_scripts(qga, bundle.name)
            _run_install_script(qga, bundle.name, bundle.script)
    if config.artifacts:
        output_dir = config_path.parent / ARTIFACTS_DIR_NAME / time.strftime('%Y%m%d-%H%M%S')
        _fetch_artifacts(qga, config.artifacts, output_dir)
        print(f'artifacts saved under {output_dir}', flush=True)
    print('deployment finished', flush=True)
