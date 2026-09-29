"""
Install every tool bundle in an offline VM and report what each one answers.

    python3 scripts/check_tool_install.py --base-image <base.qcow2> --run-dir <local run dir>
        --tools-root <material root> --output-dir <report dir>

One overlay VM without network installs every selected tool through the same
bundle machinery the agent run uses, so installation is judged by each bundle's
own install.sh, whose self-check aborts the install on any mismatch. Install
output goes to this process's stdout rather than to the report. Once every
install has finished, a second pass asks each tool on its own Python interface,
which is the one probe the installers do not run for each other. Table order is
install order: mujoco pushes its wheel into the interpreter the python tool
unpacks, so python lands first and that second pass has to come after both. The
API relay and the model key never take part, so a tool check needs neither a
working endpoint nor a credential table.
"""

import argparse
from pathlib import Path
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
from sandbox.deploy.qemu_vm import VmError
from sandbox.qga import QgaClient, QgaError

REPORT_NAME: Final = 'tool-install-report.json'
PROBE_GUEST_LIMIT: Final = 120
PROBE_TIMEOUT: Final = 180.0
OUTPUT_LIMIT: Final = 400
ERROR_LIMIT: Final = 1000

VERDICT_INSTALL_FAILED: Final = 'install_failed'
VERDICT_PROBE_FAILED: Final = 'probe_failed'
VERDICT_USABLE: Final = 'usable'
VERDICT_INSTALLED: Final = 'installed'

# One table holds the tool set, its install order, and the Python interface each tool is asked to answer on.
# Table order is install order: mujoco's installer pushes its wheel into the interpreter the python tool
# unpacks, so python has to land first. An empty tuple means the tool exposes no Python interface to probe;
# sdf-view is a viewer whose installer already proves the binary runs. Candidates are tried in order and the
# first that exits 0 is the evidence, which keeps the freecad entry tolerant of where the AppImage hides its
# console interpreter. A tool named here but absent from the material root is a finding, not a guess.
PROBES: Final[dict[str, tuple[str, ...]]] = {
    'python': ('/task/software/python/bin/python -c "import mujoco, numpy, scipy, trimesh, manifold3d, PIL"',),
    'mujoco': ('/task/software/python/bin/python -c "import mujoco; print(mujoco.__version__)"',),
    'blender': ('/task/software/blender/blender --background --python-expr "import bpy; print(bpy.app.version_string)"',),
    'freecad': (
        '/task/software/freecad/squashfs-root/usr/bin/freecadcmd -c "import FreeCAD; print(FreeCAD.Version()[0])"',
        '/task/software/freecad/squashfs-root/usr/bin/python -c "import FreeCAD; print(FreeCAD.Version()[0])"'
    ),
    'sdf-view': ()
}


def _select(tools_root: Path, tools: list[str] | None) -> list[str]:
    """
    Return the specs to check, in table order.

    Args:
        `tools_root` (Path): Root of the tool material, organized as <name>/install.sh and <name>/<version>/.
        `tools` (list[str] | None): Tool names to check; None selects every tool under the root.

    Returns:
        `specs` (list[str]): `<name>@<version>` specs in install order.

    Raises:
        MaterialCheckError: A selected tool has no material, a tool holds more than one version directory, or a
            tool under the root has no declared probe.
    """
    found: dict[str, list[str]] = {}
    for spec in discover_specs(tools_root):
        name, _, version = spec.partition('@')
        found.setdefault(name, []).append(version)
    repeated = sorted(name for name, versions in found.items() if len(versions) > 1)
    if repeated:
        raise MaterialCheckError(f'tools holding more than one version directory: {repeated}; a tool installs into one place')
    discovered = {
        name: f'{name}@{versions[0]}' for name, versions in found.items()
    }
    selected = tools if tools is not None else list(discovered)
    missing = sorted(name for name in selected if name not in discovered)
    if missing:
        raise MaterialCheckError(f'no tool material under {tools_root} for: {missing}')
    unknown = sorted(name for name in set(selected) | set(discovered) if name not in PROBES)
    if unknown:
        raise MaterialCheckError(f'tools without a declared probe: {unknown}; add them to PROBES')
    return [discovered[name] for name in PROBES if name in selected]


def _install_one(run_dir: Path, qga: QgaClient, tools_root: Path, spec: str) -> dict[str, object]:
    """
    Install one tool bundle and record what its installer reported.

    Args:
        `run_dir` (Path): VM run directory on local storage.
        `qga` (QgaClient): Guest agent client for the running VM.
        `tools_root` (Path): Root of the tool material.
        `spec` (str): `<name>@<version>` spec to install.

    Returns:
        `record` (dict[str, object]): Spec, payloads, and either the install error or a pending verdict.
    """
    name, version = parse_spec(spec)
    record: dict[str, object] = {
        'spec': spec,
        'tool': name,
        'version': version
    }
    directory = (tools_root / name).resolve()
    script = (directory / 'install.sh').resolve()
    if not script.is_file():
        record.update({
            'verdict': VERDICT_INSTALL_FAILED,
            'error': f'tool material must carry install.sh: {script}'
        })
        return record
    try:
        record['payloads'] = sorted(path.name for path in (directory / version).iterdir())
        install_bundle(run_dir, qga, spec, directory, script)
    except (DeployError, QgaError, TimeoutError, OSError) as error:
        record.update({
            'verdict': VERDICT_INSTALL_FAILED,
            'error': summarize(str(error), ERROR_LIMIT, keep='head')
        })
        return record
    record['install'] = 'ok'
    return record


def _probe_one(qga: QgaClient, record: dict[str, object]) -> None:
    """
    Ask one installed tool on its declared Python interface and settle its verdict.

    Args:
        `qga` (QgaClient): Guest agent client for the running VM.
        `record` (dict[str, object]): Record returned by _install_one, updated in place.
    """
    commands = PROBES[str(record['tool'])]
    if not commands:
        record['probe'] = None
        record['verdict'] = VERDICT_INSTALLED
        return
    probe = run_commands(qga, commands, guest_timeout=PROBE_GUEST_LIMIT, call_timeout=PROBE_TIMEOUT, limit=OUTPUT_LIMIT)
    record['probe'] = probe
    record['verdict'] = VERDICT_USABLE if probe.get('exitcode') == 0 else VERDICT_PROBE_FAILED


def check_tools(
    tools_root: Path,
    out_dir: Path,
    run_dir: Path,
    base_image: Path,
    tools: list[str] | None = None,
    smp: int = 4,
    memory: int = 8192,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False
) -> dict[str, object]:
    """
    Install every selected tool in one offline VM, then probe each Python interface.

    Args:
        `tools_root` (Path): Root of the tool material, organized as <name>/install.sh and <name>/<version>/.
        `out_dir` (Path): Host directory receiving tool-install-report.json.
        `run_dir` (Path): VM run directory on local storage.
        `base_image` (Path): Frozen base qcow2 the overlay is created from; must be on local storage.
        `tools` (list[str] | None): Tool names to check; None selects every tool under the root.
        `smp` (int): Guest CPU count.
        `memory` (int): RAM in MiB.
        `accel` (str): `kvm` or `tcg`.
        `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live.
        `keep_vm` (bool): Keep the run directory after the check.

    Returns:
        `report` (dict[str, object]): The report, also written to out_dir/tool-install-report.json.

    Raises:
        MaterialCheckError: The tool set is empty, or a tool has no material or no declared probe.
    """
    specs = _select(tools_root, tools)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        'tools_root': str(tools_root),
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
                print(f'== tool {position}/{len(specs)}: {spec}', flush=True)
                report['results'].append(_install_one(run_dir, qga, tools_root, spec))
            # A second pass rather than a probe per install: python's probe imports the wheel mujoco installs into it.
            for record in report['results']:
                if record.get('verdict') != VERDICT_INSTALL_FAILED:
                    _probe_one(qga, record)
    except (MaterialCheckError, DeployError, VmError, QgaError, OSError) as error:
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
        help='VM run directory for transient state, on local storage; deleted after the check unless --keep-vm'
    )
    parser.add_argument(
        '--tools-root', type=Path, required=True, help='root of the tool material, organized as <name>/install.sh and <name>/<version>/'
    )
    parser.add_argument('--output-dir', type=Path, required=True, help='host directory receiving tool-install-report.json')
    parser.add_argument(
        '--tool',
        action='append',
        default=None,
        help='tool name to check, repeatable; defaults to every tool under --tools-root. python and mujoco '
        'depend on each other, so select both or neither'
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
        report = check_tools(
            args.tools_root,
            args.output_dir,
            args.run_dir,
            args.base_image,
            tools=args.tool,
            smp=args.smp,
            memory=args.memory,
            accel=args.accel,
            vnc=args.vnc,
            keep_vm=args.keep_vm
        )
    except (MaterialCheckError, DeployError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    results = report['results']
    width = max((len(str(result['spec'])) for result in results), default=0)
    # An aborted run can leave records whose install finished but whose probe never ran; they read as failed.
    for result in results:
        print(f"{str(result['spec']):{width}}  {result.get('verdict', VERDICT_INSTALL_FAILED)}")
    print(f'report: {report_path}', flush=True)
    if report['error']:
        print(f"error: {report['error']}", file=sys.stderr)
    if report['error'] or any(result.get('verdict', VERDICT_INSTALL_FAILED) == VERDICT_INSTALL_FAILED for result in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
