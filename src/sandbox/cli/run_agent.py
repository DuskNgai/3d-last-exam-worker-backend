"""
External CLI for running an agent task inside a gateway-mode VM.

The core logic lives in sandbox.deploy.run_agent; this module only parses
arguments and reports failures.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from configuration import read_materials_directory
from sandbox.deploy.credentials import KEYS_FILENAME
from sandbox.deploy.run_agent import END_COMPLETED, run_agent


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Worker configuration file')
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument(
        '--run-dir', type=Path, required=True, help='VM run directory for transient state; deleted after the run unless --keep-vm'
    )
    parser.add_argument('--output-dir', type=Path, required=True, help='host directory receiving run-summary.json and output/')
    parser.add_argument('--base-image', type=Path, required=True, help='frozen base qcow2 the overlay is created from')
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm', help='tcg works without KVM at a large slowdown')
    parser.add_argument('--agent-config', type=Path, required=True, help='JSON with provider, model, effort, and harness for this run')
    parser.add_argument(
        '--keys', type=Path, default=Path(KEYS_FILENAME), help='credential table the model resolves against; never enters the guest'
    )
    parser.add_argument(
        '--vnc',
        default=None,
        help='VNC display for watching the guest desktop live, e.g. :5 for host port 5905; '
        'a bare :N listens on loopback only'
    )
    parser.add_argument('--keep-vm', action='store_true')
    args = parser.parse_args()
    materials_directory = read_materials_directory(args.config)
    summary = run_agent(
        args.task_dir,
        args.output_dir,
        args.run_dir,
        args.agent_config,
        args.keys,
        materials_directory / 'harnesses',
        args.base_image,
        tools_root=materials_directory / 'tools',
        accel=args.accel,
        vnc=args.vnc,
        keep_vm=args.keep_vm
    )
    print(json.dumps(summary, indent=2))
    if summary['end_reason'] != END_COMPLETED:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
