"""
Model credentials: the endpoint and key one provider entry carries.

The credential table maps a provider stem, the identity the agent config names, to the endpoint that serves it and
the API key for that endpoint. Neither value leaves the host; the guest only ever sees a placeholder credential.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Final

KEYS_FILENAME: Final = '.keys.json'
ENTRY_FIELDS: Final = ('BASE_URL', 'API_KEY')


class CredentialsError(RuntimeError):
    """The credential table is unreadable, incomplete, or holds no entry for a provider."""


@dataclass(frozen=True)
class KeyProfile:
    """
    One credential entry.

    `name` is the provider stem the entry is keyed by, `base_url` the real endpoint, and `api_key` the real key.
    """

    name: str
    base_url: str
    api_key: str


def load_keys(path: Path) -> tuple[KeyProfile, ...]:
    """
    Read the credential table.

    Args:
        `path` (Path): JSON file mapping a provider stem to BASE_URL and API_KEY.

    Returns:
        `profiles` (tuple[KeyProfile, ...]): Entries in file order.

    Raises:
        CredentialsError: The file is missing, is not a JSON object of entries, or holds an entry without the
            two fields or with a field of the wrong type.
    """
    if not path.is_file():
        raise CredentialsError(f'credential file not found: {path}')
    try:
        table = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise CredentialsError(f'cannot read {path}: {error}') from error
    if not isinstance(table, dict) or not table:
        raise CredentialsError(f'{path} must hold an object of labeled credential entries')
    return tuple(_entry(path, name, entry) for name, entry in table.items())


def _entry(path: Path, name: str, entry: object) -> KeyProfile:
    """
    Validate one table entry.

    Args:
        `path` (Path): File the entry came from, named in the error.
        `name` (str): Label of the entry.
        `entry` (object): Parsed JSON value of the entry.

    Returns:
        `profile` (KeyProfile): The validated entry.

    Raises:
        CredentialsError: The entry is not an object with two non-empty string fields.
    """
    if not isinstance(entry, dict):
        raise CredentialsError(f'{path}: entry {name!r} must be an object')
    missing = [field for field in ENTRY_FIELDS if field not in entry]
    if missing:
        raise CredentialsError(f'{path}: entry {name!r} is missing {missing}')
    for field in ENTRY_FIELDS:
        if not isinstance(entry[field], str) or not entry[field]:
            raise CredentialsError(f'{path}: entry {name!r} must hold a non-empty {field}')
    return KeyProfile(name=name, base_url=entry['BASE_URL'], api_key=entry['API_KEY'])


def find_profile(profiles: tuple[KeyProfile, ...], name: str) -> KeyProfile:
    """
    Return the entry keyed by one provider stem.

    Args:
        `profiles` (tuple[KeyProfile, ...]): Entries from load_keys.
        `name` (str): Provider stem to look up, as the agent config names it and the relay receives it.

    Returns:
        `profile` (KeyProfile): The matching entry.

    Raises:
        CredentialsError: No entry carries that stem.
    """
    for profile in profiles:
        if profile.name == name:
            return profile
    raise CredentialsError(f'no credential entry for provider {name!r}; the table holds {sorted(profile.name for profile in profiles)}')
