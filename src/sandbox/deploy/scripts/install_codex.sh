#!/bin/bash
set -euo pipefail
archive=$1
expected=8b711520beddf385467b8da4d2c93736637c6ba1e46811cf0d8606b7c490b6f6
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
install -d /opt/benchmark/codex/0.156.1
python3 - "$archive" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise ValueError(f'Unsupported archive member: {member.name}')
    archive.extractall('/opt/benchmark/codex/0.156.1', filter='data')
PY
chown -R root:root /opt/benchmark/codex
chmod -R go-w /opt/benchmark/codex
ln -sfn /opt/benchmark/codex/0.156.1/bin/codex /usr/local/bin/codex
runuser -u agent -- /usr/local/bin/codex --version
runuser -u agent -- /usr/local/bin/codex exec --help
