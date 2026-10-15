#!/bin/bash
set -euo pipefail
bundle=$1
version=$2
archive=$(find "$bundle/$version" -maxdepth 1 -type f -name 'cpython-*-install_only_stripped.tar.gz' -print -quit)
[[ -n "$archive" ]]
install -d /task/software/python
tar -xzf "$archive" -C /task/software/python --strip-components=1
test -x /task/software/python/bin/python
/task/software/python/bin/python -c 'import platform,sys; assert platform.python_version() == sys.argv[1]' "$version"
/task/software/python/bin/python -m pip install --root-user-action=ignore --no-index --find-links "$bundle/$version/wheels" \
  numpy==2.2.6 scipy==1.15.3 trimesh==4.7.4 manifold3d==3.2.1 pillow==12.3.0
/task/software/python/bin/python -c 'import numpy, scipy, trimesh, manifold3d, PIL'
