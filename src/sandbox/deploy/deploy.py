"""
Deploy software bundles into a running QEMU/KVM guest over the guest agent.

A bundle is a host directory packed into a tar.gz, uploaded in 64 MiB parts,
and extracted in the guest under /task/bundles/<name>. Each bundle also carries
its own install script, uploaded into that directory as install.sh. The script
runs in the guest as root from inside the bundle, with the bundle directory as
its first argument and the bundle version, when it has one, as the second.
"""

from collections.abc import Iterator
from dataclasses import dataclass
import io
from pathlib import Path
import sys
import tarfile
import tempfile
from typing import Final

from .qemu_vm import qga_socket_path
from ..qga import QgaClient, QgaError

CHUNK_BYTES: Final = 64 * 1024 * 1024
GUEST_ROOT: Final = '/task'
GUEST_BUNDLES: Final = '/task/bundles'
SCRIPTS_DIR: Final = Path(__file__).resolve().parent / 'scripts'
INSTALL_NAME: Final = 'install.sh'
QUICK_TIMEOUT: Final = 30.0
MERGE_TIMEOUT: Final = 600.0
INSTALL_TIMEOUT: Final = 1800.0
SUMMARY_LIMIT: Final = 2000


class DeployError(RuntimeError):
    """A deployment step failed."""


@dataclass
class BundleConfig:
    """One bundle entry; installation follows list order.

    `path` is the material directory and `script` is uploaded into it as
    install.sh. `version` is passed to the install script as its second
    argument when it is set.
    """

    name: str
    path: Path
    script: Path
    version: str | None = None


def _summarize(data: bytes) -> str:
    """Return the tail of captured guest output as text for error messages."""
    text = data.decode(errors='replace').strip()
    if len(text) > SUMMARY_LIMIT:
        text = f'...{text[-SUMMARY_LIMIT:]}'
    return text or '<no output>'


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


def _run_install_script(qga: QgaClient, name: str, script: Path, version: str | None) -> None:
    """
    Upload the bundle's install script into the bundle and run it there as root.

    Raises:
        DeployError: The script exited nonzero or did not finish in time.
    """
    guest_script = f'{GUEST_BUNDLES}/{name}/{INSTALL_NAME}'
    qga.upload(guest_script, script.read_bytes())
    print(f'bundle {name}: running {INSTALL_NAME}', flush=True)
    command = ['/bin/bash', guest_script, f'{GUEST_BUNDLES}/{name}']
    if version is not None:
        command.append(version)
    try:
        result = qga.run(command, timeout=INSTALL_TIMEOUT)
    except TimeoutError as error:
        message = f'bundle {name}: {INSTALL_NAME} did not finish within {INSTALL_TIMEOUT}s; guest process may still be running'
        raise DeployError(message) from error
    sys.stdout.buffer.write(result.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(result.stderr)
    sys.stderr.buffer.flush()
    if result.exitcode != 0:
        raise DeployError(
            f'bundle {name}: {INSTALL_NAME} exited with code {result.exitcode}: '
            f'{_summarize(result.stderr or result.stdout)}'
        )


def _connect(run_dir: Path) -> QgaClient:
    """
    Connect to the guest agent of the VM in run_dir.

    Raises:
        DeployError: The QGA socket is missing.
    """
    socket_path = qga_socket_path(run_dir)
    if not socket_path.exists():
        raise DeployError(f'QGA socket not found: {socket_path}; start the VM first')
    qga = QgaClient(socket_path)
    print(f'connecting to guest agent: {socket_path}', flush=True)
    qga.ping()
    print('guest agent ready', flush=True)
    return qga


def _install_all(qga: QgaClient, bundles: list[BundleConfig]) -> None:
    """
    Upload and install bundles in list order.

    Raises:
        DeployError: The bundle list is empty or a deployment step failed.
    """
    if not bundles:
        raise DeployError('no bundles to install')
    for position, bundle in enumerate(bundles, start=1):
        print(f'== bundle {position}/{len(bundles)}: {bundle.name}', flush=True)
        _upload_bundle(qga, bundle.name, bundle.path)
        _run_install_script(qga, bundle.name, bundle.script, bundle.version)


def install_bundles(run_dir: Path, bundles: list[BundleConfig]) -> None:
    """Connect to the VM in run_dir and install the bundles in order."""
    _install_all(_connect(run_dir), bundles)
