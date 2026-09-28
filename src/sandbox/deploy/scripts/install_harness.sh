#!/bin/bash
set -euo pipefail
bundle=$1
python3 - "$bundle" <<'PY'
import hashlib,json,sys,tarfile,zipfile
from pathlib import Path, PurePosixPath
SUFFIXES=('.tar.gz','.tgz','.zip')
TARGET=Path('/task/harness')
bundle=Path(sys.argv[1])
archives=[p for p in bundle.iterdir() if p.is_file() and p.name.endswith(SUFFIXES)]
if len(archives)!=1:
    raise SystemExit(f'expected exactly one archive in {bundle}, found {[p.name for p in archives]}')
manifest=bundle/'manifest.json'
if manifest.is_file():
    for item in json.loads(manifest.read_text()):
        path=bundle/item['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError(f'Hash mismatch: {path.name}')

def check(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsupported archive member: {name}')

def extract_zip(archive):
    with zipfile.ZipFile(archive) as handle:
        for info in handle.infolist():
            check(info.filename)
            if (info.external_attr>>16)&0o170000==0o120000:
                raise ValueError(f'Unsupported archive member: {info.filename}')
        handle.extractall(TARGET)

def extract_tar(archive):
    with tarfile.open(archive) as handle:
        for member in handle.getmembers():
            check(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsupported archive member: {member.name}')
        handle.extractall(TARGET,filter='data')

TARGET.mkdir(parents=True,exist_ok=True)
if archives[0].name.endswith('.zip'):
    extract_zip(archives[0])
else:
    extract_tar(archives[0])
PY
chown -R root:root /task/harness
chmod -R go-w /task/harness
