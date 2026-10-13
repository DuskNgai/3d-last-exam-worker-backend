"""
Download and verify pinned material files.

Shared by the download scripts under scripts/download/: each supplies its own
table and this module runs it. Every path in a table is relative to a material
root, so the same table fills both the shared store and a local copy.

Nothing is extracted, installed, or replaced. Each entry pins a URL, a sha256,
and a release time, and a release published after the frozen cutoff is refused.
Packages deliberately not archived are listed in the table's BLOCKED with the
reason, so a rerun cannot quietly substitute another source.
"""

import argparse
from dataclasses import dataclass
import datetime
import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Final

CUTOFF_UTC: Final = '2026-09-25T23:59:59+00:00'
PARTIAL_SUFFIX: Final = '.part'
RETRY_COUNT: Final = 3
CONNECT_TIMEOUT_SECONDS: Final = 20


class MaterialError(RuntimeError):
    """A download or verification step failed."""


@dataclass(frozen=True)
class Material:
    """
    One distribution file to download and verify.

    Attributes:
        label (str): Human-readable package name used in the log.
        version (str): Version of this distribution.
        release_utc (str): ISO-8601 UTC release time, or UNKNOWN when no release record was confirmed.
        url (str): Official download URL.
        sha256 (str): Expected digest of the file.
        path (str): Destination relative to the material root.
    """

    label: str
    version: str
    release_utc: str
    url: str
    sha256: str
    path: str


@dataclass(frozen=True)
class Blocked:
    """
    A package deliberately not downloaded.

    Attributes:
        label (str): Human-readable package name.
        source (str): Where it would have come from.
        reason (str): Why it is not archived at this root.
    """

    label: str
    source: str
    reason: str


def _timestamp(value: str) -> datetime.datetime:
    """Parse an ISO-8601 UTC timestamp."""
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


def sha256(path: Path) -> str:
    """
    Return the hex sha256 of a file, streaming the reads.

    Args:
        `path` (Path): File to hash.

    Returns:
        `digest` (str): Hex sha256 of the file content.
    """
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _check_release(material: Material) -> None:
    """
    Refuse a release published after the frozen cutoff.

    Args:
        `material` (Material): Entry whose release time is checked.

    Raises:
        MaterialError: The recorded release time is after the cutoff.
    """
    if material.release_utc == 'UNKNOWN':
        print(f'warning: {material.label} {material.version} release date is not independently verified', file=sys.stderr, flush=True)
        return
    if _timestamp(material.release_utc) > _timestamp(CUTOFF_UTC):
        raise MaterialError(f'{material.label} {material.version} is after the frozen cutoff: {material.release_utc}')


def fetch(url: str, destination: Path, *, retries: int = RETRY_COUNT, retry_delay: int = 0) -> None:
    """
    Download a URL to a path through a .part file, which is replaced only after a clean transfer.

    The download scripts come through here: the pinned tables call it with the default retry budget, the
    source build raises it because a build fetches tens of megabytes over a link that drops.

    Args:
        `url` (str): Source URL to fetch.
        `destination` (Path): Final path for the file; its parent directory must exist.
        `retries` (int): curl's retry budget.
        `retry_delay` (int): Seconds curl waits between retries; omitted when zero.

    Raises:
        MaterialError: A partial file from an earlier attempt is still there, or curl failed.
    """
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    if partial.exists():
        raise MaterialError(f'partial download already exists; inspect before retrying: {partial}')
    command = ['curl', '--fail', '--location', '--retry', str(retries)]
    if retry_delay:
        command += ['--retry-delay', str(retry_delay)]
    command += ['--retry-all-errors', '--connect-timeout', str(CONNECT_TIMEOUT_SECONDS), '--output', str(partial), url]
    completed = subprocess.run(command)
    if completed.returncode != 0:
        partial.unlink(missing_ok=True)
        raise MaterialError(f'download failed with exit code {completed.returncode}: {url}')
    partial.replace(destination)


def _fetch(material: Material, destination: Path) -> bool:
    """
    Download one material unless it is already present.

    Args:
        `material` (Material): Entry whose URL is fetched.
        `destination` (Path): Absolute destination path for the file.

    Returns:
        `written` (bool): True when bytes were written, False when the file was already there.

    Raises:
        MaterialError: A partial file was left behind, or curl failed.
    """
    if destination.is_file():
        print(f'exists: {destination}', flush=True)
        return False
    print(f'download: {material.label} {material.version}', flush=True)
    try:
        fetch(material.url, destination)
    except MaterialError as error:
        raise MaterialError(f'{material.label} {material.version}: {error}') from error
    return True


def _verify(material: Material, destination: Path) -> None:
    """
    Check a downloaded file against its pinned digest.

    Args:
        `material` (Material): Entry carrying the expected digest.
        `destination` (Path): Downloaded file to hash.

    Raises:
        MaterialError: The digest does not match.
    """
    actual = sha256(destination)
    if actual != material.sha256:
        raise MaterialError(f'{material.label} {material.version} SHA-256 mismatch: expected {material.sha256}, got {actual}')
    print(f'verified: {material.label} {material.version} sha256={actual}', flush=True)
    subprocess.run(['file', str(destination)], check=False)


def download_material(table: tuple[Material, ...], blocked: tuple[Blocked, ...], root: Path) -> dict[str, int]:
    """
    Download and verify every material in one table under root.

    Args:
        `table` (tuple[Material, ...]): Pinned materials to fetch and verify.
        `blocked` (tuple[Blocked, ...]): Packages deliberately not archived, reported and skipped.
        `root` (Path): Material root the table paths are relative to.

    Returns:
        `summary` (dict[str, int]): Counts of downloaded and already-present files.

    Raises:
        MaterialError: A release is after the cutoff, a download failed, or a digest mismatched.
    """
    root = root.resolve()
    print(f'root: {root}', flush=True)
    summary = {
        'downloaded': 0,
        'existing': 0
    }
    for material in table:
        _check_release(material)
        destination = root / material.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        moved = _fetch(material, destination)
        _verify(material, destination)
        summary['downloaded' if moved else 'existing'] += 1
    for record in blocked:
        print(f'blocked: {record.label}\n  source: {record.source}\n  reason: {record.reason}', file=sys.stderr, flush=True)
    return summary


def run(table: tuple[Material, ...], blocked: tuple[Blocked, ...], description: str) -> None:
    """
    Parse --root and run one table, reporting the summary.

    Args:
        `table` (tuple[Material, ...]): Pinned materials to fetch and verify.
        `blocked` (tuple[Blocked, ...]): Packages deliberately not archived, reported and skipped.
        `description` (str): Description shown in the --help output.

    Raises:
        SystemExit: The download failed, or --root was rejected.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        '--root', type=Path, default=Path.cwd(), help='material root the table paths are relative to; defaults to the working directory'
    )
    args = parser.parse_args()
    try:
        summary = download_material(table, blocked, args.root)
    except (MaterialError, OSError) as error:
        print(f'download failed: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1) from error
    print(f'done: {summary["downloaded"]} downloaded, {summary["existing"]} already present', flush=True)
