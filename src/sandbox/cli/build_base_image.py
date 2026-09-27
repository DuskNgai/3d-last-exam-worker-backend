#!/usr/bin/env python3
"""Command line interface for building the QEMU sandbox base image.

This is the public CLI entry point; the core build logic lives in
sandbox.setup.build_base_image and can be reused as a library.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.qga import QgaError
from sandbox.setup.build_base_image import BuildConfig, BuildError, build_base_image


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True, help='path to the Ubuntu desktop ISO')
    parser.add_argument(
        '--iso-sha256',
        default=None,
        help='official sha256 for the ISO; the build fails when it does not match. '
        'Without it the computed hash is only recorded in the manifest'
    )
    parser.add_argument('--out', type=Path, required=True, help='output directory for base.qcow2 and manifest.json')
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm', help='tcg works without KVM but the install takes hour-scale')
    parser.add_argument('--smp', type=int, default=4)
    parser.add_argument('--memory', type=int, default=8192)
    parser.add_argument('--disk', default='64G')
    parser.add_argument('--skip-install', action='store_true', help='reuse the existing base.qcow2 and skip to verification')
    args = parser.parse_args()

    config = BuildConfig(
        iso=args.iso.resolve(),
        iso_sha256=args.iso_sha256,
        out=args.out.resolve(),
        accel=args.accel,
        smp=args.smp,
        memory=args.memory,
        disk=args.disk,
        skip_install=args.skip_install
    )
    try:
        build_base_image(config)
    except (BuildError, subprocess.CalledProcessError, QgaError, OSError) as error:
        print(f'build failed: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
