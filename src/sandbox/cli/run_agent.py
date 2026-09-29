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

from sandbox.deploy.agent_config import AgentConfigError
from sandbox.deploy.credentials import CredentialsError, KEYS_FILENAME
from sandbox.deploy.deploy import DeployError
from sandbox.deploy.harness import HarnessError
from sandbox.deploy.run_agent import AgentRunError, END_COMPLETED, run_agent, TOKENS_PER_BUDGET_UNIT


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument(
        '--run-dir', type=Path, required=True, help='VM run directory for transient state; deleted after the run unless --keep-vm'
    )
    parser.add_argument('--output-dir', type=Path, required=True, help='host directory receiving run-summary.json and output/')
    parser.add_argument(
        '--harness-root',
        type=Path,
        required=True,
        help='directory of harness distributions, organized as <name>/<version>/<vendor distribution file>, with install.sh at its root'
    )
    parser.add_argument('--base-image', type=Path, required=True, help='frozen base qcow2 the overlay is created from')
    parser.add_argument(
        '--tools-root',
        type=Path,
        required=True,
        help='tool bundle root holding <name>/install.sh and <name>/<version>/ for every tool the task names'
    )
    parser.add_argument(
        '--time-budget',
        type=float,
        default=None,
        help='wall-clock budget in seconds for the agent stage; defaults to the task card budget.maximum_minutes'
    )
    parser.add_argument(
        '--token-budget',
        type=float,
        default=None,
        help='model-token budget in millions of tokens for the agent stage; defaults to the task card budget.maximum_tokens'
    )
    parser.add_argument('--smp', type=int, default=4)
    parser.add_argument('--memory', type=int, default=16384)
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
    try:
        summary = run_agent(
            args.task_dir,
            args.output_dir,
            args.run_dir,
            args.agent_config,
            args.keys,
            args.harness_root,
            args.base_image,
            tools_root=args.tools_root,
            time_budget=args.time_budget,
            token_budget=None if args.token_budget is None else round(args.token_budget * TOKENS_PER_BUDGET_UNIT),
            smp=args.smp,
            memory=args.memory,
            accel=args.accel,
            vnc=args.vnc,
            keep_vm=args.keep_vm
        )
    except (AgentRunError, CredentialsError, DeployError, HarnessError, AgentConfigError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(summary, indent=2))
    if summary['end_reason'] != END_COMPLETED:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
