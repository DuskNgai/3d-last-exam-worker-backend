"""
QEMU virtual machine lifecycle: overlay creation, start, stop, status.

Every run lives in its own run directory on local storage. All paths passed to
QEMU are absolute because `-daemonize` changes the working directory to `/`.
"""

from collections.abc import Callable, Iterator
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Final

from ..qemu_command import machine_flags
from ..qga import QgaClient, QgaError, QmpClient

OVERLAY_NAME: Final = 'overlay.qcow2'
QGA_SOCKET_NAME: Final = 'qga.sock'
QMP_SOCKET_NAME: Final = 'qmp.socket'
SERIAL_LOG_NAME: Final = 'serial.log'
PIDFILE_NAME: Final = 'vm.pid'
CONFIG_NAME: Final = 'vm.json'
LOCK_NAME: Final = 'run.lock'
RELAY_LOG_NAME: Final = 'relay.log'
USAGE_NAME: Final = 'usage.jsonl'
GUESTFWD_ADDRESS: Final = '10.0.2.100'
GUESTFWD_PORT: Final = 8443
STOP_GRACE_SECONDS: Final = 60.0
READY_TIMEOUT_SECONDS: Final = 300.0


class VmError(RuntimeError):
    """The QEMU process, overlay, or run directory is in a bad state."""


def overlay_path(run_dir: Path) -> Path:
    """
    Return the overlay disk path inside a run directory.

    Args:
        `run_dir` (Path): Run directory.

    Returns:
        `overlay` (Path): Absolute overlay path.
    """
    return run_dir.resolve() / OVERLAY_NAME


def qga_socket_path(run_dir: Path) -> Path:
    """Return the QGA unix socket path inside a run directory."""
    return run_dir.resolve() / QGA_SOCKET_NAME


def qmp_socket_path(run_dir: Path) -> Path:
    """Return the QMP unix socket path inside a run directory."""
    return run_dir.resolve() / QMP_SOCKET_NAME


@contextlib.contextmanager
def run_lock(run_dir: Path) -> Iterator[Path]:
    """
    Claim a run directory for the lifetime of one run.

    The claim is a non-blocking flock on <run_dir>/run.lock. The kernel drops it when the owning process
    dies, so a lock file left by a killed or kept run never blocks the next one; a directory a live run
    holds is refused before anything in it is touched.

    Args:
        `run_dir` (Path): Run directory to claim, created if missing.

    Yields:
        `lock_path` (Path): The lock file inside the run directory.

    Raises:
        VmError: A live run already holds the directory.
    """
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / LOCK_NAME
    with lock_path.open('a+', encoding='utf-8') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.seek(0)
            holder = handle.read().strip() or 'unknown'
            raise VmError(f'run directory {run_dir} is held by a live run (pid {holder})') from error
        handle.seek(0)
        handle.truncate()
        handle.write(f'{os.getpid()}\n')
        handle.flush()
        yield lock_path


def create_overlay(run_dir: Path, base_image: Path) -> Path:
    """
    Create a qcow2 overlay backed by the frozen base image.

    Args:
        `run_dir` (Path): Run directory, created if missing.
        `base_image` (Path): Frozen base qcow2.

    Returns:
        `overlay` (Path): The new overlay path.

    Raises:
        VmError: The base image is missing, an overlay already exists, or qemu-img failed.
    """
    base_image = base_image.resolve()
    if not base_image.is_file():
        raise VmError(f'Base image not found: {base_image}')
    run_dir.mkdir(parents=True, exist_ok=True)
    overlay = overlay_path(run_dir)
    if overlay.exists():
        raise VmError(f'Overlay already exists: {overlay}')
    command = ['qemu-img', 'create', '-f', 'qcow2', '-b', str(base_image), '-F', 'qcow2', str(overlay)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise VmError(f'qemu-img create failed: {result.stderr.strip()}')
    return overlay


def _build_qemu_command(
    run_dir: Path,
    accel: str,
    cpus: int,
    memory_mb: int,
    network: str,
    vnc: str | None,
    relay_command: str | None,
) -> list[str]:
    """Assemble the QEMU command line for a run directory."""
    run_dir = run_dir.resolve()
    command = ['qemu-system-x86_64']
    command += machine_flags(accel, cpus, memory_mb, overlay_path(run_dir), run_dir / PIDFILE_NAME)
    command += [
        '-chardev',
        f'socket,path={qga_socket_path(run_dir)},server=on,wait=off,id=qga0',
        '-device',
        'virtio-serial',
        '-device',
        'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
        '-qmp',
        f'unix:{qmp_socket_path(run_dir)},server=on,wait=off',
        '-serial',
        f'file:{run_dir / SERIAL_LOG_NAME}',
    ]
    if network == 'none':
        command += ['-nic', 'none']
    elif network == 'nat':
        command += ['-nic', 'user,model=virtio-net-pci']
    elif network == 'gateway':
        netdev = 'user,id=net0,restrict=on'
        if relay_command:
            if ',' in relay_command:
                raise VmError('relay_command must not contain commas; QEMU netdev options are comma-separated')
            netdev += f',guestfwd=tcp:{GUESTFWD_ADDRESS}:{GUESTFWD_PORT}-cmd:{relay_command}'
        command += ['-netdev', netdev, '-device', 'virtio-net-pci,netdev=net0']
    else:
        raise VmError(f'Unknown network mode: {network}')
    if vnc is not None:
        # A bare `:N` makes QEMU listen on every interface with no password; keep it on loopback.
        command += ['-vnc', f'127.0.0.1{vnc}' if vnc.startswith(':') else vnc]
    return command


def start_vm(
    run_dir: Path,
    cpus: int,
    memory_mb: int,
    accel: str = 'kvm',
    network: str = 'gateway',
    vnc: str | None = None,
    relay_command: str | None = None,
) -> int:
    """
    Start a daemonized QEMU for an existing run directory overlay.

    Args:
        `run_dir` (Path): Run directory holding the overlay.
        `cpus` (int): Number of virtual CPUs.
        `memory_mb` (int): RAM in MiB.
        `accel` (str): `kvm` or `tcg`.
        `network` (str): `none`, `nat`, or `gateway`; gateway isolates the guest with restrict=on
            and, when relay_command is given, forwards one address to it, spawned per guest connection.
        `vnc` (str | None): Optional VNC display such as `:5` for debugging; a bare `:5` is pinned to
            loopback, and an explicit host such as `0.0.0.0:5` is passed through as written.
        `relay_command` (str | None): Command QEMU runs per forwarded guest connection; only used
            in gateway mode, must not contain commas.

    Returns:
        `pid` (int): The QEMU process pid.

    Raises:
        VmError: The overlay is missing, the VM is already running, a relay_command is invalid, or QEMU failed to start.
    """
    run_dir = run_dir.resolve()
    if not overlay_path(run_dir).is_file():
        raise VmError(f'Overlay not found: {overlay_path(run_dir)}')
    if read_pid(run_dir) is not None:
        raise VmError(f'VM already running in {run_dir}')
    for stale in (qga_socket_path(run_dir), qmp_socket_path(run_dir)):
        stale.unlink(missing_ok=True)
    command = _build_qemu_command(run_dir, accel, cpus, memory_mb, network, vnc, relay_command)
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise VmError(f'QEMU failed to start: {result.stderr.strip()}')
    pid = read_pid(run_dir, wait=True)
    if pid is None:
        raise VmError('QEMU daemonized but no pidfile appeared')
    config = {
        'accel': accel,
        'cpus': cpus,
        'memory_mb': memory_mb,
        'network': network,
        'vnc': vnc,
        'relay_command': relay_command,
        'pid': pid
    }
    (run_dir / CONFIG_NAME).write_text(json.dumps(config, indent=2) + '\n')
    return pid


def read_pid(run_dir: Path, wait: bool = False) -> int | None:
    """
    Return the live QEMU pid for a run directory, or None.

    Args:
        `run_dir` (Path): Run directory holding the pidfile.
        `wait` (bool): Give a just-daemonized QEMU a few seconds to write the pidfile.

    Returns:
        `pid` (int | None): The live QEMU pid, or None when the pidfile is missing or the process is gone.
    """
    pidfile = run_dir.resolve() / PIDFILE_NAME
    deadline = time.monotonic() + 10.0
    while True:
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            pid = None
        if pid is not None:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return None
            return pid
        if not wait or time.monotonic() > deadline:
            return None
        time.sleep(0.5)


def wait_ready(
    run_dir: Path,
    timeout: float = READY_TIMEOUT_SECONDS,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    """
    Block until the guest agent answers or the timeout expires.

    Args:
        `run_dir` (Path): Run directory holding the QGA socket.
        `timeout` (float): Seconds to wait for the guest agent.
        `cancel_requested` (Callable[[], bool] | None): Whether the run was cancelled.

    Raises:
        VmError: The VM died or the guest agent never came up.
    """
    run_dir = run_dir.resolve()
    qga = QgaClient(qga_socket_path(run_dir))
    deadline = time.monotonic() + timeout
    while True:
        if cancel_requested is not None and cancel_requested():
            raise VmError('VM startup was cancelled')
        if read_pid(run_dir) is None:
            raise VmError(f'QEMU exited while waiting for the guest agent; see {run_dir / SERIAL_LOG_NAME}')
        try:
            qga.ping()
            return
        except (QgaError, OSError):
            pass
        if time.monotonic() > deadline:
            raise VmError(f'Guest agent not ready after {timeout}s; see {run_dir / SERIAL_LOG_NAME}')
        time.sleep(0.5)


def _wait_dead(run_dir: Path, seconds: float) -> bool:
    """Poll until the QEMU process is gone or the budget runs out."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if read_pid(run_dir) is None:
            return True
        time.sleep(1.0)
    return read_pid(run_dir) is None


def stop_vm(run_dir: Path, grace: float = STOP_GRACE_SECONDS) -> str:
    """
    Stop the VM with escalating force and report the level that worked.

    ACPI power button via QMP is the graceful level: qemu-ga's guest-shutdown and
    QGA-issued `systemctl poweroff` both hang on this guest because stopping
    qemu-guest-agent.service waits on the very exec session that requested it.

    Args:
        `run_dir` (Path): Run directory of the running VM.
        `grace` (float): Seconds to wait for the ACPI shutdown.

    Returns:
        `level` (str): `powerdown`, `term`, `kill`, or `stopped` if it was not running.
    """
    run_dir = run_dir.resolve()
    pid = read_pid(run_dir)
    if pid is None:
        return 'stopped'
    try:
        QmpClient(qmp_socket_path(run_dir), timeout=10.0).system_powerdown()
    except (QgaError, OSError):
        pass
    if _wait_dead(run_dir, grace):
        return 'powerdown'
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return 'powerdown'
    if _wait_dead(run_dir, 10.0):
        return 'term'
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return 'term'
    return 'kill'


def vm_status(run_dir: Path) -> dict[str, object]:
    """
    Report pid, QMP runstate, and guest agent reachability.

    Args:
        `run_dir` (Path): Run directory to inspect.

    Returns:
        `status` (dict[str, object]): Keys `pid`, `runstate`, `qga`.
    """
    run_dir = run_dir.resolve()
    status: dict[str, object] = {
        'pid': read_pid(run_dir),
        'runstate': None,
        'qga': False
    }
    if status['pid'] is None:
        status['runstate'] = 'not running'
        return status
    try:
        status['runstate'] = QmpClient(qmp_socket_path(run_dir), timeout=5.0).query_status()
    except (QgaError, OSError) as error:
        status['runstate'] = f'qmp error: {error}'
    try:
        QgaClient(qga_socket_path(run_dir), timeout=5.0).ping()
        status['qga'] = True
    except (QgaError, OSError):
        pass
    return status
