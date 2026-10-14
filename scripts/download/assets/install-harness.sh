#!/bin/bash
set -euo pipefail
bundle=$1
python3 - "$bundle" <<'PY'
"""Install one harness distribution from its bundle into /task/harness.

The bundle holds the vendor distribution file plus an optional manifest.json of
{"file", "sha256"} records, verified before anything is unpacked. A tar archive
is unpacked, any other payload is copied in as a bare binary; the shell part
then links the matching /opt/harness-deps closure so a Node entry resolves its
dependencies.
"""

import hashlib
import json
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Final

TARGET: Final = Path('/task/harness')
TAR_SUFFIXES: Final = ('.tar.gz', '.tgz')
NON_PAYLOAD_NAMES: Final = ('manifest.json', 'install.sh')
COPY_MODE: Final = 0o755
HASH_CHUNK_BYTES: Final = 8 * 1024 * 1024


class InstallError(RuntimeError):
    """The bundle cannot be installed: wrong payload set, bad digest, or unsafe archive."""


def _sha256(path: Path) -> str:
    """Return the hex sha256 of a file, streaming the reads."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(name: str, source: str) -> Path:
    """Return name as a relative path.

    Raises:
        InstallError: The path is absolute or climbs out with ..
    """
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise InstallError(f'unsupported path in {source}: {name}')
    return Path(*path.parts)


def _verify_manifest(bundle: Path) -> None:
    """Check every record in the bundle's manifest.json against the file it names.

    Raises:
        InstallError: The manifest is unreadable, or a file does not match its recorded digest.
    """
    manifest = bundle / 'manifest.json'
    if not manifest.is_file():
        return
    try:
        records = json.loads(manifest.read_text())
    except (OSError, ValueError) as error:
        raise InstallError(f'cannot read {manifest}: {error}') from error
    if not isinstance(records, list):
        raise InstallError(f'{manifest} must hold a list of file records')
    for record in records:
        path = bundle / _relative(record['file'], manifest.name)
        actual = _sha256(path) if path.is_file() else None
        if actual != record['sha256']:
            raise InstallError(f'{path.name} does not match {manifest.name}: expected {record["sha256"]}, got {actual}')


def _payload(bundle: Path) -> Path:
    """Return the bundle's single distribution file.

    Raises:
        InstallError: The bundle does not hold exactly one distribution file.
    """
    payloads = [path for path in sorted(bundle.iterdir()) if path.is_file() and path.name not in NON_PAYLOAD_NAMES]
    if len(payloads) != 1:
        raise InstallError(f'{bundle} must hold exactly one distribution file, found {[path.name for path in payloads]}')
    return payloads[0]


def _extract_tar(archive: Path) -> None:
    """Unpack a tar archive, refusing members that are neither files nor directories."""
    with tarfile.open(archive) as handle:
        for member in handle.getmembers():
            _relative(member.name, archive.name)
            if not (member.isfile() or member.isdir()):
                raise InstallError(f'unsupported archive member: {member.name}')
        handle.extractall(TARGET, filter='data')


def _install(bundle: Path) -> None:
    """Verify the manifest, then unpack the distribution into TARGET."""
    _verify_manifest(bundle)
    payload = _payload(bundle)
    TARGET.mkdir(parents=True, exist_ok=True)
    if payload.name.endswith(TAR_SUFFIXES):
        _extract_tar(payload)
    else:
        destination = TARGET / payload.name
        shutil.copyfile(payload, destination)
        destination.chmod(COPY_MODE)


def main() -> None:
    """Install the bundle directory named by the single argument."""
    if len(sys.argv) != 2:
        raise SystemExit('usage: install.sh <bundle directory>')
    try:
        _install(Path(sys.argv[1]))
    except InstallError as error:
        print(f'harness install failed: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1) from error
    print(f'harness installed into {TARGET}', flush=True)


if __name__ == '__main__':
    main()
PY
# The image stages each harness's npm dependency closure under /opt/harness-deps;
# link the matching tree in so the entry resolves it.
name=$(basename "$bundle")
if [ -d "/opt/harness-deps/$name/node_modules" ]; then
  ln -sfn "/opt/harness-deps/$name/node_modules" /task/harness/node_modules
fi
chown -R root:root /task/harness
chmod -R go-w /task/harness
