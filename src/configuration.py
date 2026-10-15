"""Read shared Worker paths from the configuration file."""

from pathlib import Path

import yaml


class ConfigurationError(ValueError):
    """A required deployment setting is missing or invalid."""


def read_materials_directory(config_path: Path) -> Path:
    """
    Read the configured materials directory.

    Args:
        `config_path` (Path): Worker YAML configuration file.

    Returns:
        `materials_directory` (Path): Absolute directory containing the material tables.

    Raises:
        ConfigurationError: The materials directory is missing or invalid.
        OSError: The configuration file cannot be read.
        yaml.YAMLError: The configuration file is invalid YAML.
    """
    document = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    directories = document.get('directory') if isinstance(document, dict) else None
    directory = directories.get('materials_directory') if isinstance(directories, dict) else None
    if not isinstance(directory, str) or not directory.strip():
        raise ConfigurationError(f'{config_path} requires a non-empty directory.materials_directory string')
    return Path(directory).resolve()
