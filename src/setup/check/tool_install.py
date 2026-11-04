"""
Install every tool bundle in an offline VM and report what each one answers.

    python src/setup/check/tool_install.py --config src/configuration.yaml
        --run-dir <run dir> --output-dir <output dir>

One overlay VM without network installs every tool through the same
bundle machinery the agent run uses, so installation is judged by each bundle's
own install.sh, whose self-check aborts the install on any mismatch. Install
output is forwarded to this process's stdout and stderr. Once every
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from configuration import read_materials_directory
from setup.common import (
    discover_base_image,
    discover_specs,
    install_bundle,
    MaterialCheckError,
    offline_vm,
    run_commands,
    stamp,
    write_report,
)

from sandbox.deploy.deploy import parse_spec
from sandbox.qga import QgaClient

REPORT_NAME: Final = 'tool-install-report.json'
PROBE_GUEST_LIMIT: Final = 120
PROBE_TIMEOUT: Final = 180.0
OUTPUT_LIMIT: Final = 400
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


def _select(tools_root: Path) -> list[str]:
    """
    Return every spec under the root, in table order.

    Args:
        `tools_root` (Path): Root of the tool material, organized as <name>/install.sh and <name>/<version>/.

    Returns:
        `specs` (list[str]): `<name>@<version>` specs in install order.

    Raises:
        MaterialCheckError: The root holds no tool, holds multiple versions of one tool, pairs python without
            mujoco or vice versa, misses an install.sh, or names a tool without a declared probe.
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
    if not discovered:
        raise MaterialCheckError('at least one tool is required')
    if ('python' in discovered) != ('mujoco' in discovered):
        raise MaterialCheckError('python and mujoco must both be present: mujoco installs into the python tool')
    unknown = sorted(name for name in discovered if name not in PROBES)
    if unknown:
        raise MaterialCheckError(f'tools without a declared probe: {unknown}; add them to PROBES')
    for name in discovered:
        script = tools_root / name / 'install.sh'
        if not script.is_file():
            raise MaterialCheckError(f'tool material must carry install.sh: {script}')
    return [discovered[name] for name in PROBES if name in discovered]


def _install_one(run_dir: Path, qga: QgaClient, tools_root: Path, spec: str) -> dict[str, object]:
    """
    Install one tool bundle and record what its installer reported.

    Args:
        `run_dir` (Path): VM run directory on local storage.
        `qga` (QgaClient): Guest agent client for the running VM.
        `tools_root` (Path): Root of the tool material.
        `spec` (str): `<name>@<version>` spec to install.

    Returns:
        `record` (dict[str, object]): Spec, payloads, and successful installation status.
    """
    name, version = parse_spec(spec)
    record: dict[str, object] = {
        'spec': spec,
        'tool': name,
        'version': version
    }
    directory = (tools_root / name).resolve()
    script = (directory / 'install.sh').resolve()
    record['payloads'] = sorted(path.name for path in (directory / version).iterdir())
    install_bundle(run_dir, qga, spec, directory, script)
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
    record['verdict'] = VERDICT_USABLE if probe['exitcode'] == 0 else VERDICT_PROBE_FAILED


def check_tools(
    tools_root: Path,
    out_dir: Path,
    run_dir: Path,
    base_image: Path,
    cpus: int = 4,
    memory_mb: int = 8192,
    accel: str = 'kvm',
    vnc: str | None = None,
    keep_vm: bool = False
) -> dict[str, object]:
    """
    Install every tool under the root in one offline VM, then probe each Python interface.

    Args:
        `tools_root` (Path): Root of the tool material, organized as <name>/install.sh and <name>/<version>/.
        `out_dir` (Path): Host directory receiving tool-install-report.json.
        `run_dir` (Path): VM run directory on local storage.
        `base_image` (Path): Frozen base qcow2 the overlay is created from; must be on local storage.
        `cpus` (int): Guest CPU count.
        `memory_mb` (int): RAM in MiB.
        `accel` (str): `kvm` or `tcg`.
        `vnc` (str | None): Optional VNC display such as `:5` to watch the guest desktop live.
        `keep_vm` (bool): Keep the run directory after the check.

    Returns:
        `report` (dict[str, object]): The report, also written to out_dir/tool-install-report.json.

    Raises:
        MaterialCheckError: The tool set is empty, or a tool has no material or no declared probe.
    """
    specs = _select(tools_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        'tools_root': str(tools_root),
        'base_image': str(base_image),
        'run_dir': str(run_dir),
        'specs': specs,
        'results': [],
        'started_at': stamp()
    }
    try:
        with offline_vm(base_image, run_dir, accel=accel, cpus=cpus, memory_mb=memory_mb, vnc=vnc, keep_vm=keep_vm) as qga:
            for position, spec in enumerate(specs, start=1):
                print(f'== tool {position}/{len(specs)}: {spec}', flush=True)
                report['results'].append(_install_one(run_dir, qga, tools_root, spec))
            for record in report['results']:
                _probe_one(qga, record)
    finally:
        report['finished_at'] = stamp()
        write_report(out_dir, REPORT_NAME, report)
    return report


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Worker configuration file')
    parser.add_argument(
        '--run-dir',
        type=Path,
        required=True,
        help='VM run directory for transient state, on local storage; deleted after the check unless --keep-vm; '
        'keep it outside the configured run_directory'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        required=True,
        help='host directory receiving tool-install-report.json; keep it outside the configured output_directory'
    )
    parser.add_argument('--cpus', type=int, default=4)
    parser.add_argument('--memory-mb', type=int, default=8192)
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm', help='tcg works without KVM at a large slowdown')
    parser.add_argument(
        '--vnc',
        default=None,
        help='VNC display for watching the guest desktop live, e.g. :5 for host port 5905; a bare :N listens on loopback only'
    )
    parser.add_argument('--keep-vm', action='store_true')
    args = parser.parse_args()
    materials = read_materials_directory(args.config)
    report_path = args.output_dir / REPORT_NAME
    report = check_tools(
        materials / 'tools',
        args.output_dir,
        args.run_dir,
        discover_base_image(materials),
        cpus=args.cpus,
        memory_mb=args.memory_mb,
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
