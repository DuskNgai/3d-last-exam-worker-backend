"""
Download and verify every material in one run.

    python scripts/setup/materials.py --config src/configuration.yaml

The materials directory comes from directory.materials_directory in the configuration
file; the ISO, harness, and tool tables then run in one pass, the harness and tool
install scripts are refreshed from the repository assets, conda-packages.json is
regenerated, and the ZCode source build is handled as in harnesses.py.
"""

import argparse
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import download_material
from download import harnesses, isos, tools


class ConfigError(RuntimeError):
    """The configuration file has no directory.materials_directory string."""


def read_materials_directory(config_path: Path) -> Path:
    """
    Read the materials directory from the directory group of the configuration file.

    Args:
        `config_path` (Path): Configuration file holding directory.materials_directory.

    Returns:
        `materials_directory` (Path): Directory the material tables are relative to.

    Raises:
        ConfigError: No materials_directory string exists under directory.
        OSError: The configuration file cannot be read.
        yaml.YAMLError: The configuration file is not valid YAML.
    """
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    directories = config.get('directory') if isinstance(config, dict) else None
    directory = directories.get('materials_directory') if isinstance(directories, dict) else None
    if not isinstance(directory, str) or not directory:
        raise ConfigError(f'{config_path} must hold a "materials_directory" string under "directory"')
    return Path(directory)


def main() -> None:
    """
    CLI entry point.

    Raises:
        SystemExit: Argument parsing fails.
        ConfigError: The configuration has no materials directory.
        OSError: A configuration or material file cannot be read or written.
        yaml.YAMLError: The configuration file is not valid YAML.
    """
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
    tools.write_conda_packages(root)
    harnesses.ensure_zcode(root, args.work_dir.resolve() if args.work_dir else None, args.tag, args.node_version, args.keep_work)
    print(f'done: {downloaded} downloaded, {existing} already present', flush=True)


if __name__ == '__main__':
    main()
