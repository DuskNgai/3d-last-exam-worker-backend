#!/bin/bash
set -euo pipefail
bundle=$1
version=$2
archive=$(find "$bundle" -type f -name 'sdf-view-*.tar.gz' -print -quit)
[[ -n "$archive" ]]
install -d /task/software/sdf-view
tar -xzf "$archive" -C /task/software/sdf-view
binary=$(find /task/software/sdf-view -type f -name sdf-view -print -quit)
[[ -n "$binary" ]]
chmod +x "$binary"
"$binary" --version 2>&1 | grep -F "$version"
