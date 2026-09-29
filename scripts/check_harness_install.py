"""
Install every harness distribution in an offline VM and report what runs.

    python3 scripts/check_harness_install.py --base-image <base.qcow2> --run-dir <local run dir>
        --harness-root <material root> --output-dir <report dir>

One overlay VM without network installs every selected harness through the same
bundle machinery the agent run uses, then runs the entry point that
sandbox.deploy.harness declares for that harness and records what it printed.
The API relay and the model key never take part, so an install test needs
neither a working endpoint nor a credential table. The VM and bundle plumbing is shared with
scripts/check_tool_install.py through scripts/common/.
"""

import argparse
from pathlib import Path
import shlex
import sys
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from common import (
    discover_specs,
    install_bundle,
    MaterialCheckError,
    offline_vm,
    run_commands,
    stamp,
    summarize,
    write_report,
)

from sandbox.deploy.deploy import DeployError, parse_spec
from sandbox.deploy.harness import distribution_dir, ENTRY_POINTS, HarnessError, install_script, NON_PAYLOAD_NAMES
from sandbox.deploy.qemu_vm import VmError
from sandbox.qga import QgaClient, QgaError

GUEST_HARNESS: Final = '/task/harness'
REPORT_NAME: Final = 'harness-install-report.json'
PROBE_ARGV: Final = (('--version',), ('--help',))
PROBE_GUEST_LIMIT: Final = 20
PROBE_TIMEOUT: Final = 45.0
OUTPUT_LIMIT: Final = 400
ERROR_LIMIT: Final = 1000

VERDICT_INSTALL_FAILED: Final = 'install_failed'
VERDICT_ENTRY_MISSING: Final = 'entry_missing'
VERDICT_ENTRY_FAILED: Final = 'entry_failed'
VERDICT_RUNNABLE: Final = 'runnable'


def _probe(qga: QgaClient, target: str) -> dict[str, object]:
    """
    Run one candidate entry point, trying --version then --help.

    The entry is invoked through `/bin/bash -c`, the layer the run wrapper uses, so an entry without a valid
    executable image is read as a shell script rather than failing at exec.

    Args:
        `qga` (QgaClient): Guest agent client used to run the probe.
        `target` (str): Absolute guest path of the candidate entry point.

    Returns:
        `record` (dict[str, object]): Command, exitcode, and the output tail, or an error.
    """
    commands = tuple(f'{shlex.quote(target)} {" ".join(argv)}' for argv in PROBE_ARGV)
    return run_commands(qga, commands, guest_timeout=PROBE_GUEST_LIMIT, call_timeout=PROBE_TIMEOUT, limit=OUTPUT_LIMIT)


def _entry_path(name: str, payload: str) -> str:
    """Return what to run inside /task/harness: the declared entry, or the bare payload itself."""
    return ENTRY_POINTS[name] or payload


def _inspect(qga: QgaClient, entry: str) -> dict[str, object]:
    """
    Summarize the installed tree, then run the harness entry point.

    Args:
        `qga` (QgaClient): Guest agent client used to inspect the guest.
        `entry` (str): Entry point path relative to /task/harness.

    Returns:
        `summary` (dict[str, object]): File count, total size, top-level names, and the probe outcome.
    """
    files = qga.run(['/bin/bash', '-c', f'find {GUEST_HARNESS} -type f | wc -l'], timeout=120.0)
    size = qga.run(['/bin/bash', '-c', f'du -sb {GUEST_HARNESS} | cut -f1'], timeout=120.0)
    top = qga.run(['/bin/bash', '-c', f'ls -1 {GUEST_HARNESS} | head -40'], timeout=120.0)
    target = f'{GUEST_HARNESS}/{entry}'
    present = qga.run(['/bin/bash', '-c', f'test -f {shlex.quote(target)}'], timeout=60.0).exitcode == 0
    return {
        'file_count': int(files.stdout.decode().strip() or 0),
        'total_bytes': int(size.stdout.decode().strip() or 0),
        'top_level': top.stdout.decode(errors='replace').split(),
        'entry': entry,
        'entry_present': present,
        'probe': _probe(qga, target) if present else None
    }


def _test_one(run_dir: Path, qga: QgaClient, harness_root: Path, installer: Path, spec: str) -> dict[str, object]:
    """
    Install one harness from a clean /task/harness and report the outcome.

    Args:
        `run_dir` (Path): VM run directory on local storage.
        `qga` (QgaClient): Guest agent client for the running VM.
        `harness_root` (Path): Directory of harness distributions.
        `installer` (Path): Shared install.sh script used for every bundle.
        `spec` (str): `<name>@<version>` spec to install.

    Returns:
        `record` (dict[str, object]): Spec, verdict, and the observed install details.
    """
    name, version = parse_spec(spec)
    record: dict[str, object] = {
        'spec': spec,
        'harness': name,
        'version': version
    }
    try:
        directory = distribution_dir(harness_root, name, version)
    except HarnessError as error:
        record.update({
            'verdict': VERDICT_INSTALL_FAILED,
            'error': summarize(str(error), ERROR_LIMIT, keep='head')
        })
        return record
    payload = next(path.name for path in sorted(directory.iterdir()) if path.is_file() and path.name not in NON_PAYLOAD_NAMES)
    record['payload'] = payload
    entry = _entry_path(name, payload)
    try:
        qga.run(['/bin/bash', '-c', f'rm -rf {GUEST_HARNESS}'], timeout=180.0)
        install_bundle(run_dir, qga, spec, directory, installer)
        record.update(_inspect(qga, entry))
    except (DeployError, QgaError, TimeoutError, OSError) as error:
        record.update({
            'verdict': VERDICT_INSTALL_FAILED,
            'error': summarize(str(error), ERROR_LIMIT, keep='head')
        })
        return record
    probe = record['probe']
    if not record['entry_present']:
        record['verdict'] = VERDICT_ENTRY_MISSING
    elif isinstance(probe, dict) and probe.get('exitcode') == 0:
        record['verdict'] = VERDICT_RUNNABLE
    else:
        record['verdict'] = VERDICT_ENTRY_FAILED
    return record


def check_harnesses(
    harness_root: Path,
    out_dir: Path,
    run_dir: Path,
    base_image: Path,
    harnesses: list[str] | None = None,
    smp: int = 4,
    memory: int = 8192,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False
) -> dict[str, object]:
    """
    Install every selected harness in one offline VM and report each outcome.

    Args:
        `harness_root` (Path): Directory of harness distributions, organized as <name>/<version>/<payload>,
            with the shared install.sh at its root.
        `out_dir` (Path): Host directory receiving harness-install-report.json.
        `run_dir` (Path): VM run directory on local storage.
        `base_image` (Path): Frozen base qcow2 the overlay is created from; must be on local storage.
        `harnesses` (list[str] | None): Specs as `<name>@<version>`; None scans harness_root.
        `smp` (int): Guest CPU count.
        `memory` (int): RAM in MiB.
        `accel` (str): `kvm` or `tcg`.
        `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live.
        `keep_vm` (bool): Keep the run directory after the test.

    Returns:
        `report` (dict[str, object]): The report, also written to out_dir/harness-install-report.json.

    Raises:
        MaterialCheckError: The harness set is empty, or one of its names has no declared entry point.
    """
    specs = list(harnesses) if harnesses else discover_specs(harness_root)
    for spec in specs:
        name, _ = parse_spec(spec)
        if name not in ENTRY_POINTS:
            raise MaterialCheckError(f'{name} has no declared entry point; add it to harness.ENTRY_POINTS')
    installer = install_script(harness_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        'harness_root': str(harness_root),
        'base_image': str(base_image),
        'run_dir': str(run_dir),
        'specs': specs,
        'results': [],
        'started_at': stamp(),
        'error': None
    }
    try:
        with offline_vm(base_image, run_dir, accel=accel, smp=smp, memory=memory, vnc=vnc, keep_vm=keep_vm) as qga:
            for position, spec in enumerate(specs, start=1):
                print(f'== harness {position}/{len(specs)}: {spec}', flush=True)
                report['results'].append(_test_one(run_dir, qga, harness_root, installer, spec))
    except (MaterialCheckError, HarnessError, DeployError, VmError, QgaError, OSError) as error:
        report['error'] = str(error)
    report['finished_at'] = stamp()
    write_report(out_dir, REPORT_NAME, report)
    return report


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', type=Path, required=True, help='frozen base qcow2, on local storage')
    parser.add_argument(
        '--run-dir',
        type=Path,
        required=True,
        help='VM run directory for transient state, on local storage; deleted after the test unless --keep-vm'
    )
    parser.add_argument(
        '--harness-root',
        type=Path,
        required=True,
        help='directory of harness distributions, organized as <name>/<version>/<payload>, with install.sh at its root'
    )
    parser.add_argument('--output-dir', type=Path, required=True, help='host directory receiving harness-install-report.json')
    parser.add_argument(
        '--harness',
        action='append',
        default=None,
        help='<name>@<version> spec to test, repeatable; defaults to every distribution under --harness-root'
    )
    parser.add_argument('--smp', type=int, default=4)
    parser.add_argument('--memory', type=int, default=8192)
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm', help='tcg works without KVM at a large slowdown')
    parser.add_argument(
        '--vnc',
        default=None,
        help='VNC display for watching the guest desktop live, e.g. :5 for host port 5905; a bare :N listens on loopback only'
    )
    parser.add_argument('--keep-vm', action='store_true')
    args = parser.parse_args()
    report_path = args.output_dir / REPORT_NAME
    try:
        report = check_harnesses(
            args.harness_root,
            args.output_dir,
            args.run_dir,
            args.base_image,
            harnesses=args.harness,
            smp=args.smp,
            memory=args.memory,
            accel=args.accel,
            vnc=args.vnc,
            keep_vm=args.keep_vm
        )
    except (MaterialCheckError, HarnessError, DeployError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    results = report['results']
    width = max((len(str(result['spec'])) for result in results), default=0)
    for result in results:
        print(f"{str(result['spec']):{width}}  {result['verdict']}")
    print(f'report: {report_path}', flush=True)
    if report['error']:
        print(f"error: {report['error']}", file=sys.stderr)
    if report['error'] or any(result['verdict'] == VERDICT_INSTALL_FAILED for result in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
