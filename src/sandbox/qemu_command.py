"""
QEMU command-line fragments shared by the base image build and a run.

A disk installed under one machine type, accelerator, CPU model, or disk attachment and booted under another
fails in ways that read as guest bugs, so those flags have one definition here. Everything else stays with the
caller: the build adds an ISO and a seed disk, a run adds the guest agent channel, QMP, and its network.
"""

from pathlib import Path
from typing import Final

MACHINE_TYPE: Final = 'q35'


def machine_flags(accel: str, cpus: int, memory_mb: int, disk: Path, pidfile: Path) -> list[str]:
    """
    Return the flags describing the machine itself, ending with the daemonize request.

    Args:
        `accel` (str): `kvm` or `tcg`; it also selects the CPU model.
        `cpus` (int): vCPU count.
        `memory_mb` (int): RAM in MiB.
        `disk` (Path): qcow2 image attached as the virtio system disk.
        `pidfile` (Path): File QEMU writes its pid into once it is daemonized.

    Returns:
        `flags` (list[str]): The shared flags, in command-line order.
    """
    return [
        '-machine',
        MACHINE_TYPE,
        '-accel',
        accel,
        '-cpu',
        'host' if accel == 'kvm' else 'max',
        '-smp',
        str(cpus),
        '-m',
        str(memory_mb),
        '-drive',
        f'file={disk},if=virtio,format=qcow2',
        '-device',
        'virtio-gpu-pci',
        '-display',
        'none',
        '-no-reboot',
        '-pidfile',
        str(pidfile),
        '-daemonize',
    ]
