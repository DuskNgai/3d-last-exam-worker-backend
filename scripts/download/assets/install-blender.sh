#!/bin/bash
set -euo pipefail
bundle=$1
version=$2
archive=$(find "$bundle/$version" -maxdepth 1 -type f -name 'blender-*.tar.xz' -print -quit)
[[ -n "$archive" ]]
install -d /task/software/blender
tar -xJf "$archive" -C /task/software/blender --strip-components=1
test -x /task/software/blender/blender
/task/software/blender/blender --version | head -n 1 | grep -F "$version"
