#!/usr/bin/env python3
"""Command-line interface for QEMU VM lifecycle operations.

This is the public CLI entry point; the core logic lives in sandbox.deploy.qemu_vm.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.deploy.qemu_vm import (
    DEFAULT_BASE_IMAGE, DEFAULT_RUNS_ROOT, READY_TIMEOUT_SECONDS, VmError, create_overlay, destroy_run, start_vm,
    stop_vm, vm_status, wait_ready
)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-root', type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument('--base-image', type=Path, default=DEFAULT_BASE_IMAGE)
    subparsers = parser.add_subparsers(dest='command', required=True)
    create = subparsers.add_parser('create', help='create run dir and overlay')
    create.add_argument('run_dir', type=Path)
    start = subparsers.add_parser('start', help='start the VM in a run dir')
    start.add_argument('run_dir', type=Path)
    start.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm')
    start.add_argument('--smp', type=int, default=4)
    start.add_argument('--memory', type=int, default=16384)
    start.add_argument('--network', choices=['none', 'nat', 'gateway'], default='none')
    start.add_argument('--vnc', default=None)
    start.add_argument('--relay-command', default=None, help='command spawned per guest connection in gateway mode')
    wait = subparsers.add_parser('wait-ready', help='block until the guest agent answers')
    wait.add_argument('run_dir', type=Path)
    wait.add_argument('--timeout', type=float, default=READY_TIMEOUT_SECONDS)
    stop = subparsers.add_parser('stop', help='stop the VM with escalating force')
    stop.add_argument('run_dir', type=Path)
    status = subparsers.add_parser('status', help='print VM status as JSON')
    status.add_argument('run_dir', type=Path)
    destroy = subparsers.add_parser('destroy', help='stop the VM and delete the run dir')
    destroy.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'create':
            overlay = create_overlay(args.run_dir, args.base_image)
            print(f'overlay: {overlay}')
        elif args.command == 'start':
            pid = start_vm(args.run_dir, accel=args.accel, smp=args.smp, memory=args.memory,
                           network=args.network, vnc=args.vnc, relay_command=args.relay_command)
            print(f'pid: {pid}')
        elif args.command == 'wait-ready':
            wait_ready(args.run_dir, timeout=args.timeout)
            print('guest agent ready')
        elif args.command == 'stop':
            print(f'stop level: {stop_vm(args.run_dir)}')
        elif args.command == 'status':
            print(json.dumps(vm_status(args.run_dir), indent=2))
        elif args.command == 'destroy':
            destroy_run(args.run_dir)
            print(f'destroyed: {args.run_dir.resolve()}')
    except VmError as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
