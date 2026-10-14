#!/bin/bash
set -euo pipefail
bundle=$1
version=$2
payload="$bundle/$version"
wheel=$(find "$payload" -maxdepth 1 -type f -name 'mujoco-*.whl' -print -quit)
[[ -n "$wheel" ]]
test -x /task/software/python/bin/python
/task/software/python/bin/python -m pip install --root-user-action=ignore --no-index --find-links "$payload/wheels" mujoco=="$version"
/task/software/python/bin/python -c 'import mujoco,sys; assert mujoco.__version__ == sys.argv[1], mujoco.__version__' "$version"
ln -sfn python/lib/python3.11/site-packages/mujoco /task/software/mujoco
