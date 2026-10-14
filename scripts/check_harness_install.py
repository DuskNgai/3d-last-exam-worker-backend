"""
Install every harness distribution in an offline VM and report what runs.

    python scripts/check_harness_install.py --base-image <base image> --run-dir <run dir>
        --harness-root <harness root> --output-dir <output dir>

One overlay VM without network installs every harness through the same
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
    write_report,
)

from sandbox.deploy.deploy import parse_spec
from sandbox.deploy.harness import distribution_dir, ENTRY_POINTS, install_script, NON_PAYLOAD_NAMES
from sandbox.qga import QgaClient

GUEST_HARNESS: Final = '/task/harness'
REPORT_NAME: Final = 'harness-install-report.json'
PROBE_ARGV: Final = (('--version',), ('--help',))
PROBE_GUEST_LIMIT: Final = 20
PROBE_TIMEOUT: Final = 45.0
OUTPUT_LIMIT: Final = 400
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

    Raises:
        MaterialCheckError: The installed harness cannot be inspected.
    """
    files = qga.run(['/bin/bash', '-o', 'pipefail', '-c', f'find {GUEST_HARNESS} -type f | wc -l'], timeout=120.0)
    size = qga.run(['du', '-sb', GUEST_HARNESS], timeout=120.0)
    top = qga.run(['ls', '-1', GUEST_HARNESS], timeout=120.0)
    if files.exitcode != 0 or size.exitcode != 0 or top.exitcode != 0:
        raise MaterialCheckError(f'cannot inspect installed harness at {GUEST_HARNESS}')
    target = f'{GUEST_HARNESS}/{entry}'
    present = qga.run(['/bin/bash', '-c', f'test -f {shlex.quote(target)}'], timeout=60.0).exitcode == 0
    return {
        'file_count': int(files.stdout.decode().strip()),
        'total_bytes': int(size.stdout.decode().split(maxsplit=1)[0]),
        'top_level': top.stdout.decode(errors='replace').splitlines()[:40],
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

    Raises:
        MaterialCheckError: The previous harness directory cannot be cleared.
    """
    name, version = parse_spec(spec)
    record: dict[str, object] = {
        'spec': spec,
        'harness': name,
        'version': version
    }
    directory = distribution_dir(harness_root, name, version)
    payload = next(path.name for path in sorted(directory.iterdir()) if path.is_file() and path.name not in NON_PAYLOAD_NAMES)
    record['payload'] = payload
    entry = _entry_path(name, payload)
    cleared = qga.run(['/bin/bash', '-c', f'rm -rf {GUEST_HARNESS}'], timeout=180.0)
    if cleared.exitcode != 0:
        raise MaterialCheckError(f'cannot clear {GUEST_HARNESS}')
    install_bundle(run_dir, qga, spec, directory, installer)
    record.update(_inspect(qga, entry))
    probe = record['probe']
    if not record['entry_present']:
        record['verdict'] = VERDICT_ENTRY_MISSING
    elif isinstance(probe, dict) and probe['exitcode'] == 0:
        record['verdict'] = VERDICT_RUNNABLE
    else:
        record['verdict'] = VERDICT_ENTRY_FAILED
    return record


def check_harnesses(
    harness_root: Path,
    out_dir: Path,
    run_dir: Path,
    base_image: Path,
    smp: int = 4,
    memory: int = 8192,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False
) -> dict[str, object]:
    """
    Install every harness under the root in one offline VM and report each outcome.

    Args:
        `harness_root` (Path): Directory of harness distributions, organized as <name>/<version>/<payload>,
            with the shared install.sh at its root.
        `out_dir` (Path): Host directory receiving harness-install-report.json.
        `run_dir` (Path): VM run directory on local storage.
        `base_image` (Path): Frozen base qcow2 the overlay is created from; must be on local storage.
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
    specs = discover_specs(harness_root)
    if not specs:
        raise MaterialCheckError('at least one harness is required')
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
        'started_at': stamp()
    }
    try:
        with offline_vm(base_image, run_dir, accel=accel, smp=smp, memory=memory, vnc=vnc, keep_vm=keep_vm) as qga:
            for position, spec in enumerate(specs, start=1):
                print(f'== harness {position}/{len(specs)}: {spec}', flush=True)
                report['results'].append(_test_one(run_dir, qga, harness_root, installer, spec))
    finally:
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
    report = check_harnesses(
        args.harness_root,
        args.output_dir,
        args.run_dir,
        args.base_image,
        smp=args.smp,
        memory=args.memory,
        accel=args.accel,
        vnc=args.vnc,
        keep_vm=args.keep_vm
    )
    results = report['results']
    width = max((len(str(result['spec'])) for result in results), default=0)
    for result in results:
        print(f"{str(result['spec']):{width}}  {result['verdict']}")
    print(f'report: {report_path}', flush=True)


if __name__ == '__main__':
    main()
