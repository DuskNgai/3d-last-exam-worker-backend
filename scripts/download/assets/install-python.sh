#!/bin/bash
set -euo pipefail
bundle=$1
version=$2
payload="$bundle/$version"
python3 - "$payload" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
metadata = root / 'conda-packages.json'
package_dir = root / 'conda'
if not metadata.is_file() or not package_dir.is_dir():
    raise SystemExit('Python bundle requires conda-packages.json and its offline conda/ dependencies')
records = json.loads(metadata.read_text())
(root / 'explicit.txt').write_text('@EXPLICIT\n' + ''.join(
    (package_dir / record['file']).as_uri() + '#' + record['md5'] + '\n' for record in records
))
PY
bootstrap="$payload/miniconda.sh"
[[ -f "$bootstrap" ]]
if [[ ! -x /task/installer-conda/bin/conda ]]; then
  bash "$bootstrap" -b -p /task/installer-conda
fi
/task/installer-conda/bin/conda create --offline -y -p /task/software/python --file "$payload/explicit.txt"
/task/software/python/bin/python -m pip install --root-user-action=ignore --no-index --find-links "$payload/wheels" \
  numpy==2.2.6 scipy==1.15.3 trimesh==4.7.4 manifold3d==3.2.1 pillow==12.3.0
/task/software/python/bin/python -c 'import platform,sys; assert platform.python_version() == sys.argv[1]' "$version"
/task/software/python/bin/python -c 'import numpy, scipy, trimesh, manifold3d, PIL'
