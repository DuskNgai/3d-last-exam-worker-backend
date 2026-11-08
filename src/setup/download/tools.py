"""
Download and verify the tool packages, their offline wheel closures, and their install scripts.

    python src/setup/download/tools.py --config src/configuration.yaml

The MATERIALS table pins each vendor distribution under tools/<name>/<version>/.
The python tool is a python-build-standalone tarball; python and mujoco carry
offline wheels their install scripts feed to pip --no-index. Each tool's
install.sh is repository content under src/setup/download/assets/, refreshed
into tools/<name>/install.sh on every run. Harness distributions live in
harnesses.py.
"""

import argparse
from pathlib import Path
import sys
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from configuration import read_materials_directory
from setup.common import download_material, Material

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
        '1.1.4',
        '2026-09-28T13:58:35Z',
        'https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage',
        'f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434',
        'tools/freecad/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage'
    ),
    Material(
        'MuJoCo',
        '3.16.0',
        '2026-10-14T10:31:19.055677Z',
        'https://files.pythonhosted.org/packages/fa/54/9d5d12a5cf979e4cbb164fc00879dc9e5dbd9811186a0846be22243f973d/'
        'mujoco-3.16.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        '9990aecfe442a516d0595e5762f89da83431ed46fd353bee1e135e3a7140a551',
        'tools/mujoco/3.16.0/mujoco-3.16.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
    ),
    Material(
        'Python',
        '3.11.17',
        '2026-10-14T02:06:09Z',
        'https://github.com/astral-sh/python-build-standalone/releases/download/20261013/'
        'cpython-3.11.17%2B20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz',
        '3b8e5a248bcc351265ffe68f5e457668a2e09c99a47f81f658988c7d3d107522',
        'tools/python/3.11.17/cpython-3.11.17+20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
    ),
    Material(
        'SDF View',
        '0.2.0',
        '2026-09-26T10:46:10Z',
        'https://github.com/d-itu/sdf-view/releases/download/v0.2.0/sdf-view-x86_64-unknown-linux-gnu.tar.gz',
        '6bea659124f0c135476b53e11b433f1f1e0a7502b0fe02d47043100a9e87e0e9',
        'tools/sdf-view/0.2.0/sdf-view-x86_64-unknown-linux-gnu.tar.gz'
    ),
)

PYTHON_VERSION: Final = '3.11.17'
MUJOCO_VERSION: Final = '3.16.0'
ASSETS: Final = Path(__file__).resolve().parent / 'assets'
TOOL_NAMES: Final = ('blender', 'freecad', 'mujoco', 'python', 'sdf-view')

# Basic third-party libraries the python tool installs into its interpreter, pinned to the
# versions the probe imports. Rows are (file, sha256, release, url); digests, upload times,
# and URLs come from the PyPI release record.
PYTHON_WHEELS: Final = (
    (
        'manifold3d-3.2.1-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        '218fc3abf3654045dc6a413774531cfbe33eef4eb87f2fe2238523042fe170bb',
        '2025-08-06T06:33:46.984707Z',
        'https://files.pythonhosted.org/packages/b6/2f/e6fe1810ba27c5773b1effc0011b2f665e9549dde644bab813502c307ffb/'
        'manifold3d-3.2.1-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
    ),
    (
        'numpy-2.2.6-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl',
        'ba10f8411898fc418a521833e014a77d3ca01c15b0c6cdcce6a0d2897e6dbbdf',
        '2025-05-17T21:32:47.991518Z',
        'https://files.pythonhosted.org/packages/b3/dd/2238b898e51bd6d389b7389ffb20d7f4c10066d80351187ec8e303a5a475/'
        'numpy-2.2.6-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
    ),
    (
        'pillow-12.3.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        '23d27a3e0307ec2244cc51e7287b919aa68d097504ebe19df4e76a98a3eea5bd',
        '2026-07-01T11:53:53.487322Z',
        'https://files.pythonhosted.org/packages/3b/2d/ede717bc1144f63886c21fd349bb95860b0d1a21149ff16f2bb362b612b6/'
        'pillow-12.3.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
    ),
    (
        'scipy-1.15.3-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl',
        '39cb9c62e471b1bb3750066ecc3a3f3052b37751c7c3dfd0fd7e48900ed52982',
        '2025-05-08T16:05:40.762918Z',
        'https://files.pythonhosted.org/packages/bd/37/89f19c8c05505d0601ed5650156e50eb881ae3918786c8fd7262b4ee66d3/'
        'scipy-1.15.3-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
    ),
    (
        'trimesh-4.7.4-py3-none-any.whl',
        '47af90235f7006316c37584b43d5f6c109a5069b252d7ab2bf8e6ed7c9fd953b',
        '2025-08-14T20:13:48.964649Z',
        'https://files.pythonhosted.org/packages/89/7d/37f1b50037c1448cacf875b305a3cdfddc113073d1cfbabe1a3b78939bae/'
        'trimesh-4.7.4-py3-none-any.whl'
    ),
)

# mujoco's own wheel plus its private dependencies; the basic libraries come from the python tool.
MUJOCO_WHEELS: Final = (
    (
        'absl_py-2.5.0-py3-none-any.whl',
        '0f17b89f2a4eaaedc4f28c622998aa690564b3012a396a4ffad0821007fe03ba',
        '2026-07-03T10:57:46.735892Z',
        'https://files.pythonhosted.org/packages/58/0a/a10b45aab35b175aded078a462dc8d0c698f5b13946e7cb0869097b78bb6/'
        'absl_py-2.5.0-py3-none-any.whl'
    ),
    (
        'etils-1.14.0-py3-none-any.whl',
        'b5df7341f54dbe1405a4450b2741207b4a8c279780402b45f87202b94dfc52b4',
        '2026-03-04T17:41:35.010954Z',
        'https://files.pythonhosted.org/packages/5a/3d/589663aeeacd59bb2f3e8596bfd3e81cf0fb18d70bb433199041f469771b/'
        'etils-1.14.0-py3-none-any.whl'
    ),
    (
        'fsspec-2026.9.0-py3-none-any.whl',
        '8dd6e646e99ea382bd85f97a45e6b526a442d79423a7dc673f1e2756d05fcb5f',
        '2026-09-18T17:50:41.341146Z',
        'https://files.pythonhosted.org/packages/6c/c0/a98505f18594f1bce828bb159cec0fcf9860562f1a2c85913409fc8f3d9e/'
        'fsspec-2026.9.0-py3-none-any.whl'
    ),
    (
        'glfw-2.10.2-py2.py3-none-manylinux_2_28_x86_64.whl',
        'b860de3ca0686182483f98f3ddd12e660acf25b3e0d521450ec9a3f999f72a65',
        '2026-07-21T14:42:32.377506Z',
        'https://files.pythonhosted.org/packages/69/84/1b98671314ea5f40397586b6cd14db913de3196333be558fdc177e61708f/'
        'glfw-2.10.2-py2.py3-none-manylinux_2_28_x86_64.whl'
    ),
    (
        'mujoco-3.16.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        '9990aecfe442a516d0595e5762f89da83431ed46fd353bee1e135e3a7140a551',
        '2026-10-14T10:31:19.055677Z',
        'https://files.pythonhosted.org/packages/fa/54/9d5d12a5cf979e4cbb164fc00879dc9e5dbd9811186a0846be22243f973d/'
        'mujoco-3.16.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
    ),
    (
        'pyopengl-3.1.10-py3-none-any.whl',
        '794a943daced39300879e4e47bd94525280685f42dbb5a998d336cfff151d74f',
        '2025-08-18T02:32:59.902135Z',
        'https://files.pythonhosted.org/packages/de/e4/1ba6f44e491c4eece978685230dde56b14d51a0365bc1b774ddaa94d14cd/'
        'pyopengl-3.1.10-py3-none-any.whl'
    ),
    (
        'typing_extensions-4.16.0-py3-none-any.whl',
        '481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8',
        '2026-07-02T08:40:04.659120Z',
        'https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/'
        'typing_extensions-4.16.0-py3-none-any.whl'
    ),
    (
        'websockets-17.1-cp311-cp311-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl',
        '0c84bdef916556cbe1d5a43b423398be4dd3cba6522b463e53d848578b920695',
        '2026-08-26T14:55:26.016891Z',
        'https://files.pythonhosted.org/packages/5c/9a/00ae2e147eaa086fe8bdddd36f57216ce72b9a9dfc0b17c717005ebdacaf/'
        'websockets-17.1-cp311-cp311-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl'
    ),
    (
        'zipp-4.1.0-py3-none-any.whl',
        '25ad4e16390cd314347dd8f1de67a2ac538ae658ed4ab9db16029c07c188e97f',
        '2026-05-18T20:08:57.045692Z',
        'https://files.pythonhosted.org/packages/3a/13/547360d81e6d88d58492968ffda9f9542854f11310ee556fef14260cc886/'
        'zipp-4.1.0-py3-none-any.whl'
    ),
)

def _wheel_table(rows: tuple[tuple[str, str, str, str], ...], directory: str) -> tuple[Material, ...]:
    """
    Build Material rows for one wheels closure.

    Args:
        `rows` (tuple): (file name, sha256, release time, url) tuples.
        `directory` (str): Closure directory relative to the material root.

    Returns:
        `table` (tuple[Material, ...]): One pinned row per wheel.
    """
    table = []
    for filename, sha256, release, url in rows:
        name, version = filename.split('-')[: 2]
        table.append(Material(name, version, release, url, sha256, f'{directory}/{filename}'))
    return tuple(table)


def write_install_scripts(root: Path) -> None:
    """
    Refresh every tool's install.sh from the repository assets.

    Args:
        `root` (Path): Material root the tools tree lives under.

    Raises:
        OSError: A source or destination file cannot be read or written.
    """
    for name in TOOL_NAMES:
        content = (ASSETS / f'install-{name}.sh').read_bytes()
        destination = root.resolve() / 'tools' / name / 'install.sh'
        if destination.is_file() and destination.read_bytes() == content:
            print(f'unchanged: {destination}', flush=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        print(f'wrote: {destination}', flush=True)


def tables() -> tuple[tuple[Material, ...], ...]:
    """
    Return every download table of this module, in download order.

    Returns:
        `tables` (tuple[tuple[Material, ...], ...]): Vendor payloads, then the closures.
    """
    return (
        MATERIALS,
        _wheel_table(PYTHON_WHEELS, f'tools/python/{PYTHON_VERSION}/wheels'),
        _wheel_table(MUJOCO_WHEELS, f'tools/mujoco/{MUJOCO_VERSION}/wheels'),
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Worker configuration file')
    args = parser.parse_args()
    root = read_materials_directory(args.config)
    downloaded = 0
    existing = 0
    for table in tables():
        summary = download_material(table, root)
        downloaded += summary['downloaded']
        existing += summary['existing']
    write_install_scripts(root)
    print(f'done: {downloaded} downloaded, {existing} already present', flush=True)


if __name__ == '__main__':
    main()
