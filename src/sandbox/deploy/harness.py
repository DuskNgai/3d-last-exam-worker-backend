"""Harness adapters: distribution lookup, guest configuration, run wrapper.

A harness distribution lives at harness_root/<name>/<version>/ and holds the
vendor archive plus an optional manifest.json with {"file", "sha256"} entries,
the same convention as tools bundles. Upload and installation reuse the
deploy.py bundle machinery with the generic scripts/install_harness.sh, which
verifies the manifest and extracts the archive to /task/harness. This module
covers what a generic install script cannot: link the harness binary, inject
the model endpoint config, and render the non-interactive run wrapper.
Supporting another harness means one adapter entry in HARNESSES.
"""

from pathlib import Path
from typing import Final

from ..qga import QgaClient
from .guest import GuestError, write_file
from .qemu_vm import GUESTFWD_ADDRESS, GUESTFWD_PORT

GUESTFWD_BASE_URL: Final = f'http://{GUESTFWD_ADDRESS}:{GUESTFWD_PORT}/v1'
ARCHIVE_SUFFIXES: Final = ('.tar.gz', '.tgz', '.zip')
INSTALL_SCRIPT: Final = 'install_harness.sh'


class HarnessError(RuntimeError):
    """The harness name, version, or distribution directory is invalid."""


def distribution_dir(harness_root: Path, name: str, version: str) -> Path:
    """Return the harness distribution directory after validating its layout.

    Raises:
        HarnessError: The directory is missing or does not hold exactly one archive.
    """
    directory = (harness_root / name / version).resolve()
    if not directory.is_dir():
        raise HarnessError(f'harness distribution not found: {directory}')
    archives = [path for path in directory.iterdir() if path.is_file() and path.name.endswith(ARCHIVE_SUFFIXES)]
    if len(archives) != 1:
        raise HarnessError(f'{directory} must hold exactly one archive {ARCHIVE_SUFFIXES}, found {len(archives)}')
    return directory


class CodexAdapter:
    """codex CLI: config.toml points at the guestfwd relay; the wrapper runs codex exec."""

    name: Final = 'codex'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """Link the codex binary and point the agent user's codex at the guestfwd relay.

        codex ignores OPENAI_BASE_URL; a custom model provider in config.toml is the
        reliable route. supports_websockets = false keeps it on plain HTTPS SSE.

        Raises:
            HarnessError: The guest-side setup failed.
        """
        result = qga.run(['/bin/bash', '-c', 'mkdir -p /home/agent/.codex && chown agent:agent /home/agent/.codex'], timeout=30.0)
        if result.exitcode != 0:
            raise HarnessError(f'codex home setup failed: {result.stderr.decode()[:300]}')
        link = qga.run(
            [
                '/bin/bash',
                '-c',
                'ln -sfn /task/harness/bin/codex /usr/local/bin/codex && '
                'runuser -u agent -- /usr/local/bin/codex --version'
            ],
            timeout=60.0,
        )
        if link.exitcode != 0:
            raise HarnessError(f'codex binary link or smoke check failed: {link.stderr.decode()[:300]}')
        config = f'''model = "{env['MODEL']}"
model_reasoning_effort = "{env['EFFORT']}"
model_provider = "relay"

[model_providers.relay]
name = "relay"
base_url = "{GUESTFWD_BASE_URL}"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false
'''
        try:
            write_file(qga, '/home/agent/.codex/config.toml', config, owner='agent:agent')
        except GuestError as error:
            raise HarnessError(str(error)) from error

    def run_script(self) -> str:
        """Render the wrapper; codex lands on PATH via the /usr/local/bin symlink in configure."""
        return '''#!/bin/bash
set -u
mkdir -p /task/output
export OPENAI_API_KEY=placeholder-managed-by-relay
cd /task/work
codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \\
  --disable responses_websockets --disable responses_websockets_v2 \\
  -C /task/work --json -o /task/output/last-message.txt "$(cat /task/instruction.md)" \\
  < /dev/null > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
echo -n $? > /task/output/exitcode
'''


HARNESSES: Final = {
    'codex': CodexAdapter()
}


def get_harness(name: str) -> CodexAdapter:
    """Return the adapter registered for a harness name.

    Raises:
        HarnessError: The harness is not supported yet.
    """
    try:
        return HARNESSES[name]
    except KeyError:
        raise HarnessError(f'unsupported harness {name!r}; supported: {sorted(HARNESSES)}') from None
