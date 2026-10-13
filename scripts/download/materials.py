"""
Download and verify every material in one run.

    python3 scripts/download/materials.py --config src/configuration.yaml

The materials directory comes from directory.materials_directory in the configuration
file; the ISO, harness, and tool tables then run in one pass, with the ZCode
source build handled as in harnesses.py.
"""

import argparse
from pathlib import Path
import subprocess
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import download_material, MaterialError
import harnesses
import isos
import tools


class ConfigError(RuntimeError):
    """The configuration file is unreadable or holds no directory.materials_directory."""


def read_materials_directory(config_path: Path) -> Path:
    """
    Read the materials directory from the directory group of the configuration file.

    Args:
        `config_path` (Path): Configuration file holding directory.materials_directory.

    Returns:
        `materials_directory` (Path): Directory the material tables are relative to.

    Raises:
        ConfigError: The file is unreadable, is not a YAML mapping, or holds no materials_directory string
            under directory.
    """
    try:
        config = yaml.safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f'cannot read {config_path}: {error}') from error
    directories = config.get('directory') if isinstance(config, dict) else None
    directory = directories.get('materials_directory') if isinstance(directories, dict) else None
    if not isinstance(directory, str) or not directory:
        raise ConfigError(f'{config_path} must hold a "materials_directory" string under "directory"')
    return Path(directory)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='configuration file holding directory.materials_directory')
    parser.add_argument(
        '--work-dir',
        type=Path,
        default=None,
        help='scratch directory for the ZCode build, about 6 GB on local storage; needed only when that distribution is absent'
    )
    parser.add_argument('--tag', default=harnesses.DEFAULT_TAG, help='ZCode repository tag to build')
    parser.add_argument('--node-version', default=harnesses.DEFAULT_NODE_VERSION, help='Node version from the ZCode repository mise.toml')
    parser.add_argument('--keep-work', action='store_true', help='keep the ZCode scratch directory')
    args = parser.parse_args()
    try:
        root = read_materials_directory(args.config).resolve()
        downloaded = 0
        existing = 0
        for module in (isos, harnesses, tools):
            summary = download_material(module.MATERIALS, module.BLOCKED, root)
            downloaded += summary['downloaded']
            existing += summary['existing']
        harnesses.ensure_zcode(root, args.work_dir.resolve() if args.work_dir else None, args.tag, args.node_version, args.keep_work)
    except (ConfigError, MaterialError, harnesses.ZcodeBuildError, subprocess.CalledProcessError, OSError) as error:
        print(f'material failed: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1) from error
    print(f'done: {downloaded} downloaded, {existing} already present', flush=True)


if __name__ == '__main__':
    main()
