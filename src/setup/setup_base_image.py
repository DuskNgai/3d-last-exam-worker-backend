"""
Build the Ubuntu desktop base image for QEMU sandboxes via unattended autoinstall.

    python src/setup/setup_base_image.py --iso <iso> --out <out dir>

A cloud-init seed drives subiquity, the installed disk is verified with an
offline boot (QGA ping, OS facts, desktop screendump), then frozen read-only
with a manifest.json recording hashes and guest facts.
"""

import argparse
from dataclasses import dataclass
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.qemu_command import machine_flags
from sandbox.qga import QgaClient, QgaError, QmpClient

BASE_IMAGE_DIR: Final = Path(__file__).resolve().parent / 'base_image'
HARNESS_DEPS_FILE: Final = BASE_IMAGE_DIR / 'harness-deps.json'
HARNESS_DEPS_DIR: Final = '/opt/harness-deps'
INSTALL_COMPLETE_MARKER: Final = '/var/lib/sandbox-base-image/install-complete'
NPM_CLI: Final = '/usr/local/lib/node_modules/npm/bin/npm-cli.js'
TOOLS: Final = ('qemu-system-x86_64', 'qemu-img', 'genisoimage', 'isoinfo', 'openssl')
INSTALL_TIMEOUT_SECONDS: Final = 4500
STALL_WATCHDOG_SECONDS: Final = 900
TCG_TIMEOUT_FACTOR: Final = 4
POLL_SECONDS: Final = 20
QGA_TIMEOUT: Final = 300.0
DESKTOP_TIMEOUT: Final = 180.0


class BuildError(RuntimeError):
    """A build step failed."""


@dataclass(frozen=True)
class BuildConfig:
    """
    Parameters of one base image build.

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
    return lines[-1][: 160].decode(errors='replace') if lines else ''


def _capture_install_diagnostics(config: BuildConfig, serial_log: Path) -> tuple[str, int, int, str]:
    """
    Capture the install screen and return QEMU state and disk/log statistics.

    Args:
        `config` (BuildConfig): Build parameters locating the disk, seed directory, and QMP socket.
        `serial_log` (Path): Serial console log of the install boot.

    Returns:
        `diagnostics` (tuple[str, int, int, str]): QMP runstate, allocated disk bytes, serial log bytes,
        and guest disk write statistics.
    """
    image_stat = (config.out / 'base.qcow2').stat()
    log_stat = serial_log.stat() if serial_log.exists() else None
    disk_allocated = image_stat.st_blocks * 512
    serial_bytes = log_stat.st_size if log_stat else 0
    screenshot = config.out / 'seed' / 'install-stalled.ppm'
    qmp = QmpClient(config.out / 'qmp-install.sock', timeout=5)
    try:
        qmp.screendump(screenshot)
    except (QgaError, OSError, TimeoutError):
        pass
    try:
        runstate = qmp.query_status()
    except (QgaError, OSError, TimeoutError) as error:
        runstate = f'unavailable: {error}'
    try:
        write_bytes, write_operations = qmp.query_block_write_stats()
        write_stats = f'{write_bytes} bytes, {write_operations} operations'
    except (QgaError, OSError, TimeoutError) as error:
        write_stats = f'unavailable: {error}'
    return runstate, disk_allocated, serial_bytes, write_stats


def _is_name_list_map(value: object) -> bool:
    """Return whether value maps non-empty names to lists of non-empty strings."""
    return isinstance(value, dict) and all(
        isinstance(name, str) and name and isinstance(entries, list) and all(isinstance(entry, str) and entry
                                                                             for entry in entries)
        for name, entries in value.items()
    )


def _harness_deps() -> dict[str, list[str]]:
    """
    Read the staged harness dependency table.

    The file is hand-maintained, so a wrong shape is reported here as a build
    error instead of surfacing later as an attribute error.

    Returns:
        `packages` (dict[str, list[str]]): npm specifiers per harness.

    Raises:
        BuildError: The file is unreadable, is not JSON, or does not map harness names to string lists.
    """
    try:
        spec = json.loads(HARNESS_DEPS_FILE.read_text())
    except (OSError, ValueError) as error:
        raise BuildError(f'cannot read {HARNESS_DEPS_FILE}: {error}') from error
    packages = spec.get('packages') if isinstance(spec, dict) else None
    if not _is_name_list_map(packages):
        raise BuildError(f'{HARNESS_DEPS_FILE} must map harness names to lists of strings under "packages"')
    return packages


def _check_install_guest(client: QgaClient) -> None:
    """
    Require the install marker and the guest configuration needed by sandboxes.

    Runs against the installed system during the offline verification boot. The
    installer's live session has no guest agent and every fact checked here lives
    on the target rather than in the live environment.

    Args:
        `client` (QgaClient): Guest agent client for the verification boot.

    Raises:
        BuildError: A check failed, naming the check and what the guest reported.
    """
    checks = {
        'install marker': ['/usr/bin/test', '-f', INSTALL_COMPLETE_MARKER],
        'Ubuntu release': ['/bin/sh', '-c', '. /etc/os-release && test "$ID" = ubuntu'],
        'agent uid': ['/bin/sh', '-c', 'test "$(id -u agent)" = 1001'],
        'Node.js': ['/bin/sh', '-c', 'node --version'],
        'guest agent service': ['/bin/systemctl', 'is-enabled', 'qemu-guest-agent.service'],
        'desktop auto-login': ['/bin/sh', '-c', '/usr/bin/grep -Fq "AutomaticLogin=agent" /etc/gdm3/custom.conf'],
        'desktop first-run state': ['/usr/bin/test', '-f', '/home/agent/.config/gnome-initial-setup-done'],
        'libfuse2t64': ['/bin/sh', '-c', "dpkg-query -W -f='${Status}' libfuse2t64 | /usr/bin/grep -q '^install ok installed$'"],
    }
    packages = _harness_deps()
    for name in packages:
        checks[f'harness dependencies for {name}'] = ['/usr/bin/test', '-d', f'{HARNESS_DEPS_DIR}/{name}/node_modules']
    for name, command in checks.items():
        result = client.run(command, timeout=30.0)
        if result.exitcode != 0:
            detail = result.stderr.decode(errors='replace').strip() or result.stdout.decode(errors='replace').strip()
            if not detail:
                detail = f'exit {result.exitcode}'
            raise BuildError(f'install guest check failed for {name}: {detail}')


def _qemu_common(config: BuildConfig) -> list[str]:
    """
    QEMU flags shared by the install and verification boots.

    Args:
        `config` (BuildConfig): Build parameters supplying accel, CPU count, memory, and disk path.

    Returns:
        `flags` (list[str]): QEMU command-line flags common to both boots.
    """
    return machine_flags(config.accel, config.smp, config.memory, config.out / 'base.qcow2', config.out / 'qemu.pid')


def _check_prerequisites(config: BuildConfig) -> None:
    """
    Verify tools, KVM access when accel is kvm, and the ISO before touching anything.

    Args:
        `config` (BuildConfig): Build parameters naming the accelerator and the ISO.

    Raises:
        BuildError: A required tool is missing, /dev/kvm is not accessible under kvm accel, or the ISO file is absent.
    """
    for tool in TOOLS:
        if shutil.which(tool) is None:
            raise BuildError(f'missing tool: {tool}')
    if config.accel == 'kvm' and not (os.access('/dev/kvm', os.R_OK) and os.access('/dev/kvm', os.W_OK)):
        raise BuildError('no KVM access on /dev/kvm; pass --accel tcg to build without KVM')
    if not config.iso.is_file():
        raise BuildError(f'ISO not found: {config.iso}')


def _render_harness_deps() -> str:
    """
    Render the late-commands that stage each harness's npm dependency closure.

    The closures come from base_image/harness-deps.json: one npm install per
    harness into /opt/harness-deps/<name>. The guest install step links the
    matching tree into /task/harness, so one image serves every harness while a
    run installs one.

    Returns:
        `late_commands` (str): Seed late-command lines, one npm install per harness.

    Raises:
        BuildError: The dependency file is missing or malformed.
    """
    packages = _harness_deps()
    lines = []
    for name, entries in packages.items():
        prefix = f'{HARNESS_DEPS_DIR}/{name}'
        lines.append(f'    - curtin in-target -- mkdir -p {prefix}')
        lines.append(
            f'    - curtin in-target -- /usr/local/bin/node {NPM_CLI} install'
            f' --prefix {prefix} --no-audit --no-fund --loglevel=error {" ".join(entries)}'
        )
    return '\n'.join(lines)


def _check_iso_hash(config: BuildConfig, logger: logging.Logger) -> tuple[str, bool | None]:
    """
    Hash the ISO and compare with the caller-supplied reference when given.

    Args:
        `config` (BuildConfig): Build parameters naming the ISO and the reference hash.
        `logger` (logging.Logger): Build logger recording the computed hash.

    Returns:
        `hash_and_verified` (tuple[str, bool | None]): The ISO sha256 and whether it matched the reference;
        None when no reference was given.

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
    """
    Verify the ISO, extract the installer kernel, and render the cidata seed.

    Args:
        `config` (BuildConfig): Build parameters naming the ISO and the output directory.
        `logger` (logging.Logger): Build logger recording the ISO verification.

    Returns:
        `hash_and_verified` (tuple[str, bool | None]): The ISO sha256 and whether it matched the reference hash;
        None when no reference was given.

    Raises:
        BuildError: The ISO hash check failed or the casper kernel and initrd could not be extracted.
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
    privileged_hash = subprocess.run(['openssl', 'passwd', '-6', iso_sha256], capture_output=True, text=True, check=True).stdout.strip()
    cidata = seed / 'cidata'
    template = (BASE_IMAGE_DIR / 'user-data').read_text()
    (cidata /
     'user-data').write_text(template.replace('__PRIVILEGED_HASH__', privileged_hash).replace('__HARNESS_DEPS__', _render_harness_deps()))
    shutil.copy(BASE_IMAGE_DIR / 'meta-data', cidata / 'meta-data')
    subprocess.run([
        'genisoimage',
        '-output',
        str(seed / 'seed.iso'),
        '-volid',
        'cidata',
        '-joliet',
        '-rock',
        '-input-charset',
        'utf8',
        'user-data',
        'meta-data'
    ],
                   cwd=cidata,
                   check=True,
                   capture_output=True)
    return iso_sha256, verified


def _run_install(config: BuildConfig, logger: logging.Logger) -> None:
    """
    Boot the unattended installer and watch it to poweroff or a stuck state.

    The seed powers the guest off once the install finishes, which is the only
    completion signal available here: the live session runs no guest agent and
    the facts worth checking sit on the target. Those are checked on the
    verification boot instead.

    Args:
        `config` (BuildConfig): Build parameters supplying the accelerator and disk size.
        `logger` (logging.Logger): Build logger receiving progress and diagnostics.

    Raises:
        BuildError: The install timed out or stalled before the completion marker.
    """
    logger.info('== Create base disk ==')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(config.out / 'base.qcow2'), config.disk], check=True)

    timeout = INSTALL_TIMEOUT_SECONDS
    stall_watchdog = STALL_WATCHDOG_SECONDS
    if config.accel == 'tcg':
        timeout *= TCG_TIMEOUT_FACTOR
        stall_watchdog *= TCG_TIMEOUT_FACTOR
        logger.warning(
            'TCG build is hour-scale; timeouts relaxed by %dx. '
            'Prefer building on a KVM host and copying the frozen directory',
            TCG_TIMEOUT_FACTOR
        )
    logger.info('== Unattended install (timeout %ds) ==', timeout)
    seed = config.out / 'seed'
    subprocess.run([
        'qemu-system-x86_64',
        *_qemu_common(config),
        '-kernel',
        str(seed / 'vmlinuz'),
        '-initrd',
        str(seed / 'initrd'),
        '-append',
        'autoinstall ds=nocloud console=ttyS0,115200n8',
        '-drive',
        f'file={config.iso},media=cdrom,readonly=on',
        '-drive',
        f'file={seed}/seed.iso,media=cdrom,readonly=on',
        '-netdev',
        'user,id=n0',
        '-device',
        'virtio-net-pci,netdev=n0',
        '-serial',
        f'file:{seed}/serial-install.log',
        '-qmp',
        f'unix:{config.out}/qmp-install.sock,server=on,wait=off'
    ],
                   check=True)
    pid = int((config.out / 'qemu.pid').read_text().strip())
    deadline = time.monotonic() + timeout
    serial_log = seed / 'serial-install.log'
    last_progress = time.monotonic()
    previous_progress: tuple[tuple[int, int] | None, int, int] | None = None
    while _alive(pid):
        now = time.monotonic()
        if now > deadline:
            runstate, disk_allocated, serial_bytes, write_stats = _capture_install_diagnostics(config, serial_log)
            tail = '\n'.join(serial_log.read_bytes().decode(errors='replace').splitlines()[-15 :])
            raise BuildError(
                f'install timed out before completion marker; VM left running, pid {pid}; '
                f'QMP state={runstate}, disk allocated={disk_allocated} bytes, '
                f'guest disk writes={write_stats}, serial log={serial_bytes} bytes, '
                f'screenshot=seed/install-stalled.ppm\n{tail}'
            )

        image_stat = (config.out / 'base.qcow2').stat()
        log_stat = serial_log.stat() if serial_log.exists() else None
        try:
            disk_writes = QmpClient(config.out / 'qmp-install.sock', timeout=5).query_block_write_stats()
        except (QgaError, OSError, TimeoutError):
            disk_writes = None
        progress = (disk_writes, image_stat.st_blocks * 512, log_stat.st_size if log_stat else 0)
        if progress != previous_progress:
            previous_progress = progress
            last_progress = now
        elif now - last_progress > stall_watchdog:
            runstate, disk_allocated, serial_bytes, write_stats = _capture_install_diagnostics(config, serial_log)
            tail = '\n'.join(serial_log.read_bytes().decode(errors='replace').splitlines()[-15 :])
            raise BuildError(
                f'no disk-write or serial-log progress for {stall_watchdog // 60} minutes before '
                f'completion marker; VM left running, pid {pid}; QMP state={runstate}, '
                f'disk allocated={disk_allocated} bytes, guest disk writes={write_stats}, '
                f'serial log={serial_bytes} bytes, '
                f'screenshot=seed/install-stalled.ppm\n{tail}'
            )

        time.sleep(POLL_SECONDS)
        logger.info('%s', _tail_last_line(serial_log))

    logger.info('== Installer powered off ==')
    logger.info('%s', '\n'.join(serial_log.read_bytes().decode(errors='replace').splitlines()[-15 :]))


def _verify_boot(config: BuildConfig, logger: logging.Logger) -> dict[str, object]:
    """
    Boot the new disk offline, require its guest configuration, and collect facts.

    This is the first point where the checks of `_check_install_guest` can run,
    and the last chance to reject a disk before it is frozen. Every failure from
    the marker check on kills the VM before it propagates, so a broken
    verification never leaves a QEMU running against the disk being frozen.

    Args:
        `config` (BuildConfig): Build parameters locating the disk, seed directory, and QGA socket.
        `logger` (logging.Logger): Build logger recording the verification facts.

    Returns:
        `report` (dict[str, object]): The verification report, also written to seed/verify.json.

    Raises:
        BuildError: A guest check failed or the install completion marker is absent.
    """
    logger.info('== Offline verification boot ==')
    seed = config.out / 'seed'
    subprocess.run([
        'qemu-system-x86_64',
        *_qemu_common(config),
        '-nic',
        'none',
        '-chardev',
        f'socket,path={config.out}/qga.sock,server=on,wait=off,id=qga0',
        '-device',
        'virtio-serial-pci',
        '-device',
        'virtserialport,chardev=qga0,name=org.qemu.guest_agent.0',
        '-serial',
        f'file:{seed}/serial-verify.log',
        '-qmp',
        f'unix:{config.out}/qmp-verify.sock,server=on,wait=off'
    ],
                   check=True)
    verify_pid = int((config.out / 'qemu.pid').read_text().strip())
    logger.info('verify boot pid: %d', verify_pid)

    client = QgaClient(config.out / 'qga.sock', timeout=15)
    deadline = time.monotonic() + QGA_TIMEOUT
    while True:
        try:
            client.ping()
            break
        except (QgaError, OSError, TimeoutError) as error:
            if time.monotonic() > deadline:
                _kill_pid(verify_pid)
                raise BuildError('QGA never answered on the offline boot') from error
            time.sleep(5)
    logger.info('QGA ping OK')

    try:
        marker = client.run(['/usr/bin/test', '-f', INSTALL_COMPLETE_MARKER], timeout=15.0)
        if marker.exitcode != 0:
            raise BuildError('base image is missing the install completion marker; refusing verification')
        _check_install_guest(client)

        kernel = client.run(['/bin/uname', '-r']).stdout.decode().strip()
        os_name = client.run(['/bin/sh', '-c', '. /etc/os-release; echo "$PRETTY_NAME"']).stdout.decode().strip()
        links = client.run(['/bin/sh', '-c', "ip -o link show | awk '{print $2}'"]).stdout.decode().split()
        users = client.run(['/bin/sh', '-c', 'getent passwd 1001; id sandboxadm']).stdout.decode().strip()
        node = client.run(['/bin/sh', '-c', 'node --version']).stdout.decode().strip()
        fuse = client.run(['/bin/sh', '-c', "dpkg-query -W -f='${Status} ${Version}' libfuse2t64 2>&1"]).stdout.decode().strip()
        harness_deps = client.run(['/bin/ls', '-1', HARNESS_DEPS_DIR], timeout=30.0).stdout.decode().split()

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

        report = {
            'guest_kernel': kernel,
            'guest_os': os_name,
            'links': links,
            'users': users,
            'runtimes': {
                'node': node,
                'libfuse2t64': fuse,
                'harness_deps': harness_deps
            },
            'screendump': 'seed/desktop-verify.ppm'
        }
        (seed / 'verify.json').write_text(json.dumps(report, indent=2) + '\n')
        logger.info('%s', json.dumps(report, indent=2))
        return report
    except (BuildError, QgaError, OSError, TimeoutError):
        # Past the boot every failure would otherwise leave the daemonized QEMU running on the disk that is
        # about to be frozen, so it is killed here whatever the step that failed.
        _kill_pid(verify_pid)
        raise


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
    """
    Stop the verification VM, freeze the disk read-only, and return its sha256.

    Args:
        `config` (BuildConfig): Build parameters locating the disk and the verification sockets.
        `logger` (logging.Logger): Build logger recording shutdown progress.

    Returns:
        `base_sha256` (str): Hex sha256 of the frozen base.qcow2.
    """
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


def _reuse_existing_disk(config: BuildConfig, logger: logging.Logger) -> None:
    """
    Accept an existing base.qcow2 for verification and reopen it for writing.

    The counterpart of `_shutdown_and_freeze`: the verification boot writes to the
    disk, so a frozen image has to be made writable again before it can be checked.

    Args:
        `config` (BuildConfig): Build parameters locating the existing base.qcow2.
        `logger` (logging.Logger): Build logger noting the reopened image.

    Raises:
        BuildError: There is no base.qcow2 to reuse.
    """
    image = config.out / 'base.qcow2'
    if not image.is_file():
        raise BuildError(f'--skip-install was given but {image} does not exist; drop the flag to install from scratch')
    os.chmod(image, 0o644)
    logger.info('== skip_install set: verifying %s, reopened for writing ==', image)


def _write_manifest(
    config: BuildConfig, logger: logging.Logger, iso_sha256: str, iso_verified: bool | None, base_sha256: str, verify: dict[str, object]
) -> None:
    """
    Write manifest.json next to the frozen base image.

    Args:
        `config` (BuildConfig): Build parameters naming the ISO and the output directory.
        `logger` (logging.Logger): Build logger recording the manifest.
        `iso_sha256` (str): SHA256 of the install ISO.
        `iso_verified` (bool | None): Whether the ISO matched the reference hash; None when no check was made.
        `base_sha256` (str): SHA256 of the frozen base image.
        `verify` (dict[str, object]): Verification report collected on the offline boot.
    """
    qemu_version = subprocess.run(['qemu-system-x86_64', '--version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    manifest = {
        'base_image': {
            'file': 'base.qcow2',
            'sha256': base_sha256,
            'bytes': (config.out / 'base.qcow2').stat().st_size,
            'readonly': True
        },
        'iso': {
            'file': config.iso.name,
            'sha256': iso_sha256,
            'reference_sha256_verified': iso_verified
        },
        'qemu_version': qemu_version,
        'host_kernel': platform.release(),
        'guest_kernel': verify['guest_kernel'],
        'guest_os': verify['guest_os'],
        'guest_links': verify['links'],
        'guest_runtimes': verify['runtimes'],
        'build_time': datetime.datetime.now().isoformat(timespec='seconds'),
        'build_mode': 'autoinstall-unattended',
        'guest': {
            'users': [{
                'name': 'sandboxadm',
                'sudo': True
            }, {
                'name': 'agent',
                'uid': 1001,
                'sudo': False
            }],
            'services': ['qemu-guest-agent'],
            'desktop': 'GNOME with GDM autologin as agent'
        },
        'privileged_account': 'sandboxadm; password = the ISO sha256',
    }
    (config.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    logger.info('%s', json.dumps(manifest, indent=2))


def setup_base_image(config: BuildConfig) -> None:
    """
    Run the full build: seed, unattended install, offline verification, freeze, manifest.

    Args:
        `config` (BuildConfig): Build parameters; paths must be absolute and already resolved.

    Raises:
        BuildError: A build step failed or the ISO reference hash did not match.
        subprocess.CalledProcessError: A host tool exited non-zero.
        QgaError: The guest agent protocol failed during verification.
        OSError: A filesystem or socket operation failed.
    """
    _check_prerequisites(config)
    (config.out / 'seed' / 'cidata').mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('setup_base_image')
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(message)s')
    for handler in (logging.StreamHandler(), logging.FileHandler(config.out / 'seed' / 'build.log')):
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    try:
        if config.skip_install:
            _reuse_existing_disk(config, logger)
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


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True, help='path to the Ubuntu desktop ISO')
    parser.add_argument(
        '--iso-sha256',
        default=None,
        help='official sha256 for the ISO; the build fails when it does not match. '
        'Without it the computed hash is only recorded in the manifest'
    )
    parser.add_argument('--out', type=Path, required=True, help='output directory for base.qcow2 and manifest.json')
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm', help='tcg works without KVM but the install takes hour-scale')
    parser.add_argument('--smp', type=int, default=4)
    parser.add_argument('--memory', type=int, default=8192)
    parser.add_argument('--disk', default='64G')
    parser.add_argument('--skip-install', action='store_true', help='reuse the existing base.qcow2 and skip to verification')
    args = parser.parse_args()

    config = BuildConfig(
        iso=args.iso.resolve(),
        iso_sha256=args.iso_sha256,
        out=args.out.resolve(),
        accel=args.accel,
        smp=args.smp,
        memory=args.memory,
        disk=args.disk,
        skip_install=args.skip_install
    )
    try:
        setup_base_image(config)
    except (BuildError, subprocess.CalledProcessError, QgaError, OSError) as error:
        print(f'build failed: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
