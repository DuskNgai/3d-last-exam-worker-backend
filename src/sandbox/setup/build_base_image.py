"""Build the Ubuntu desktop base image for QEMU sandboxes via unattended autoinstall.

A cloud-init seed drives subiquity, the installed disk is verified with an
offline boot (QGA ping, OS facts, desktop screendump), then frozen read-only
with a manifest.json recording hashes and guest facts.
"""

import datetime
import hashlib
import json
import logging
import os
import platform
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..qga import QgaClient, QgaError, QmpClient

BASE_IMAGE_DIR: Final = Path(__file__).resolve().parent / 'base_image'
TOOLS: Final = ('qemu-system-x86_64', 'qemu-img', 'genisoimage', 'isoinfo', 'openssl')
INSTALL_TIMEOUT_SECONDS: Final = 4500
STALL_WATCHDOG_SECONDS: Final = 900
TCG_TIMEOUT_FACTOR: Final = 4
STALL_MIN_DISK_BYTES: Final = 1048576
POLL_SECONDS: Final = 20
QGA_TIMEOUT: Final = 300.0
DESKTOP_TIMEOUT: Final = 180.0


class BuildError(RuntimeError):
    """A build step failed."""


@dataclass(frozen=True)
class BuildConfig:
    """Parameters of one base image build.

    Attributes:
        iso (Path): Ubuntu desktop ISO used as the install source.
        iso_sha256 (str | None): Official reference hash; the build fails on mismatch, None skips the check.
        out (Path): Output directory for base.qcow2, manifest.json, and the seed directory.
        accel (str): `kvm` or `tcg`.
        smp (int): vCPU count for the build VMs.
        memory (int): RAM in MiB for the build VMs.
        disk (str): Disk size passed to qemu-img create.
        skip_install (bool): Reuse an existing base.qcow2 and skip to verification.
    """

    iso: Path
    iso_sha256: str | None
    out: Path
    accel: str = 'kvm'
    smp: int = 4
    memory: int = 8192
    disk: str = '64G'
    skip_install: bool = False


def _sha256(path: Path) -> str:
    """Return the hex sha256 of a file, streaming the reads."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _alive(pid: int) -> bool:
    """Return whether the process exists."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _tail_last_line(path: Path) -> str:
    """Return the last line of a log, truncated, or empty when unreadable."""
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return ''
    return lines[-1][:160].decode(errors='replace') if lines else ''


def _qemu_common(config: BuildConfig) -> list[str]:
    """QEMU flags shared by the install and verification boots."""
    return [
        '-machine', 'q35', '-accel', config.accel, '-cpu', 'host' if config.accel == 'kvm' else 'max',
        '-smp', str(config.smp), '-m', str(config.memory),
        '-drive', f'file={config.out}/base.qcow2,if=virtio,format=qcow2',
        '-device', 'virtio-gpu-pci',
        '-display', 'none',
        '-no-reboot',
        '-pidfile', str(config.out / 'qemu.pid'),
        '-daemonize',
    ]


def _check_prerequisites(config: BuildConfig) -> None:
    """Verify tools, KVM access when accel is kvm, and the ISO before touching anything."""
    for tool in TOOLS:
        if shutil.which(tool) is None:
            raise BuildError(f'missing tool: {tool}')
    if config.accel == 'kvm' and not (os.access('/dev/kvm', os.R_OK) and os.access('/dev/kvm', os.W_OK)):
        raise BuildError('no KVM access on /dev/kvm; pass --accel tcg to build without KVM')
    if not config.iso.is_file():
        raise BuildError(f'ISO not found: {config.iso}')


def _check_iso_hash(config: BuildConfig, logger: logging.Logger) -> tuple[str, bool | None]:
    """Hash the ISO and compare with the caller-supplied reference when given.

    Returns:
        tuple[str, bool | None]: The ISO sha256 and whether it matches the reference, None when no
        reference was given.

    Raises:
        BuildError: A reference hash was given and does not match.
    """
    logger.info('== ISO verification ==')
    iso_sha256 = _sha256(config.iso)
    logger.info('iso sha256: %s', iso_sha256)
    if config.iso_sha256 is None:
        logger.info('no --iso-sha256 given; recording the computed hash without provenance check')
        return iso_sha256, None
    if iso_sha256.lower() != config.iso_sha256.lower():
        raise BuildError(f'ISO hash mismatch: got {iso_sha256}, expected {config.iso_sha256}')
    logger.info('matches the reference sha256')
    return iso_sha256, True


def _prepare_seed(config: BuildConfig, logger: logging.Logger) -> tuple[str, bool | None]:
    """Verify the ISO, extract the installer kernel, and render the cidata seed.

    Returns:
        tuple[str, bool | None]: The ISO sha256 and whether it matches the reference hash.
    """
    iso_sha256, verified = _check_iso_hash(config, logger)

    logger.info('== Extract installer kernel and initrd ==')
    seed = config.out / 'seed'
    for source, name in (('/casper/vmlinuz', 'vmlinuz'), ('/casper/initrd', 'initrd')):
        with (seed / name).open('wb') as stream:
            subprocess.run(['isoinfo', '-R', '-i', str(config.iso), '-x', source], stdout=stream, check=True)
    if not (seed / 'vmlinuz').stat().st_size or not (seed / 'initrd').stat().st_size:
        raise BuildError('failed to extract casper kernel/initrd')

    logger.info('== Prepare autoinstall seed ==')
    admin_password = secrets.token_hex(16)
    admin_hash = subprocess.run(['openssl', 'passwd', '-6', admin_password],
                                capture_output=True, text=True, check=True).stdout.strip()
    secret_file = config.out / 'admin-password.txt'
    fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(f'base image privileged user: sandboxadm\npassword: {admin_password}\n')
    os.chmod(secret_file, 0o600)
    cidata = seed / 'cidata'
    template = (BASE_IMAGE_DIR / 'user-data').read_text()
    (cidata / 'user-data').write_text(template.replace('__ADMIN_HASH__', admin_hash))
    shutil.copy(BASE_IMAGE_DIR / 'meta-data', cidata / 'meta-data')
    subprocess.run(['genisoimage', '-output', str(seed / 'seed.iso'), '-volid', 'cidata', '-joliet', '-rock',
                    '-input-charset', 'utf8', 'user-data', 'meta-data'], cwd=cidata, check=True,
                   capture_output=True)
    return iso_sha256, verified


def _run_install(config: BuildConfig, logger: logging.Logger) -> None:
    """Boot the unattended installer and watch it to poweroff or a stuck state."""
    logger.info('== Create base disk ==')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(config.out / 'base.qcow2'), config.disk], check=True)

    timeout = INSTALL_TIMEOUT_SECONDS
    stall_watchdog = STALL_WATCHDOG_SECONDS
    if config.accel == 'tcg':
        timeout *= TCG_TIMEOUT_FACTOR
        stall_watchdog *= TCG_TIMEOUT_FACTOR
        logger.warning('TCG build is hour-scale; timeouts relaxed by %dx. '
                       'Prefer building on a KVM host and copying the frozen directory', TCG_TIMEOUT_FACTOR)
    logger.info('== Unattended install (timeout %ds) ==', timeout)
    seed = config.out / 'seed'
    subprocess.run(['qemu-system-x86_64', *_qemu_common(config),
                    '-kernel', str(seed / 'vmlinuz'),
                    '-initrd', str(seed / 'initrd'),
                    '-append', 'autoinstall ds=nocloud console=ttyS0,115200n8',
                    '-drive', f'file={config.iso},media=cdrom,readonly=on',
                    '-drive', f'file={seed}/seed.iso,media=cdrom,readonly=on',
                    '-netdev', 'user,id=n0', '-device', 'virtio-net-pci,netdev=n0',
                    '-serial', f'file:{seed}/serial-install.log',
                    '-qmp', f'unix:{config.out}/qmp-install.sock,server=on,wait=off'], check=True)
    pid = int((config.out / 'qemu.pid').read_text().strip())
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    serial_log = seed / 'serial-install.log'
    while _alive(pid):
        if time.monotonic() > deadline:
            raise BuildError(f'install timed out; leaving VM running for inspection, pid {pid}')
        if time.monotonic() - started > stall_watchdog:
            disk_size = (config.out / 'base.qcow2').stat().st_size
            if disk_size < STALL_MIN_DISK_BYTES:
                raise BuildError(f'no disk writes after {stall_watchdog // 60} minutes; installer likely stuck. '
                                 f'Inspect via QMP screendump on {config.out}/qmp-install.sock. VM left running, pid {pid}')
        time.sleep(POLL_SECONDS)
        logger.info('%s', _tail_last_line(serial_log))
    logger.info('== Installer powered off ==')
    logger.info('%s', '\n'.join(serial_log.read_bytes().decode(errors='replace').splitlines()[-15:]))


def _verify_boot(config: BuildConfig, logger: logging.Logger) -> dict[str, object]:
    """Boot the new disk offline and collect guest facts plus a desktop screendump.

    Returns:
        dict: The verification report, also written to seed/verify.json.
    """
    logger.info('== Offline verification boot ==')
    seed = config.out / 'seed'
    subprocess.run(['qemu-system-x86_64', *_qemu_common(config),
                    '-nic', 'none',
                    '-chardev', f'socket,path={config.out}/qga.sock,server=on,wait=off,id=qga0',
                    '-device', 'virtio-serial-pci', '-device', 'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
                    '-serial', f'file:{seed}/serial-verify.log',
                    '-qmp', f'unix:{config.out}/qmp-verify.sock,server=on,wait=off'], check=True)
    verify_pid = int((config.out / 'qemu.pid').read_text().strip())
    logger.info('verify boot pid: %d', verify_pid)

    client = QgaClient(config.out / 'qga.sock', timeout=15)
    deadline = time.monotonic() + QGA_TIMEOUT
    while True:
        try:
            client.ping()
            break
        except (QgaError, OSError, TimeoutError):
            if time.monotonic() > deadline:
                _kill_pid(verify_pid)
                raise BuildError('QGA never answered on the offline boot')
            time.sleep(5)
    logger.info('QGA ping OK')

    kernel = client.run(['/bin/uname', '-r']).stdout.decode().strip()
    os_name = client.run(['/bin/sh', '-c', '. /etc/os-release; echo "$PRETTY_NAME"']).stdout.decode().strip()
    links = client.run(['/bin/sh', '-c', "ip -o link show | awk '{print $2}'"]).stdout.decode().split()
    users = client.run(['/bin/sh', '-c', 'getent passwd 1001; id sandboxadm']).stdout.decode().strip()

    desktop_deadline = time.monotonic() + DESKTOP_TIMEOUT
    while True:
        shell = client.run(['/usr/bin/pgrep', '-x', 'gnome-shell'], timeout=10)
        if shell.exitcode == 0:
            break
        if time.monotonic() > desktop_deadline:
            logger.warning('gnome-shell never started; screendump will show whatever is up')
            break
        time.sleep(3)
    time.sleep(10)
    QmpClient(config.out / 'qmp-verify.sock').screendump(seed / 'desktop-verify.ppm')

    report = {'guest_kernel': kernel, 'guest_os': os_name, 'links': links, 'users': users,
              'screendump': 'seed/desktop-verify.ppm'}
    (seed / 'verify.json').write_text(json.dumps(report, indent=2) + '\n')
    logger.info('%s', json.dumps(report, indent=2))
    return report


def _kill_pid(pid: int) -> None:
    """Terminate a QEMU process, escalating to SIGKILL."""
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return
    time.sleep(3)
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def _shutdown_and_freeze(config: BuildConfig, logger: logging.Logger) -> str:
    """Stop the verification VM, freeze the disk read-only, and return its sha256."""
    logger.info('== Shutdown and freeze ==')
    verify_pid = int((config.out / 'qemu.pid').read_text().strip())
    try:
        QgaClient(config.out / 'qga.sock', timeout=15).shutdown()
    except (QgaError, OSError) as error:
        # guest-shutdown often drops the connection as the agent exits; that is fine
        logger.info('guest-shutdown note: %s', error)
    for _ in range(30):
        if not _alive(verify_pid):
            break
        time.sleep(2)
    if _alive(verify_pid):
        logger.info('guest-shutdown did not stop the VM; trying ACPI powerdown')
        try:
            QmpClient(config.out / 'qmp-verify.sock', timeout=15).system_powerdown()
        except (QgaError, OSError) as error:
            logger.info('ACPI powerdown note: %s', error)
        for _ in range(30):
            if not _alive(verify_pid):
                break
            time.sleep(2)
    if _alive(verify_pid):
        logger.warning('guest did not stop in time; killing pid %d', verify_pid)
        _kill_pid(verify_pid)

    os.chmod(config.out / 'base.qcow2', 0o444)
    return _sha256(config.out / 'base.qcow2')


def _write_manifest(config: BuildConfig, logger: logging.Logger, iso_sha256: str, iso_verified: bool | None,
                    base_sha256: str, verify: dict[str, object]) -> None:
    """Write manifest.json next to the frozen base image."""
    qemu_version = subprocess.run(['qemu-system-x86_64', '--version'],
                                  capture_output=True, text=True, check=True).stdout.splitlines()[0]
    manifest = {
        'base_image': {'file': 'base.qcow2', 'sha256': base_sha256,
                       'bytes': (config.out / 'base.qcow2').stat().st_size, 'readonly': True},
        'iso': {'file': config.iso.name, 'sha256': iso_sha256, 'reference_sha256_verified': iso_verified},
        'qemu_version': qemu_version,
        'host_kernel': platform.release(),
        'guest_kernel': verify['guest_kernel'],
        'guest_os': verify['guest_os'],
        'guest_links': verify['links'],
        'build_time': datetime.datetime.now().isoformat(timespec='seconds'),
        'build_mode': 'autoinstall-unattended',
        'guest': {'users': [{'name': 'sandboxadm', 'sudo': True}, {'name': 'agent', 'uid': 1001, 'sudo': False}],
                  'services': ['qemu-guest-agent'], 'desktop': 'GNOME with GDM autologin as agent'},
        'admin_password_file': 'admin-password.txt (mode 600, lives on vmstore, not committed)',
    }
    (config.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    logger.info('%s', json.dumps(manifest, indent=2))


def build_base_image(config: BuildConfig) -> None:
    """Run the full build: seed, unattended install, offline verification, freeze, manifest.

    `config` (BuildConfig): Build parameters; paths must be absolute and already resolved.

    Raises:
        BuildError: A build step failed or the ISO reference hash did not match.
        subprocess.CalledProcessError: A host tool exited non-zero.
        QgaError: The guest agent protocol failed during verification.
        OSError: A filesystem or socket operation failed.
    """
    _check_prerequisites(config)
    (config.out / 'seed' / 'cidata').mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('build_base_image')
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(message)s')
    for handler in (logging.StreamHandler(), logging.FileHandler(config.out / 'seed' / 'build.log')):
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    try:
        if config.skip_install and (config.out / 'base.qcow2').is_file():
            logger.info('== skip_install set: reusing existing base.qcow2, skipping to verification ==')
            iso_sha256, iso_verified = _check_iso_hash(config, logger)
        else:
            iso_sha256, iso_verified = _prepare_seed(config, logger)
            _run_install(config, logger)
        verify = _verify_boot(config, logger)
        base_sha256 = _shutdown_and_freeze(config, logger)
        _write_manifest(config, logger, iso_sha256, iso_verified, base_sha256, verify)
    except (BuildError, subprocess.CalledProcessError, QgaError, OSError) as error:
        logger.error('build failed: %s', error)
        raise
    logger.info('== Base image frozen: %s/base.qcow2 ==', config.out)
