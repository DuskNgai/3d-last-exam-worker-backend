"""
Shared plumbing for the offline material checks.

check_harness_install.py and check_tool_install.py each boot one network-less VM from the frozen base image,
push every selected material bundle through the same bundle machinery a real agent run uses, and record what
came back. What each check probes afterwards differs, so only the parts that do not differ live here.
"""

from collections.abc import Generator
import contextlib
import datetime
import json
from pathlib import Path
import shlex
import shutil
import sys
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from sandbox.deploy.deploy import BundleConfig, GUEST_BUNDLES, install_bundles, parse_spec
from sandbox.deploy.qemu_vm import create_overlay, qga_socket_path, run_lock, start_vm, stop_vm, wait_ready
from sandbox.qga import QgaClient, QgaError

CLEAN_TIMEOUT: Final = 180.0


class MaterialCheckError(RuntimeError):
    """The material check could not reach or finish an item."""


def stamp() -> str:
    """
    Return the timestamp the reports record.

    Returns:
        `stamp` (str): Local time in ISO format, second precision.
    """
    return datetime.datetime.now().isoformat(timespec='seconds')


def summarize(text: str, limit: int, *, keep: str = 'tail') -> str:
    """
    Trim captured text for a report.

    Args:
        `text` (str): Captured output or an exception message.
        `limit` (int): Characters to keep.
        `keep` (str): `tail` keeps the end, where a process explains its own failure; `head` keeps the start,
            where a wrapped message names the step that failed before the output it carries.

    Returns:
        `trimmed` (str): The trimmed text, with an ellipsis when anything was dropped.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return f'...{text[-limit:]}' if keep == 'tail' else f'{text[:limit]}...'


def discover_specs(root: Path) -> list[str]:
    """
    Return every `<name>@<version>` spec under a material root, sorted.

    Args:
        `root` (Path): Material root organized as <name>/<version>/<payload>.

    Returns:
        `specs` (list[str]): Every spec found, sorted by name and then by version.

    Raises:
        MaterialCheckError: The root pairs no name directory with a version directory.
    """
    specs = [
        f'{name_dir.name}@{version_dir.name}' for name_dir in sorted(path for path in root.iterdir() if path.is_dir())
        for version_dir in sorted(path for path in name_dir.iterdir() if path.is_dir())
    ]
    if not specs:
        raise MaterialCheckError(f'no material distribution under {root}')
    return specs


@contextlib.contextmanager
def offline_vm(
    base_image: Path,
    run_dir: Path,
    *,
    accel: str = 'kvm',
    smp: int = 4,
    memory: int = 8192,
    vnc: str | None = None,
    keep_vm: bool = False
) -> Generator[QgaClient]:
    """
    Boot one network-less VM from the frozen base image and tear it down afterwards.

    Args:
        `base_image` (Path): Frozen base qcow2 the overlay is created from; must be on local storage.
        `run_dir` (Path): VM run directory on local storage.
        `accel` (str): `kvm` or `tcg`.
        `smp` (int): Guest CPU count.
        `memory` (int): RAM in MiB.
        `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live.
        `keep_vm` (bool): Keep the run directory after the check.

    Yields:
        `qga` (QgaClient): Client for the running guest agent.
    """
    with run_lock(run_dir):
        try:
            create_overlay(run_dir, base_image)
            start_vm(run_dir, accel=accel, smp=smp, memory=memory, network='none', vnc=vnc)
            wait_ready(run_dir)
            yield QgaClient(qga_socket_path(run_dir))
        finally:
            stop_vm(run_dir)
            if not keep_vm:
                shutil.rmtree(run_dir, ignore_errors=True)


def install_bundle(run_dir: Path, qga: QgaClient, spec: str, directory: Path, script: Path) -> None:
    """
    Push one material bundle through the same machinery an agent run uses.

    Args:
        `run_dir` (Path): VM run directory holding the QGA socket.
        `qga` (QgaClient): Guest agent client, used to clear the guest bundle directory first.
        `spec` (str): `<name>@<version>` spec naming the bundle and the version its script receives.
        `directory` (Path): Host material directory to pack.
        `script` (Path): Install script the bundle runs, uploaded into it as install.sh.

    Raises:
        DeployError: The bundle could not be packed, uploaded, extracted, or installed.
        QgaError: The guest agent connection dropped.
    """
    name, version = parse_spec(spec)
    target = shlex.quote(f'{GUEST_BUNDLES}/{name}')
    qga.run(['/bin/bash', '-c', f'rm -rf {target}'], timeout=CLEAN_TIMEOUT)
    install_bundles(run_dir, [BundleConfig(name=name, path=directory, script=script, version=version)])


def run_commands(qga: QgaClient, commands: tuple[str, ...], *, guest_timeout: int, call_timeout: float, limit: int) -> dict[str, object]:
    """
    Run candidate commands in the guest as the agent user; the first one that exits 0 wins.

    Args:
        `qga` (QgaClient): Guest agent client used to run the commands.
        `commands` (tuple[str, ...]): Shell commands to try in order.
        `guest_timeout` (int): Seconds each candidate may run inside the guest.
        `call_timeout` (float): Host-side timeout for the agent call.
        `limit` (int): Characters of output to keep per candidate.

    Returns:
        `record` (dict[str, object]): The command tried, its exitcode, and the output tail, or the error that
        stopped it.
    """
    record: dict[str, object] = {}
    for command in commands:
        guest = f'timeout {guest_timeout} /usr/sbin/runuser -u agent -- /bin/bash -c {shlex.quote(command)} < /dev/null 2>&1'
        try:
            result = qga.run(['/bin/bash', '-c', guest], timeout=call_timeout)
        except (QgaError, TimeoutError, OSError) as error:
            # The candidate never ran, so a missing exit code must not read as a zero, and the record must not
            # carry an earlier candidate's output next to this candidate's error.
            return {
                'command': command,
                'exitcode': None,
                'error': summarize(str(error), limit)
            }
        record.update({
            'command': command,
            'exitcode': result.exitcode,
            'output': summarize(result.stdout.decode(errors='replace'), limit)
        })
        if result.exitcode == 0:
            return record
    return record


def write_report(out_dir: Path, filename: str, report: dict[str, object]) -> Path:
    """
    Write a check report as JSON.

    Args:
        `out_dir` (Path): Host directory receiving the report; created when missing.
        `filename` (str): Report file name.
        `report` (dict[str, object]): Report content.

    Returns:
        `path` (Path): Path of the written report.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return path
