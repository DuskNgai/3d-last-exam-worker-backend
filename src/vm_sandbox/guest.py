"""Run VMware Guest operations without printing credentials."""

import argparse
import json
from pathlib import Path
import subprocess


def main() -> None:
    """Dispatch a single Guest operation using a private credential file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vmx", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("operation", choices=["run", "upload", "download", "screenshot", "processes"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    credentials_path = args.credentials.resolve(strict=True)
    if credentials_path.stat().st_mode & 0o077:
        parser.error("Credential file must not be accessible to group or others")
    credentials = json.loads(credentials_path.read_text())
    operations = {
        "run": "runProgramInGuest",
        "upload": "copyFileFromHostToGuest",
        "download": "copyFileFromGuestToHost",
        "screenshot": "captureScreen",
        "processes": "listProcessesInGuest",
    }
    command = [
        "vmrun", "-T", "ws", "-gu", credentials["username"], "-gp", credentials["password"],
        operations[args.operation], str(args.vmx.resolve(strict=True)), *args.arguments,
    ]
    try:
        result = subprocess.run(command, timeout=args.timeout, check=False, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        parser.exit(124, "Guest control request timed out; Guest process may still be running.\n")
    for output in (result.stdout, result.stderr):
        print(output.replace(credentials["password"], "<redacted>"), end="")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
