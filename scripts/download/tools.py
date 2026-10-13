"""
Download and verify the tool packages.

    python3 scripts/download/tools.py --root <material root>

The table pins each vendor distribution under tools/<name>/<version>/. A tool
bundle also needs companion files the vendor does not ship as one package: the
offline wheels under tools/mujoco/3.14.0/wheels/ and the conda packages under
tools/python/3.11.16/conda/ with their conda-packages.json. Those are resolved
separately and are not part of this table. Harness distributions live in
harnesses.py.
"""

from pathlib import Path
import sys
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import Material, run

MATERIALS: Final = (
    Material(
        'Blender',
        '5.2.2',
        '2026-09-15T09:18:49Z',
        'https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz',
        '84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168',
        'tools/blender/5.2.2/blender-5.2.2-linux-x64.tar.xz'
    ),
    Material(
        'FreeCAD',
        '1.1.3',
        '2026-07-25T04:53:36Z',
        'https://github.com/FreeCAD/FreeCAD/releases/download/1.1.3/FreeCAD_1.1.3-Linux-x86_64-py311.AppImage',
        '3a853eb69ee595f779f2255dbf80a765926981d8ff68903cefee4dfb03a8f5ef',
        'tools/freecad/1.1.3/FreeCAD_1.1.3-Linux-x86_64-py311.AppImage'
    ),
    Material(
        'MuJoCo',
        '3.14.0',
        '2026-09-22T17:30:24.940590Z',
        'https://files.pythonhosted.org/packages/4e/8e/1e50dc6db3c99a5a02642e253641bddef9b1b7c65527660baaae83eb3375/'
        'mujoco-3.14.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        '922983dfdf88d29f4eb0f451a1d18867156d9fc97e3df6e63b9933cc0eba4d38',
        'tools/mujoco/3.14.0/mujoco-3.14.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
    ),
    Material(
        'Python',
        '3.11.16',
        '2026-08-27T16:21:53Z',
        'https://repo.anaconda.com/pkgs/main/linux-64/python-3.11.16-h17756b0_0.conda',
        'a095fd0449140bf8c6e77d251d9a425081fc00498337101134ba27e8355cac60',
        'tools/python/3.11.16/python-3.11.16-linux-64.conda'
    ),
    Material(
        'SDF View',
        '0.2.0',
        'UNKNOWN',
        'https://github.com/d-itu/sdf-view/releases/download/v0.2.0/sdf-view-x86_64-unknown-linux-gnu.tar.gz',
        '6bea659124f0c135476b53e11b433f1f1e0a7502b0fe02d47043100a9e87e0e9',
        'tools/sdf-view/0.2.0/sdf-view-x86_64-unknown-linux-gnu.tar.gz'
    ),
)

# No tool in the frozen set is deliberately unarchived, so this table carries no blocked records.
BLOCKED: Final = ()

if __name__ == '__main__':
    run(MATERIALS, BLOCKED, 'Download and verify the tool packages.')
