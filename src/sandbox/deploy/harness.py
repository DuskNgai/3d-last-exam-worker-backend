"""
Harness adapters: distribution lookup, guest configuration, run wrapper.

A harness distribution lives at harness_root/<name>/<version>/ and holds one
vendor distribution file plus an optional manifest.json with {"file", "sha256"}
entries, the same convention as tools bundles. The material root also carries the
shared install.sh that unpacks either shape into /task/harness, uploaded through
the deploy.py bundle machinery. This module covers what that generic script
cannot: link the harness binary, inject the model endpoint config, and render the
non-interactive run wrapper. Supporting another harness means one adapter entry
in HARNESSES.
"""

import json
from pathlib import Path
import shlex
from typing import Final, Protocol

from ..qga import QgaClient
from .guest import GuestError, write_file
from .qemu_vm import GUESTFWD_ADDRESS, GUESTFWD_PORT

GUESTFWD_BASE_URL: Final = f'http://{GUESTFWD_ADDRESS}:{GUESTFWD_PORT}/v1'
INSTALL_SCRIPT: Final = 'install.sh'
# A version directory holds one distribution file; everything else is metadata,
# including install.sh, which the deploy step injects into the guest-side bundle.
NON_PAYLOAD_NAMES: Final = ('manifest.json', 'install.sh')
# Where each harness's runnable entry lands inside /task/harness once installed.
# None means the vendor payload is a single binary or AppImage and is its own
# entry. Nothing infers these paths: a new harness is added here by hand, and an
# entry named here but absent after install is a finding, not a guess.
ENTRY_POINTS: Final[dict[str, str | None]] = {
    'antigravity-cli': 'antigravity',
    'claude-code': 'package/claude',
    'codebuddy-code': 'package/bin/codebuddy',
    'codex': 'bin/codex',
    'deepseek-harness': 'package/lib/bin.js',
    'grok-build': None,
    'kimi-code': 'package/dist/main.mjs',
    'kiro-cli': 'kirocli/bin/kiro-cli',
    'minimax-code': 'package/cli.js',
    'mistral-vibe': 'vibe-acp',
    'muse-code': None,
    'qwen-code': 'qwen-code/bin/qwen',
    'trae-cli': 'trae-cli',
    'zcode': 'zcode/bin/zcode.mjs',
}


class HarnessError(RuntimeError):
    """The harness name, version, or distribution directory is invalid."""


def distribution_dir(harness_root: Path, name: str, version: str) -> Path:
    """
    Return the harness distribution directory after validating its layout.

    The distribution file is whatever the vendor ships: a tar archive, or a bare
    binary that install.sh copies in as a single file.

    Args:
        `harness_root` (Path): Root directory holding the harness distributions.
        `name` (str): Harness name, matching a subdirectory of the root.
        `version` (str): Version subdirectory to resolve.

    Returns:
        `directory` (Path): Resolved directory holding exactly one distribution file.

    Raises:
        HarnessError: The directory is missing or does not hold exactly one distribution file.
    """
    directory = (harness_root / name / version).resolve()
    if not directory.is_dir():
        raise HarnessError(f'harness distribution not found: {directory}')
    payloads = [path for path in directory.iterdir() if path.is_file() and path.name not in NON_PAYLOAD_NAMES]
    if len(payloads) != 1:
        raise HarnessError(f'{directory} must hold exactly one distribution file, found {sorted(path.name for path in payloads)}')
    return directory


def install_script(harness_root: Path) -> Path:
    """
    Return the install script the material root carries for every harness.

    The script is material rather than repository content: it travels with the
    distributions it unpacks, the way a tool bundle carries its own install.sh.

    Args:
        `harness_root` (Path): Root directory holding the harness distributions.

    Returns:
        `script` (Path): Resolved path to the shared install script.

    Raises:
        HarnessError: The material root holds no install script.
    """
    script = (harness_root / INSTALL_SCRIPT).resolve()
    if not script.is_file():
        raise HarnessError(f'harness install script not found: {script}; the material root must carry {INSTALL_SCRIPT}')
    return script


class SupportsHarness(Protocol):
    """What the registry and the run stage need from a harness adapter."""

    name: str

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """Prepare the guest so the harness binary can run against the relay."""
        ...

    def run_script(self) -> str:
        """Render the non-interactive wrapper the guest runs as the agent user."""
        ...


class CodexAdapter:
    """codex CLI: config.toml points at the guestfwd relay; the wrapper runs codex exec."""

    name: Final = 'codex'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Link the codex binary and point the agent user's codex at the guestfwd relay.

        codex ignores OPENAI_BASE_URL; a custom model provider in config.toml is the
        reliable route. supports_websockets = false keeps it on plain HTTPS SSE.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL and EFFORT.

        Raises:
            HarnessError: The guest-side setup failed.
        """
        result = qga.run(['/bin/bash', '-c', 'mkdir -p /home/agent/.codex && chown agent:agent /home/agent/.codex'], timeout=30.0)
        if result.exitcode != 0:
            raise HarnessError(f'codex home setup failed: {result.stderr.decode()[:300]}')
        entry = f'/task/harness/{ENTRY_POINTS[self.name]}'
        link = qga.run(
            ['/bin/bash', '-c', f'ln -sfn {entry} /usr/local/bin/codex && runuser -u agent -- /usr/local/bin/codex --version'],
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
        """
        Render the wrapper; codex lands on PATH via the /usr/local/bin symlink in configure.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
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


class ClaudeCodeAdapter:
    """Claude Code configured for an Anthropic-compatible endpoint."""

    name: Final = 'claude-code'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Point Claude Code at the host relay without placing the real key in the guest.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: The binary link or smoke check failed, or the guest-side file write failed.
        """
        entry = f'/task/harness/{ENTRY_POINTS[self.name]}'
        command = (f'ln -sfn {entry} /usr/local/bin/claude && '
                   'runuser -u agent -- /usr/local/bin/claude --version')
        result = qga.run(['/bin/bash', '-c', command], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'claude binary link or smoke check failed: {result.stderr.decode()[:300]}')
        try:
            write_file(
                qga,
                '/task/harness-env.sh',
                f'export MODEL={shlex.quote(env["MODEL"])}\n'
                f'export ANTHROPIC_BASE_URL={shlex.quote(GUESTFWD_BASE_URL)}\n'
                'export ANTHROPIC_API_KEY=placeholder-managed-by-relay\n',
                mode='644',
                owner='agent:agent'
            )
        except GuestError as error:
            raise HarnessError(str(error)) from error

    def run_script(self) -> str:
        """
        Run one non-interactive Claude task and collect its stream and final answer.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return f'''#!/bin/bash
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
claude -p --output-format json --dangerously-skip-permissions --no-session-persistence \\
  --model "$MODEL" "$(cat /task/instruction.md)" \\
  < /dev/null > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
record = json.loads(Path("/task/output/agent-log.jsonl").read_text())
Path("/task/output/last-message.txt").write_text(record.get("result", ""))' \\
  || true
echo -n "$status" > /task/output/exitcode
'''


class QwenCodeAdapter:
    """Qwen Code configured to use an OpenAI-compatible relay provider."""

    name: Final = 'qwen-code'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Install the custom OpenAI-compatible model in Qwen's user settings.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: The binary link or smoke check failed, or the guest-side file write failed.
        """
        entry = f'/task/harness/{ENTRY_POINTS[self.name]}'
        command = (f'ln -sfn {entry} /usr/local/bin/qwen && '
                   'runuser -u agent -- /usr/local/bin/qwen --version')
        result = qga.run(['/bin/bash', '-c', command], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'qwen binary link or smoke check failed: {result.stderr.decode()[:300]}')
        try:
            write_file(
                qga,
                '/task/harness-env.sh',
                f'export MODEL={shlex.quote(env["MODEL"])}\n'
                'export OPENAI_API_KEY=placeholder-managed-by-relay\n'
                f'export OPENAI_BASE_URL={shlex.quote(GUESTFWD_BASE_URL)}\n',
                owner='agent:agent'
            )
        except GuestError as error:
            raise HarnessError(str(error)) from error

    def run_script(self) -> str:
        """
        Run Qwen Code in print mode and save raw and final outputs.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''#!/bin/bash
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
qwen --prompt "$(cat /task/instruction.md)" --model "$MODEL" --auth-type openai \\
  --openai-api-key "$OPENAI_API_KEY" --openai-base-url "$OPENAI_BASE_URL" \\
  --approval-mode yolo --output-format json \\
  > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
record = json.loads(Path("/task/output/agent-log.jsonl").read_text())
Path("/task/output/last-message.txt").write_text(record.get("response", record.get("result", "")))' \\
  || true
echo -n "$status" > /task/output/exitcode
'''


class KimiCodeAdapter:
    """Kimi Code configured for an Anthropic-compatible endpoint."""

    name: Final = 'kimi-code'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Install the Node entry wrapper and provide the relay credentials.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: A guest-side file write failed, or the binary smoke check failed.
        """
        try:
            write_file(
                qga, '/usr/local/bin/kimi', '#!/bin/bash\nexec /usr/local/bin/node /task/harness/package/dist/main.mjs "$@"\n', mode='755'
            )
            write_file(
                qga,
                '/task/harness-env.sh',
                f'export MODEL={shlex.quote(env["MODEL"])}\n'
                f'export ANTHROPIC_BASE_URL={shlex.quote(GUESTFWD_BASE_URL)}\n'
                'export ANTHROPIC_API_KEY=placeholder-managed-by-relay\n',
                owner='agent:agent'
            )
        except GuestError as error:
            raise HarnessError(str(error)) from error
        result = qga.run(['/bin/bash', '-c', 'runuser -u agent -- /usr/local/bin/kimi --version'], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'kimi binary smoke check failed: {result.stderr.decode()[:300]}')

    def run_script(self) -> str:
        """
        Run one Kimi prompt headlessly and preserve its stream output.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''#!/bin/bash
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
kimi --model "$MODEL" --prompt "$(cat /task/instruction.md)" --output-format stream-json \\
  < /dev/null > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
status=$?
cp /task/output/agent-log.jsonl /task/output/last-message.txt
echo -n "$status" > /task/output/exitcode
'''


class GrokBuildAdapter:
    """Grok Build configured for an OpenAI-compatible custom model endpoint."""

    name: Final = 'grok-build'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Set the custom model endpoint and provide a placeholder API key.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL and EFFORT.

        Raises:
            HarnessError: The binary link, smoke check, config directory setup, or guest-side file write failed.
        """
        command = (
            'bin=$(find /task/harness -maxdepth 1 -type f -print -quit) && '
            'test -n "$bin" && ln -sfn "$bin" /usr/local/bin/grok && '
            'runuser -u agent -- /usr/local/bin/grok --version'
        )
        result = qga.run(['/bin/bash', '-c', command], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'grok binary link or smoke check failed: {result.stderr.decode()[:300]}')
        config = (
            'default_model = "relay"\n'
            '\n[model.relay]\n'
            f'model = {json.dumps(env["MODEL"])}\n'
            f'base_url = {json.dumps(GUESTFWD_BASE_URL)}\n'
            'api_backend = "responses"\n'
            'env_key = "XAI_API_KEY"\n'
        )
        try:
            result = qga.run(['/bin/bash', '-c', 'install -d -o agent -g agent /home/agent/.grok'], timeout=30.0)
            if result.exitcode != 0:
                raise HarnessError(f'grok config directory setup failed: {result.stderr.decode()[:300]}')
            write_file(qga, '/home/agent/.grok/config.toml', config, owner='agent:agent')
            write_file(
                qga,
                '/task/harness-env.sh',
                f'export MODEL={shlex.quote(env["MODEL"])}\n'
                f'export EFFORT={shlex.quote(env["EFFORT"])}\n'
                'export XAI_API_KEY=placeholder-managed-by-relay\n',
                owner='agent:agent'
            )
        except GuestError as error:
            raise HarnessError(str(error)) from error

    def run_script(self) -> str:
        """
        Run Grok Build in single-turn mode and persist its structured response.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''#!/bin/bash
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
grok --model relay --single "$(cat /task/instruction.md)" --reasoning-effort "$EFFORT" \\
  --permission-mode bypassPermissions \\
  --output-format json > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
status=$?
cp /task/output/agent-log.jsonl /task/output/last-message.txt
echo -n "$status" > /task/output/exitcode
'''


class MiniMaxCodeAdapter:
    """MiniMax Code with a non-interactive OpenAI Responses custom provider."""

    name: Final = 'minimax-code'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Register a relay provider using MiniMax's documented environment key input.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL and EFFORT.

        Raises:
            HarnessError: The binary link or smoke check failed, or the guest-side file write failed.
        """
        entry = f'/task/harness/{ENTRY_POINTS[self.name]}'
        command = (f'ln -sfn {entry} /usr/local/bin/mcode && '
                   'runuser -u agent -- /usr/local/bin/mcode --version')
        result = qga.run(['/bin/bash', '-c', command], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'mcode binary link or smoke check failed: {result.stderr.decode()[:300]}')
        try:
            write_file(
                qga,
                '/task/harness-env.sh',
                f'export MODEL={shlex.quote(env["MODEL"])}\n'
                f'export EFFORT={shlex.quote(env["EFFORT"])}\n'
                'export MCODE_PROVIDER_API_KEY=placeholder-managed-by-relay\n',
                owner='agent:agent'
            )
        except GuestError as error:
            raise HarnessError(str(error)) from error

    def run_script(self) -> str:
        """
        Register the provider, then run one non-interactive MiniMax task.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return f'''#!/bin/bash
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
mcode provider add --name relay --base-url {shlex.quote(GUESTFWD_BASE_URL)} \\
  --api-format openai-responses --model "$MODEL" --api-key-env MCODE_PROVIDER_API_KEY
provider_status=$?
if [ "$provider_status" -ne 0 ]; then
  echo -n "$provider_status" > /task/output/exitcode
  exit 0
fi
mcode exec --model "custom_provider:relay/$MODEL" --effort "$EFFORT" \\
  --prompt-mode work --permission off --output-format stream-json \\
  --output-last-message /task/output/last-message.txt "$(cat /task/instruction.md)" \\
  > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
status=$?
echo -n "$status" > /task/output/exitcode
'''


class CodeBuddyAdapter:
    """CodeBuddy Code configured with its documented OpenAI-compatible models file."""

    name: Final = 'codebuddy-code'

    def configure(self, qga: QgaClient, env: dict[str, str]) -> None:
        """
        Set the model catalog and link the packaged CLI.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: The binary link, smoke check, config directory setup, or guest-side file write failed.
        """
        entry = f'/task/harness/{ENTRY_POINTS[self.name]}'
        command = (f'ln -sfn {entry} /usr/local/bin/codebuddy && '
                   'runuser -u agent -- /usr/local/bin/codebuddy --version')
        result = qga.run(['/bin/bash', '-c', command], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'codebuddy binary link or smoke check failed: {result.stderr.decode()[:300]}')
        model = env['MODEL']
        models = {
            'models': [{
                'id': model,
                'name': model,
                'vendor': 'OpenAI-compatible relay',
                'apiKey': '${OPENAI_API_KEY}',
                'url': '${OPENAI_BASE_URL}/chat/completions',
                'supportsToolCall': True
            }],
            'availableModels': [model]
        }
        try:
            result = qga.run(['/bin/bash', '-c', 'install -d -o agent -g agent /home/agent/.codebuddy'], timeout=30.0)
            if result.exitcode != 0:
                raise HarnessError(f'codebuddy config directory setup failed: {result.stderr.decode()[:300]}')
            write_file(qga, '/home/agent/.codebuddy/models.json', json.dumps(models, indent=2) + '\n', owner='agent:agent')
            write_file(
                qga,
                '/task/harness-env.sh',
                f'export MODEL={shlex.quote(model)}\n'
                f'export OPENAI_BASE_URL={shlex.quote(GUESTFWD_BASE_URL)}\n'
                'export OPENAI_API_KEY=placeholder-managed-by-relay\n'
                'export CODEBUDDY_IS_SANDBOX=1\n',
                owner='agent:agent'
            )
        except GuestError as error:
            raise HarnessError(str(error)) from error

    def run_script(self) -> str:
        """
        Run CodeBuddy in print mode with the guest sandbox permission profile.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''#!/bin/bash
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
codebuddy -p --output-format json --dangerously-skip-permissions --model "$MODEL" \\
  "$(cat /task/instruction.md)" < /dev/null \\
  > /task/output/agent-log.jsonl 2> /task/output/agent-stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
record = json.loads(Path("/task/output/agent-log.jsonl").read_text())
Path("/task/output/last-message.txt").write_text(record.get("result", ""))' \\
  || true
echo -n "$status" > /task/output/exitcode
'''


HARNESSES: Final[dict[str, SupportsHarness]] = {
    'codex': CodexAdapter(),
    'claude-code': ClaudeCodeAdapter(),
    'qwen-code': QwenCodeAdapter(),
    'kimi-code': KimiCodeAdapter(),
    'grok-build': GrokBuildAdapter(),
    'minimax-code': MiniMaxCodeAdapter(),
    'codebuddy-code': CodeBuddyAdapter()
}


def get_harness(name: str) -> SupportsHarness:
    """
    Return the adapter registered for a harness name.

    Args:
        `name` (str): Harness name to look up in the adapter registry.

    Returns:
        `adapter` (SupportsHarness): The adapter registered for the name.

    Raises:
        HarnessError: The harness is not supported yet.
    """
    try:
        return HARNESSES[name]
    except KeyError:
        raise HarnessError(f'no runnable adapter for harness {name!r}; available adapters: {sorted(HARNESSES)}') from None
