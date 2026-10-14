"""
Download and verify the tool packages, their offline dependency closures, and their install scripts.

    python scripts/download/tools.py --root <material root>

The MATERIALS table pins each vendor distribution under tools/<name>/<version>/.
Two tools need companion files the vendor does not ship as one package: python
carries a Miniconda bootstrap plus the conda packages its interpreter is created
from, and python and mujoco carry offline wheels. Each tool's install.sh is
repository content under scripts/download/assets/, refreshed into
tools/<name>/install.sh on every run; conda-packages.json is regenerated from the
downloaded conda packages. Harness distributions live in harnesses.py.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import download_material, Material

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

PYTHON_VERSION: Final = '3.11.16'
MUJOCO_VERSION: Final = '3.14.0'
CONDA_MAIN: Final = 'https://repo.anaconda.com/pkgs/main'
ASSETS: Final = Path(__file__).resolve().parent / 'assets'
TOOL_NAMES: Final = ('blender', 'freecad', 'mujoco', 'python', 'sdf-view')

# The bootstrap installer that creates the interpreter prefix offline.
PYTHON_BOOTSTRAP: Final = (
    Material(
        'Miniconda3 bootstrap',
        'py314_26.7.1-1',
        '2026-08-28T18:39:27Z',
        'https://repo.anaconda.com/miniconda/Miniconda3-py314_26.7.1-1-Linux-x86_64.sh',
        'e8b25b92b262499141c5bd57a98d3c008024185fa951494b9cd9b6d94e72338b',
        f'tools/python/{PYTHON_VERSION}/miniconda.sh'
    ),
)

# The interpreter closure, in install order: conda create --offline consumes exactly this list
# through the generated conda-packages.json. Rows are (file, sha256, release, noarch); noarch
# entries live under pkgs/main/noarch, the rest under pkgs/main/linux-64. Release times are the
# official repository's publication timestamps.
PYTHON_CONDA: Final = (
    ('_libgcc_mutex-0.1-main.conda', '476626712f60e5ef0fe04c354727152b1ee5285d57ccd3575c7be930122bd051', '2025-02-06T00:18:30Z', False),
    ('_openmp_mutex-5.1-52_gnu.conda', '2f2c16a33f51ba032e8d4929b4d2e9173d81931d8d280a4a4a85a2412e8bdaf0', '2026-05-18T19:16:02Z', False),
    ('bzip2-1.0.8-h5eee18b_6.conda', '235f266d5f9c3c61748bb1af0eff21bc7ed2a2a356b97ff28d9c1135039b08b0', '2025-02-05T15:03:11Z', False),
    (
        'ca-certificates-2026.8.13-h06a4308_0.conda',
        'b026cdcb69009db86e4f079d81ffa503b773508a02bd6fd57527d45a95a32be9',
        '2026-08-27T14:18:56Z',
        False
    ),
    (
        'ld_impl_linux-64-2.44-h9e0c5a2_3.conda',
        '4a4875ab4354d4a1bf1695850efad8787d7063387ed6b5aec5ecb659869e2590',
        '2026-03-11T19:09:27Z',
        False
    ),
    ('libexpat-2.8.5-h7354ed3_1.conda', '4dfc5550ed842c5334cb2ec3985c7f848af52004cfed29a84512f12056c4ccfd', '2026-09-24T20:26:28Z', False),
    ('libffi-3.4.8-h06d3fd0_3.conda', 'd000d5d1843f472c7c8f750af2113013c74e9dcdcbe58220568d9d37df5a8607', '2026-06-16T18:37:33Z', False),
    ('libgcc-15.2.0-h69a1729_8.conda', '10fbca6e0cc0da1018f82f3c92e3841e734e704dbfee5df88ae652a49d8b402f', '2026-05-19T19:28:17Z', False),
    (
        'libgcc-ng-15.2.0-h166f726_8.conda',
        '3b4b734309a58b3ded42e01aecfbb91b029ad22d768a4c9f0f4f0006a3172203',
        '2026-05-19T19:28:17Z',
        False
    ),
    ('libnsl-2.0.0-h5eee18b_0.conda', '34fbadc1e86ee37b19b98799b4f806d8be205e9c0220bdb76598f0295961812e', '2025-02-05T04:10:37Z', False),
    (
        'libstdcxx-15.2.0-h39759b7_8.conda',
        '30b7a96c0f1f527d16ec9ec7f80404f34d4b916332cca0634111ed077d7fc870',
        '2026-05-19T19:28:17Z',
        False
    ),
    ('libuuid-1.41.5-h5eee18b_0.conda', '2a401aafabac51b7736cfe12d2ab205d29052640ea8183253c9d0a8e7ed0d49a', '2025-02-05T05:28:07Z', False),
    ('libxcb-1.17.0-h9b100fa_0.conda', 'f9cd1564fc83261ddba7496404f18143d4f8fda52e42c040005c1b1ad3e47f56', '2025-04-30T14:13:21Z', False),
    ('libzlib-1.3.2-h47b2149_0.conda', '02b0d3bad12d15c668f06a32283907608bcfb49ca40d9d09d3bcfe6bd4765f31', '2026-06-09T12:58:53Z', False),
    ('ncurses-6.6-hfaaeb4e_0.conda', 'a080cfac9b51bb62dcd54823622e602cd8cd8d67406c76cbbdfc0602f9e822b8', '2026-08-24T09:20:26Z', False),
    ('openssl-3.5.8-h1b28b03_0.conda', '4774aa6ce576e3ffc5a87056eae5d09280122ffcce2d2468e94fb89f003ce39f', '2026-08-31T12:25:53Z', False),
    (
        'packaging-26.3-py311h06a4308_0.conda',
        '106ce589ceb59bda4b510fdf6ec5bea19060e1b772a5a3f4c232766f8528ca23',
        '2026-08-21T14:33:34Z',
        False
    ),
    ('pip-26.2.1-pyhc872135_0.conda', '64a7339fb4b85160f4c7fff98e1ad9d2a9b84f01e954a7bc5c6b2dea3bb98bfd', '2026-08-28T15:09:12Z', True),
    (
        'pthread-stubs-0.3-h47b2149_2.conda',
        'f9ccf45615a75712116b79a4b02fbc69e681b4e82d65a0dd11430fac3244623c',
        '2026-07-24T13:17:35Z',
        False
    ),
    ('python-3.11.16-h17756b0_0.conda', 'a095fd0449140bf8c6e77d251d9a425081fc00498337101134ba27e8355cac60', '2026-08-27T16:21:53Z', False),
    ('readline-8.3-hc2a1206_0.conda', '0a438fa74f2776bde137017d6c18214b5da65b557f6a59f877fa4e091582f789', '2025-08-06T13:19:20Z', False),
    (
        'setuptools-83.0.0-py311h06a4308_0.conda',
        '3407df5998ddf3398a08e4a4def4d2c653e252d824272bdc1bada2a1e8d284da',
        '2026-07-14T13:37:11Z',
        False
    ),
    ('sqlite-3.53.4-h795bf6d_0.conda', '839f97490251e076b64ff70521a2437e8835a0a42728d39562e8ede0addcf18f', '2026-09-10T14:22:50Z', False),
    ('tk-8.6.15-h54e0aa7_0.conda', 'a3bcd301ddd0bdb2f52f9bd3274fabe3c33d43ed61e58ee657efeac01b4be157', '2025-08-15T10:10:00Z', False),
    ('tzdata-2026c-he532380_0.conda', '52adc4b0a9cce2ffb8ad319d54b7c940ae7961f7caf7ce6851eec4b3f542c368', '2026-07-13T14:11:16Z', True),
    (
        'wheel-0.47.0-py311h06a4308_0.conda',
        '48c0706884c4a81c720332b2f8ceab77eccb48ae157d5d45ad597f981495c91e',
        '2026-06-18T12:16:11Z',
        False
    ),
    (
        'xorg-libx11-1.8.13-h65de747_0.conda',
        '5aa71cae22365e5d882dc983a58cc805a689d1e4a370a8030bd4d1b784bdf532',
        '2026-08-28T08:28:15Z',
        False
    ),
    (
        'xorg-libxau-1.0.12-h9b100fa_0.conda',
        '68acf87da69a0237d5083798a9cc9b89d2887a84a5323aa740006b53cd5159ef',
        '2025-04-29T20:52:34Z',
        False
    ),
    (
        'xorg-libxdmcp-1.1.5-h9b100fa_0.conda',
        '459c8497d1c22b020404b481fa12011abeb27d8eefde37dd4ac9f5a443fd5ac7',
        '2025-04-30T05:56:56Z',
        False
    ),
    (
        'xorg-xorgproto-2025.1-h47b2149_0.conda',
        '1fc1b37830927279255d31775497f8abf5b872e924ed48fedab129ee6a75657a',
        '2026-08-27T15:32:30Z',
        False
    ),
    ('xz-5.8.4-h140a991_0.conda', 'd9383d80b09c9cbb3b1b689f173e84d6e75a4a61ecd4eebee6dae0adc6441793', '2026-09-22T11:17:43Z', False),
    ('zlib-1.3.2-h47b2149_0.conda', '4bb61142e8fe22510086c1030e1ff3ad4888ebf9bf0a11e9931594e8549a9d42', '2026-06-09T12:58:53Z', False),
)

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
        'mujoco-3.14.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        '922983dfdf88d29f4eb0f451a1d18867156d9fc97e3df6e63b9933cc0eba4d38',
        '2026-09-22T17:30:24.940590Z',
        'https://files.pythonhosted.org/packages/4e/8e/1e50dc6db3c99a5a02642e253641bddef9b1b7c65527660baaae83eb3375/'
        'mujoco-3.14.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
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

# No tool in the frozen set is deliberately unarchived, so this table carries no blocked records.
BLOCKED: Final = ()


class ToolError(RuntimeError):
    """A tool closure file is missing or a generated file cannot be written."""


def _conda_table(rows: tuple[tuple[str, str, str, bool], ...], directory: str) -> tuple[Material, ...]:
    """
    Build Material rows for the conda closure.

    Args:
        `rows` (tuple): (file name, sha256, release time, noarch) tuples in install order.
        `directory` (str): Closure directory relative to the material root.

    Returns:
        `table` (tuple[Material, ...]): One pinned row per conda package.
    """
    table = []
    for filename, sha256, release, noarch in rows:
        name, version, _build = filename.removesuffix('.conda').rsplit('-', 2)
        url = f'{CONDA_MAIN}/{"noarch" if noarch else "linux-64"}/{filename}'
        table.append(Material(name, version, release, url, sha256, f'{directory}/{filename}'))
    return tuple(table)


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


def _md5(path: Path) -> str:
    """
    Return the hex md5 of a file, streaming the reads.

    Args:
        `path` (Path): File to hash.

    Returns:
        `digest` (str): Hex md5 of the file content.

    Raises:
        OSError: The file cannot be opened or read.
    """
    digest = hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


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


def write_conda_packages(root: Path) -> Path:
    """
    Regenerate conda-packages.json from the downloaded conda packages.

    The install script turns each record into an explicit-spec line with the md5
    fragment conda expects, so the md5 is computed here from the verified file
    rather than pinned by hand.

    Args:
        `root` (Path): Material root the tools tree lives under.

    Returns:
        `destination` (Path): The written metadata file.

    Raises:
        ToolError: A conda package from the table is missing.
        OSError: The file cannot be read or written.
    """
    directory = root.resolve() / 'tools' / 'python' / PYTHON_VERSION
    records = []
    for filename, _sha256, _release, _noarch in PYTHON_CONDA:
        path = directory / 'conda' / filename
        if not path.is_file():
            raise ToolError(f'conda package missing: {path}; run the download first')
        records.append({
            'file': filename,
            'md5': _md5(path)
        })
    content = (json.dumps(records, indent=1) + '\n').encode()
    destination = directory / 'conda-packages.json'
    if destination.is_file() and destination.read_bytes() == content:
        print(f'unchanged: {destination}', flush=True)
        return destination
    destination.write_bytes(content)
    print(f'wrote: {destination}', flush=True)
    return destination


def tables() -> tuple[tuple[Material, ...], ...]:
    """
    Return every download table of this module, in download order.

    Returns:
        `tables` (tuple[tuple[Material, ...], ...]): Vendor payloads, then the closures.
    """
    return (
        MATERIALS,
        PYTHON_BOOTSTRAP,
        _conda_table(PYTHON_CONDA, f'tools/python/{PYTHON_VERSION}/conda'),
        _wheel_table(PYTHON_WHEELS, f'tools/python/{PYTHON_VERSION}/wheels'),
        _wheel_table(MUJOCO_WHEELS, f'tools/mujoco/{MUJOCO_VERSION}/wheels'),
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--root', type=Path, default=Path.cwd(), help='material root the table paths are relative to; defaults to the working directory'
    )
    args = parser.parse_args()
    root = args.root.resolve()
    downloaded = 0
    existing = 0
    for position, table in enumerate(tables()):
        summary = download_material(table, BLOCKED if position == 0 else (), root)
        downloaded += summary['downloaded']
        existing += summary['existing']
    write_install_scripts(root)
    write_conda_packages(root)
    print(f'done: {downloaded} downloaded, {existing} already present', flush=True)


if __name__ == '__main__':
    main()
