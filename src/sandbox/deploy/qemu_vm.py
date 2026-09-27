"""QEMU virtual machine lifecycle: overlay creation, start, stop, status.

Every run lives in its own run directory on local storage. All paths passed to
QEMU are absolute because `-daemonize` changes the working directory to `/`.
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Final

from ..qga import QgaClient, QgaError, QmpClient

VMSTORE: Final = Path(os.environ.get('VMSTORE', '/home/vrlab/vmstore'))
DEFAULT_BASE_IMAGE: Final = VMSTORE / 'images/ubuntu-24.04.5-desktop-base/base.qcow2'
DEFAULT_RUNS_ROOT: Final = VMSTORE / 'runs'
OVERLAY_NAME: Final = 'overlay.qcow2'
QGA_SOCKET_NAME: Final = 'qga.sock'
QMP_SOCKET_NAME: Final = 'qmp.socket'
SERIAL_LOG_NAME: Final = 'serial.log'
PIDFILE_NAME: Final = 'vm.pid'
CONFIG_NAME: Final = 'vm.json'
STOP_GRACE_SECONDS: Final = 60.0
READY_TIMEOUT_SECONDS: Final = 300.0


class VmError(RuntimeError):
    """The QEMU process, overlay, or run directory is in a bad state."""


def overlay_path(run_dir: Path) -> Path:
    """Return the overlay disk path inside a run directory.

    `run_dir` (Path): Run directory.

    Returns:
        Path: Absolute overlay path.
    """
    return run_dir.resolve() / OVERLAY_NAME


def qga_socket_path(run_dir: Path) -> Path:
    """Return the QGA unix socket path inside a run directory."""
    return run_dir.resolve() / QGA_SOCKET_NAME


def qmp_socket_path(run_dir: Path) -> Path:
    """Return the QMP unix socket path inside a run directory."""
    return run_dir.resolve() / QMP_SOCKET_NAME


def create_overlay(run_dir: Path, base_image: Path = DEFAULT_BASE_IMAGE) -> Path:
    """Create a qcow2 overlay backed by the frozen base image.

    `run_dir` (Path): Run directory, created if missing.
    `base_image` (Path): Frozen base qcow2.

    Returns:
        Path: The new overlay path.

    Raises:
        VmError: The base image is missing or qemu-img failed.
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


def _build_qemu_command(run_dir: Path, accel: str, smp: int, memory: int, network: str, vnc: str | None,
                        relay_command: str | None) -> list[str]:
    """Assemble the QEMU command line for a run directory."""
    run_dir = run_dir.resolve()
    command = [
        'qemu-system-x86_64',
        '-machine', 'q35',
        '-accel', accel,
        '-cpu', 'host' if accel == 'kvm' else 'max',
        '-smp', str(smp),
        '-m', str(memory),
        '-drive', f'file={overlay_path(run_dir)},if=virtio,format=qcow2',
        '-device', 'virtio-gpu-pci',
        '-display', 'none',
        '-chardev', f'socket,path={qga_socket_path(run_dir)},server=on,wait=off,id=qga0',
        '-device', 'virtio-serial',
        '-device', 'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
        '-qmp', f'unix:{qmp_socket_path(run_dir)},server=on,wait=off',
        '-serial', f'file:{run_dir / SERIAL_LOG_NAME}',
        '-pidfile', str(run_dir / PIDFILE_NAME),
        '-no-reboot',
        '-daemonize',
    ]
    if network == 'none':
        command += ['-nic', 'none']
    elif network == 'nat':
        command += ['-nic', 'user,model=virtio-net-pci']
    elif network == 'gateway':
        if not relay_command:
            raise VmError('gateway mode needs relay_command; guestfwd spawns it per guest connection')
        if ',' in relay_command:
            raise VmError('relay_command must not contain commas; QEMU netdev options are comma-separated')
        command += [
            '-netdev', f'user,id=net0,restrict=on,guestfwd=tcp:10.0.2.100:8443-cmd:{relay_command}',
            '-device', 'virtio-net-pci,netdev=net0',
        ]
    else:
        raise VmError(f'Unknown network mode: {network}')
    if vnc is not None:
        command += ['-vnc', vnc]
    return command


def start_vm(run_dir: Path, accel: str = 'kvm', smp: int = 4, memory: int = 16384,
             network: str = 'none', vnc: str | None = None, relay_command: str | None = None) -> int:
    """Start a daemonized QEMU for an existing run directory overlay.

    `run_dir` (Path): Run directory holding the overlay.
    `accel` (str): `kvm` or `tcg`.
    `smp` (int): vCPU count.
    `memory` (int): RAM in MiB.
    `network` (str): `none`, `nat`, or `gateway`; gateway isolates the guest and forwards
        one address to relay_command, spawned per guest connection.
    `vnc` (str | None): Optional VNC display such as `:5` for debugging.
    `relay_command` (str | None): Command QEMU runs per forwarded guest connection; required
        for gateway mode, must not contain commas.

    Returns:
        int: The QEMU process pid.

    Raises:
        VmError: The overlay is missing, the VM is already running, or QEMU failed to start.
    """
    run_dir = run_dir.resolve()
    if not overlay_path(run_dir).is_file():
        raise VmError(f'Overlay not found: {overlay_path(run_dir)}')
    if read_pid(run_dir) is not None:
        raise VmError(f'VM already running in {run_dir}')
    for stale in (qga_socket_path(run_dir), qmp_socket_path(run_dir)):
        stale.unlink(missing_ok=True)
    command = _build_qemu_command(run_dir, accel, smp, memory, network, vnc, relay_command)
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise VmError(f'QEMU failed to start: {result.stderr.strip()}')
    pid = read_pid(run_dir, wait=True)
    if pid is None:
        raise VmError('QEMU daemonized but no pidfile appeared')
    config = {'accel': accel, 'smp': smp, 'memory': memory, 'network': network, 'vnc': vnc,
              'relay_command': relay_command, 'pid': pid}
    (run_dir / CONFIG_NAME).write_text(json.dumps(config, indent=2) + '\n')
    return pid


def read_pid(run_dir: Path, wait: bool = False) -> int | None:
    """Return the live QEMU pid for a run directory, or None.

    `run_dir` (Path): Run directory.
    `wait` (bool): Give a just-daemonized QEMU a few seconds to write the pidfile.
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
            except ProcessLookupError:
                return None
            return pid
        if not wait or time.monotonic() > deadline:
            return None
        time.sleep(0.5)


def wait_ready(run_dir: Path, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    """Block until the guest agent answers or the timeout expires.

    Raises:
        VmError: The VM died or the guest agent never came up.
    """
    run_dir = run_dir.resolve()
    qga = QgaClient(qga_socket_path(run_dir))
    deadline = time.monotonic() + timeout
    while True:
        if read_pid(run_dir) is None:
            raise VmError(f'QEMU exited while waiting for the guest agent; see {run_dir / SERIAL_LOG_NAME}')
        try:
            qga.ping()
            return
        except (QgaError, OSError):
            pass
        if time.monotonic() > deadline:
            raise VmError(f'Guest agent not ready after {timeout}s')
        time.sleep(2.0)


def _wait_dead(run_dir: Path, seconds: float) -> bool:
    """Poll until the QEMU process is gone or the budget runs out."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if read_pid(run_dir) is None:
            return True
        time.sleep(1.0)
    return read_pid(run_dir) is None


def stop_vm(run_dir: Path, grace: float = STOP_GRACE_SECONDS) -> str:
    """Stop the VM with escalating force and report the level that worked.

    ACPI power button via QMP is the graceful level: qemu-ga's guest-shutdown and
    QGA-issued `systemctl poweroff` both hang on this guest because stopping
    qemu-guest-agent.service waits on the very exec session that requested it.

    `run_dir` (Path): Run directory.
    `grace` (float): Seconds to wait for the ACPI shutdown.

    Returns:
        str: `powerdown`, `term`, `kill`, or `stopped` if it was not running.
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


def destroy_run(run_dir: Path) -> None:
    """Stop the VM if needed and delete the run directory."""
    stop_vm(run_dir)
    shutil.rmtree(run_dir.resolve())


def vm_status(run_dir: Path) -> dict[str, object]:
    """Report pid, QMP runstate, and guest agent reachability.

    Returns:
        dict: Keys `pid`, `runstate`, `qga`.
    """
    run_dir = run_dir.resolve()
    status: dict = {'pid': read_pid(run_dir), 'runstate': None, 'qga': False}
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
