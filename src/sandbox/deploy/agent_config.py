"""
The agent configuration: the provider, model, effort, and harness one agent run uses.

These travel in a JSON file rather than on the command line, so a run is reproducible from one path. The
provider is named outright, which is what the credential table is keyed by, so no lookup has to infer it from
the model name.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Final

FIELDS: Final = ('provider', 'model', 'effort', 'harness')


class AgentConfigError(RuntimeError):
    """The agent configuration is unreadable, incomplete, or carries a field the runner does not know."""


@dataclass(frozen=True)
class AgentConfig:
    """One run's provider, model, effort, and harness spec."""

    provider: str
    model: str
    effort: str
    harness: str


def load_agent_config(path: Path) -> AgentConfig:
    """
    Read the agent configuration.

    Args:
        `path` (Path): JSON file holding `provider`, `model`, `effort`, and `harness`.

    Returns:
        `config` (AgentConfig): The parsed configuration.

    Raises:
        AgentConfigError: The file is missing, is not a JSON object, misses a field, holds an empty or non-string
            field, or carries an unknown field.
    """
    if not path.is_file():
        raise AgentConfigError(f'agent config not found: {path}')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise AgentConfigError(f'cannot read {path}: {error}') from error
    if not isinstance(data, dict):
        raise AgentConfigError(f'{path} must hold an object with {list(FIELDS)}')
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise AgentConfigError(f'{path} holds fields the run does not know: {unknown}')
    missing = [field for field in FIELDS if not isinstance(data.get(field), str) or not data.get(field)]
    if missing:
        raise AgentConfigError(f'{path} must hold non-empty {missing}')
    return AgentConfig(provider=data['provider'], model=data['model'], effort=data['effort'], harness=data['harness'])
