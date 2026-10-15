"""
Download and verify the ISO the base image is built from.

    python src/setup/download/isos.py --config src/configuration.yaml

The table pins the Ubuntu desktop image under isos/. The build checks the same
digest again when it is given --iso-sha256, so the value here and the one in
notes/worker/setup/qemu-setup.md have to stay equal. Harness distributions live in
harnesses.py and tool packages in tools.py.
"""

from pathlib import Path
import sys
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from setup.common import Material, run

MATERIALS: Final = (
    Material(
        'Ubuntu Desktop',
        '24.04.5.1',
        '2026-09-14T23:20:53Z',
        'https://releases.ubuntu.com/24.04.5/ubuntu-24.04.5.1-desktop-amd64.iso',
        '4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7',
        'isos/ubuntu-24.04.5.1-desktop-amd64.iso'
    ),
)

# Every image in the frozen set is downloadable, so this table carries no blocked records.
BLOCKED: Final = ()

if __name__ == '__main__':
    run(MATERIALS, BLOCKED, 'Download and verify the ISO the base image is built from.')
