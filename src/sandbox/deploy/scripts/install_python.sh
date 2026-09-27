#!/bin/bash
set -euo pipefail
bundle=$1
python3 - "$bundle" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
for item in json.loads((root/'manifest.json').read_text()):
    path=root/item['file']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
        raise ValueError(f'Hash mismatch: {path.name}')
records=json.loads((root/'conda-packages.json').read_text())
(root/'explicit.txt').write_text('@EXPLICIT\n'+''.join((root/'conda'/r['file']).as_uri()+'#'+r['md5']+'\n' for r in records))
PY
if [[ ! -x /opt/benchmark/installer-conda/bin/conda ]]; then
  bash "$bundle/miniconda.sh" -b -p /opt/benchmark/installer-conda
fi
if [[ ! -x /task/software/python/bin/python ]]; then
  /opt/benchmark/installer-conda/bin/conda create --offline -y -p /task/software/python --file "$bundle/explicit.txt"
fi
/task/software/python/bin/python -c 'import platform; assert platform.python_version() == "3.11.16"'
