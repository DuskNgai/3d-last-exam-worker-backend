#!/bin/bash
set -euo pipefail
bundle=$1
version=$2
appimage=$(find "$bundle/$version" -maxdepth 1 -type f -name 'FreeCAD*.AppImage' -print -quit)
[[ -n "$appimage" ]]
chmod +x "$appimage"
install -d /task/software/freecad
cd /task/software/freecad
"$appimage" --appimage-extract >/dev/null
test -x /task/software/freecad/squashfs-root/AppRun
ln -sfn /task/software/freecad/squashfs-root/AppRun /task/software/freecad/FreeCAD
# The console binary, not the FreeCAD link: that one is AppRun and starts the GUI, which aborts without a
# display and takes the whole check down with it.
freecadcmd=/task/software/freecad/squashfs-root/usr/bin/freecadcmd
reported=$("$freecadcmd" -c 'import FreeCAD; print(".".join(FreeCAD.Version()[:3]))' | tail -1)
grep -F "$version" <<<"$reported"
