#!/bin/bash
set -euo pipefail
bundle=$1
python3 - "$bundle" <<'PY'
import hashlib,json,sys
from pathlib import Path
r=Path(sys.argv[1])
for item in json.loads((r/'wheels-manifest.json').read_text()):
    assert hashlib.sha256((r/item['file']).read_bytes()).hexdigest()==item['sha256'],item['file']
PY
/task/software/python/bin/python -m pip install --root-user-action=ignore --no-index --find-links "$bundle/wheels" mujoco==3.3.7 numpy==2.2.6 scipy==1.15.3 trimesh==4.7.4 manifold3d==3.2.1 pillow
ln -sfn python/lib/python3.11/site-packages/mujoco /task/software/mujoco
chmod -R go-w /task/software
install -d -o agent -g agent /work /task/output
install -d -m 755 /opt/benchmark/checks
cp "$bundle"/check_*.py /opt/benchmark/checks/
cd /work
runuser -u agent -- /task/software/python/bin/python /opt/benchmark/checks/check_software.py
runuser -u agent -- env MUJOCO_GL=egl EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 /task/software/python/bin/python /opt/benchmark/checks/check_render.py
