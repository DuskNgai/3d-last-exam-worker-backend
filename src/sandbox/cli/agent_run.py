#!/usr/bin/env python3
"""External CLI for running an agent task inside a gateway-mode VM.

The core logic lives in sandbox.deploy.agent_run; this module only parses
arguments and reports failures.
"""

import argparse
import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.deploy.agent_run import DEFAULT_OUTPUT_ROOT, DEFAULT_TIMEOUT, END_COMPLETED, AgentRunError, run_agent
from sandbox.deploy.qemu_vm import DEFAULT_RUNS_ROOT


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument('--run-id', default=datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument('--smp', type=int, default=4)
    parser.add_argument('--memory', type=int, default=16384)
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm',
                        help='tcg works without KVM at a large slowdown')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--runs-root', type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument('--keep-vm', action='store_true')
    args = parser.parse_args()
    out_dir = args.output_root / args.run_id
    run_dir = args.runs_root / f'agent-{args.run_id}'
    try:
        summary = run_agent(args.task_dir, out_dir, run_dir, args.env_file, timeout=args.timeout,
                            smp=args.smp, memory=args.memory, accel=args.accel, keep_vm=args.keep_vm)
    except AgentRunError as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(summary, indent=2))
    if summary['end_reason'] != END_COMPLETED:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
