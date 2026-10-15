"""
Download and verify every material in one run.

    python src/setup/materials.py --config src/configuration.yaml

The materials directory comes from directory.materials_directory in the configuration
file; the ISO, harness, and tool tables then run in one pass, the harness and tool
install scripts are refreshed from the repository assets, and the ZCode source
build is handled as in harnesses.py.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from configuration import read_materials_directory
from setup.common import download_material
from setup.download import harnesses, isos, tools


def main() -> None:
    """
    CLI entry point.

    Raises:
        SystemExit: Argument parsing fails.
        ConfigurationError: The configuration has no materials directory.
        OSError: A configuration or material file cannot be read or written.
        yaml.YAMLError: The configuration file is not valid YAML.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='configuration file holding directory.materials_directory')
    parser.add_argument(
        '--work-dir',
        type=Path,
        default=harnesses.DEFAULT_WORK_DIR,
        help='directory the ZCode build scratch is created under, about 6 GB on local storage; used only when that '
        'distribution is absent, defaults to the system temp directory'
    )
    parser.add_argument('--tag', default=harnesses.DEFAULT_TAG, help='ZCode repository tag to build')
    parser.add_argument('--node-version', default=harnesses.DEFAULT_NODE_VERSION, help='Node version from the ZCode repository mise.toml')
    args = parser.parse_args()

    root = read_materials_directory(args.config).resolve()
    downloaded = 0
    existing = 0
    for module in (isos, harnesses, tools):
        module_tables = module.tables() if hasattr(module, 'tables') else (module.MATERIALS,)
        for table in module_tables:
            summary = download_material(table, module.BLOCKED, root)
            downloaded += summary['downloaded']
            existing += summary['existing']
    harnesses.write_install_script(root)
    tools.write_install_scripts(root)
    harnesses.ensure_zcode(root, args.work_dir.resolve(), args.tag, args.node_version)
    print(f'done: {downloaded} downloaded, {existing} already present', flush=True)


if __name__ == '__main__':
    main()
