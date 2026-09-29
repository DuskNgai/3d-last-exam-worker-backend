"""
Deploy software bundles into a running QEMU/KVM guest over the guest agent.

A bundle is a host directory packed into a tar.gz, uploaded in 64 MiB parts,
and extracted in the guest under /task/bundles/<name>. Each bundle runs an
install script, uploaded into that directory as install.sh: a bundle names the
script its own material carries, the harness bundle the shared installer at the
root of the harness material. The script runs in the guest as root from inside
the bundle, with the bundle directory as its first argument and the bundle
version, when it has one, as the second.
"""

from collections.abc import Iterator
from dataclasses import dataclass
import io
from pathlib import Path
import shlex
import sys
import tarfile
import tempfile
from typing import Final
import uuid

from ..qga import QgaClient, QgaError
from .qemu_vm import qga_socket_path

CHUNK_BYTES: Final = 64 * 1024 * 1024
GUEST_ROOT: Final = '/task'
GUEST_BUNDLES: Final = '/task/bundles'
INSTALL_NAME: Final = 'install.sh'
QUICK_TIMEOUT: Final = 30.0
MERGE_TIMEOUT: Final = 600.0
INSTALL_TIMEOUT: Final = 1800.0
SUMMARY_LIMIT: Final = 2000


class DeployError(RuntimeError):
    """A deployment step failed."""


@dataclass
class BundleConfig:
    """
    One bundle entry; installation follows list order.

    `path` is the material directory and `script` is the install script it runs,
    uploaded into the bundle as install.sh. A tool bundle names the script that
    already sits in its own directory; the harness bundle names the shared
    harness installer. `version` is passed to the script as its second argument
    when it is set.
    """

    name: str
    path: Path
    script: Path
    version: str | None = None


def parse_spec(spec: str) -> tuple[str, str]:
    """
    Split one `<name>@<version>` spec, used for tools and for the harness.

    Args:
        `spec` (str): The spec to split.

    Returns:
        `name` (str): The part before the `@`.
        `version` (str): The part after the `@`.

    Raises:
        DeployError: The spec is missing its name or its version.
    """
    name, separator, version = spec.partition('@')
    if not separator or not name or not version:
        raise DeployError(f'spec must be <name>@<version>: {spec!r}')
    return name, version


def _summarize(data: bytes) -> str:
    """Return the tail of captured guest output as text for error messages."""
    text = data.decode(errors='replace').strip()
    if len(text) > SUMMARY_LIMIT:
        text = f'...{text[-SUMMARY_LIMIT:]}'
    return text or '<no output>'


def _remove_guest_tarball(qga: QgaClient, remote_tar: str) -> None:
    """Best-effort removal of a leftover archive in the guest."""
    try:
        qga.run(['/bin/bash', '-c', f'rm -f {shlex.quote(remote_tar)}'], timeout=QUICK_TIMEOUT)
    except (QgaError, TimeoutError, OSError):
        pass


def _stream_parts(stream: io.BufferedIOBase, label: str) -> Iterator[bytes]:
    """Yield a tar stream in CHUNK_BYTES pieces with per-part progress output."""
    index = 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            return
        yield chunk
        print(f'{label}: uploaded part {index:03d}, {len(chunk)} bytes', flush=True)
        index += 1


def upload_tree(qga: QgaClient, host_dir: Path, guest_dir: str, *, label: str, timeout: float = MERGE_TIMEOUT) -> None:
    """
    Pack a host directory, stream the archive into the guest, and extract it there.

    The archive goes to a temporary file instead of into memory and travels through upload_large, so a
    multi-gigabyte tree costs no host memory and uploads at part speed. Bundles and task inputs both come
    through here; nothing else packs a directory.

    Args:
        `qga` (QgaClient): Guest agent client used for the upload and the guest-side extract.
        `host_dir` (Path): Host directory whose contents are packed.
        `guest_dir` (str): Guest directory the tree is extracted into, created if missing.
        `label` (str): Name used in progress output and error messages.
        `timeout` (float): Guest-side timeout for the payload merge and for the extraction.

    Raises:
        DeployError: Packing, upload, or guest-side extraction failed.
    """
    print(f'{label}: packing {host_dir}', flush=True)
    remote_tar = f'/tmp/upload-{uuid.uuid4().hex[:8]}.tar.gz'
    try:
        with tempfile.TemporaryFile(prefix=f'upload-{label}-', suffix='.tar.gz') as stream:
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                archive.add(host_dir, arcname='.')
            stream.seek(0)
            qga.upload_large(remote_tar, _stream_parts(stream, label), timeout=timeout)
        target = shlex.quote(guest_dir)
        tarball = shlex.quote(remote_tar)
        extract = f'mkdir -p {target} && tar -xzf {tarball} -C {target} && rm -f {tarball}'
        result = qga.run(['/bin/bash', '-c', extract], timeout=timeout)
        if result.exitcode != 0:
            raise DeployError(f'{label}: guest extraction failed: {_summarize(result.stderr)}')
    except (OSError, tarfile.TarError) as error:
        raise DeployError(f'{label}: packing failed: {error}') from error
    except (QgaError, TimeoutError) as error:
        raise DeployError(f'{label}: upload failed: {error}') from error
    finally:
        _remove_guest_tarball(qga, remote_tar)
    print(f'{label}: extracted to {guest_dir}', flush=True)


def _upload_bundle(qga: QgaClient, name: str, bundle_dir: Path) -> None:
    """
    Upload one bundle directory and extract it under its guest directory.

    Args:
        `qga` (QgaClient): Guest agent client used for the upload and the guest-side extract.
        `name` (str): Bundle name; the guest target directory is named after it.
        `bundle_dir` (Path): Host directory holding the bundle material.

    Raises:
        DeployError: Packing, upload, or guest-side extraction failed.
    """
    upload_tree(qga, bundle_dir, f'{GUEST_BUNDLES}/{name}', label=f'bundle {name}')


def _run_install_script(qga: QgaClient, name: str, script: Path, version: str | None) -> None:
    """
    Upload the bundle's install script into the bundle and run it there as root.

    Args:
        `qga` (QgaClient): Guest agent client used to upload and run the script.
        `name` (str): Bundle name whose guest directory receives the script.
        `script` (Path): Host path of the install script to upload.
        `version` (str | None): Version passed to the script as its second argument; no argument when None.

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

    Args:
        `run_dir` (Path): VM run directory holding the QGA socket.

    Returns:
        `qga` (QgaClient): Connected client that has answered a ping.

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

    Args:
        `qga` (QgaClient): Guest agent client used for every bundle.
        `bundles` (list[BundleConfig]): Bundles to deploy, in installation order.

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
    """
    Connect to the VM in run_dir and install the bundles in order.

    Args:
        `run_dir` (Path): VM run directory holding the QGA socket.
        `bundles` (list[BundleConfig]): Bundles to deploy, in installation order.

    Raises:
        DeployError: The QGA socket is missing, the bundle list is empty, or a deployment step failed.
    """
    _install_all(_connect(run_dir), bundles)
