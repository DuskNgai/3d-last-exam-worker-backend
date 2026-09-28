#!/usr/bin/env python3
"""External CLI for running an agent task inside a gateway-mode VM.

The core logic lives in sandbox.deploy.run_agent; this module only parses
arguments and reports failures.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.deploy.harness import HarnessError
from sandbox.deploy.run_agent import DEFAULT_TIMEOUT, END_COMPLETED, AgentRunError, run_agent


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument(
        '--run-dir', type=Path, required=True, help='VM run directory for transient state; deleted after the run unless --keep-vm'
    )
    parser.add_argument('--output-dir', type=Path, required=True, help='host directory receiving run-summary.json and task-output/')
    parser.add_argument(
        '--tools-root', type=Path, required=True, help='directory of tool bundles, organized as <name>/install.sh and <name>/<version>/'
    )
    parser.add_argument(
        '--tools',
        required=True,
        help='comma-separated <name>@<version> specs, installed in order; each resolves to '
        'tools-root/<name>/<version>/ and is installed by tools-root/<name>/install.sh'
    )
    parser.add_argument(
        '--harness-root',
        type=Path,
        required=True,
        help='directory of harness distributions, organized as <name>/<version>/<vendor archive>'
    )
    parser.add_argument('--harness', required=True, help='harness spec <name>@<version>, e.g. codex@0.157.0')
    parser.add_argument('--base-image', type=Path, required=True, help='frozen base qcow2 the overlay is created from')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument('--smp', type=int, default=4)
    parser.add_argument('--memory', type=int, default=16384)
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm', help='tcg works without KVM at a large slowdown')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument(
        '--vnc',
        default=None,
        help='VNC display for watching the guest desktop live, e.g. :5 for host port 5905; '
        'a bare :N listens on loopback only'
    )
    parser.add_argument('--keep-vm', action='store_true')
    args = parser.parse_args()
    tools = [name.strip() for name in args.tools.split(',') if name.strip()]
    if not tools:
        parser.error('--tools must name at least one tool')
    try:
        summary = run_agent(
            args.task_dir,
            args.output_dir,
            args.run_dir,
            args.env_file,
            args.tools_root,
            tools,
            args.harness_root,
            args.harness,
            args.base_image,
            timeout=args.timeout,
            smp=args.smp,
            memory=args.memory,
            accel=args.accel,
            vnc=args.vnc,
            keep_vm=args.keep_vm
        )
    except (AgentRunError, HarnessError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(summary, indent=2))
    if summary['end_reason'] != END_COMPLETED:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
