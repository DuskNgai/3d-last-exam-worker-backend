#!/usr/bin/env python3
"""Command-line interface for guest operations over QGA/QMP.

This is the public CLI entry point; the core logic lives in sandbox.deploy.guest.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.deploy.guest import DEFAULT_TIMEOUT, GuestError, list_processes, run_command, take_screenshot
from sandbox.deploy.qemu_vm import qga_socket_path, qmp_socket_path
from sandbox.qga import QgaClient, QgaError, QmpClient


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, help='run directory; derives socket paths')
    parser.add_argument('--qga-socket', type=Path, help='override the QGA socket path')
    parser.add_argument('--qmp-socket', type=Path, help='override the QMP socket path')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument('--user', help='run the command as this guest user via runuser')
    parser.add_argument('--cwd', help='working directory inside the guest')
    subparsers = parser.add_subparsers(dest='operation', required=True)
    run = subparsers.add_parser('run', help='run a shell command and return its exit code')
    run.add_argument('command')
    upload = subparsers.add_parser('upload', help='copy a host file into the guest')
    upload.add_argument('host_path', type=Path)
    upload.add_argument('guest_path')
    download = subparsers.add_parser('download', help='copy a guest file to the host')
    download.add_argument('guest_path')
    download.add_argument('host_path', type=Path)
    screenshot = subparsers.add_parser('screenshot', help='capture the display to a png')
    screenshot.add_argument('output', type=Path)
    subparsers.add_parser('processes', help='list guest processes')
    args = parser.parse_args()
    if args.run_dir is None and args.qga_socket is None and args.qmp_socket is None:
        parser.error('provide --run-dir or explicit socket paths')
    run_dir = args.run_dir.resolve() if args.run_dir else None
    qga_socket = args.qga_socket or (qga_socket_path(run_dir) if run_dir else None)
    qmp_socket = args.qmp_socket or (qmp_socket_path(run_dir) if run_dir else None)
    try:
        if args.operation == 'screenshot':
            if qmp_socket is None:
                parser.error('screenshot needs --run-dir or --qmp-socket')
            output = take_screenshot(QmpClient(qmp_socket), args.output)
            print(f'screenshot: {output}')
            return
        if qga_socket is None:
            parser.error(f'{args.operation} needs --run-dir or --qga-socket')
        qga = QgaClient(qga_socket)
        if args.operation == 'run':
            raise SystemExit(run_command(qga, args.command, args.timeout, cwd=args.cwd, user=args.user))
        if args.operation == 'upload':
            qga.upload(args.guest_path, args.host_path.read_bytes())
            print(f'uploaded: {args.host_path} -> {args.guest_path}')
        elif args.operation == 'download':
            data = qga.download(args.guest_path)
            args.host_path.parent.mkdir(parents=True, exist_ok=True)
            args.host_path.write_bytes(data)
            print(f'downloaded: {args.guest_path} -> {args.host_path} ({len(data)} bytes)')
        elif args.operation == 'processes':
            raise SystemExit(list_processes(qga))
    except (GuestError, QgaError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
