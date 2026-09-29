"""Guest file writes over QGA: upload content and set its mode and owner."""

from ..qga import QgaClient


class GuestError(RuntimeError):
    """A guest operation failed before producing a usable result."""


def write_file(qga: QgaClient, remote_path: str, content: str, mode: str = '644', owner: str | None = None) -> None:
    """
    Write text to a guest file with mode and optional owner.

    Args:
        `qga` (QgaClient): Guest agent client used to upload and fix permissions.
        `remote_path` (str): Guest path that receives the content.
        `content` (str): Text written to the file, encoded as UTF-8.
        `mode` (str): chmod mode applied to the file.
        `owner` (str | None): chown owner and group; skipped when None.

    Raises:
        GuestError: The write or a permission fix failed.
    """
    qga.upload(remote_path, content.encode())
    result = qga.run(['/bin/bash', '-c', f'chmod {mode} {remote_path}'], timeout=30.0)
    if result.exitcode != 0:
        raise GuestError(f'chmod failed for {remote_path}: {result.stderr.decode()[:300]}')
    if owner:
        result = qga.run(['/bin/bash', '-c', f'chown {owner} {remote_path}'], timeout=30.0)
        if result.exitcode != 0:
            raise GuestError(f'chown failed for {remote_path}: {result.stderr.decode()[:300]}')
