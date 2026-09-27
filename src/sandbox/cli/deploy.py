#!/usr/bin/env python3
"""External CLI for deploying software bundles into a running QEMU/KVM guest.

The core logic lives in sandbox.deploy.deploy; this module only parses
arguments and reports failures.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.deploy.deploy import DeployError, deploy
from sandbox.qga import QgaError


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='deployment config JSON')
    parser.add_argument('--check-only', action='store_true', help='rerun guest checks and fetch artifacts without installing')
    args = parser.parse_args()
    try:
        config_path = args.config.resolve(strict=True)
    except OSError as error:
        print(f'error: config not found: {args.config}', file=sys.stderr)
        raise SystemExit(1) from error
    try:
        deploy(config_path, args.check_only)
    except (DeployError, QgaError, TimeoutError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
