"""Guest operations over QGA/QMP: run commands, transfer files, screenshot, list processes.

The CLI mirrors the old vmrun-based tool but needs no credentials: control flows
through the qemu-guest-agent virtio channel and the QMP socket of a running VM.
"""

from pathlib import Path
import sys
import time
from typing import Final
import uuid

from ..qga import QgaClient, QgaError, QmpClient

DEFAULT_TIMEOUT: Final = 120.0
HOST_DEADLINE_SLACK: Final = 30.0
POLL_INTERVAL: Final = 2.0
EXIT_CODE_NOT_FOUND: Final = 126


class GuestError(RuntimeError):
    """A guest operation failed before producing a usable result."""


def run_command(qga: QgaClient, command: str, timeout: float, cwd: str | None = None, user: str | None = None) -> int:
    """Run a shell command in the guest, stream its output back, and return its exit code.

    The guest-side `timeout` is the real terminator; the host side only gives up
    waiting. The true exit code is recovered from a file the wrapper writes, so a
    QGA capture failure cannot masquerade as success.

    `qga` (QgaClient): Connected guest agent client.
    `command` (str): Shell command line run via bash.
    `timeout` (float): Seconds before the guest kills the command.
    `cwd` (str | None): Working directory inside the guest.
    `user` (str | None): Drop privileges to this user via runuser.

    Returns:
        int: The command's real exit code.

    Raises:
        GuestError: The wrapper itself never reported back.
    """
    work_dir = f'/tmp/qga-run-{uuid.uuid4().hex[:12]}'
    seconds = int(timeout)
    inner = command
    if cwd:
        inner = f'cd {cwd} && {{ {command}; }}'
    wrapper = (f'timeout -s KILL {seconds} bash -c {sh_quote(inner)} >stdout.log 2>stderr.log; '
               f'echo -n $? >exitcode')
    if user:
        wrapper = f'runuser -u {user} -- bash -c {sh_quote(wrapper)}'
    qga.run(['mkdir', '-p', '-m', '777', work_dir], timeout=30.0)
    qga.run(['chmod', '777', work_dir], timeout=30.0)
    launch = f'cd {work_dir} && {wrapper}'
    pid = qga.exec_start(['/bin/bash', '-c', launch])
    deadline = time.monotonic() + timeout + HOST_DEADLINE_SLACK
    while True:
        result = qga.exec_status(pid)
        if result is not None:
            break
        if time.monotonic() > deadline:
            raise GuestError(f'guest command still running after {timeout}s, guest pid {pid}; VM left as-is')
        time.sleep(POLL_INTERVAL)
    stdout = _read_guest_file(qga, f'{work_dir}/stdout.log')
    stderr = _read_guest_file(qga, f'{work_dir}/stderr.log')
    sys.stdout.buffer.write(stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(stderr)
    sys.stderr.buffer.flush()
    try:
        exitcode = int(_read_guest_file(qga, f'{work_dir}/exitcode').decode().strip())
    except (ValueError, GuestError) as error:
        raise GuestError(f'wrapper exitcode file missing in {work_dir}') from error
    qga.run(['rm', '-rf', work_dir], timeout=30.0)
    return exitcode


def _read_guest_file(qga: QgaClient, remote_path: str) -> bytes:
    """Download a guest file, converting QGA failures into GuestError."""
    try:
        return qga.download(remote_path)
    except QgaError as error:
        raise GuestError(f'cannot read guest file {remote_path}: {error}') from error


def sh_quote(text: str) -> str:
    """Quote a string for POSIX sh.

    `text` (str): Raw text.

    Returns:
        str: Single-quoted literal safe to embed in a shell command.
    """
    return "'" + text.replace("'", "'\\''") + "'"


def take_screenshot(qmp: QmpClient, output: Path) -> Path:
    """Capture the guest display and convert it to png.

    `qmp` (QmpClient): Connected QMP client.
    `output` (Path): Target png path on the host.

    Returns:
        Path: The png path.
    """
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ppm = output.with_suffix('.ppm')
    qmp.screendump(ppm)
    try:
        from PIL import Image
    except ImportError as error:
        raise GuestError('python3-pil is required for png conversion') from error
    with Image.open(ppm) as image:
        image.save(output)
    ppm.unlink()
    return output


def list_processes(qga: QgaClient) -> int:
    """Print the guest process table.

    Returns:
        int: Exit code of the ps command.
    """
    result = qga.run(['ps', '-eo', 'pid,ppid,user,comm'], timeout=30.0)
    sys.stdout.buffer.write(result.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(result.stderr)
    sys.stderr.buffer.flush()
    return result.exitcode
