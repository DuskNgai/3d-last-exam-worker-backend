"""
Harness adapters: distribution lookup, guest configuration, run wrapper.

A harness distribution lives at harness_root/<name>/<version>/ and holds one
vendor distribution file plus an optional manifest.json with {"file", "sha256"}
entries, the same convention as tools bundles. The material root also carries the
shared install.sh that installs each supported shape into /task/harness, written there by
src/setup/download/harnesses.py and uploaded through the deploy.py bundle machinery.
This module covers what that generic script cannot: link the harness binary,
inject the model endpoint config, and render the non-interactive run wrapper.
Supporting another harness means one row in HARNESSES: its entry path and, when runnable, its adapter.
"""

import json
from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Final

from ..qga import QgaClient
from .guest import GuestError, write_file
from .qemu_vm import GUESTFWD_ADDRESS, GUESTFWD_PORT

GUESTFWD_BASE_URL: Final = f'http://{GUESTFWD_ADDRESS}:{GUESTFWD_PORT}/v1'
INSTALL_SCRIPT: Final = 'install.sh'
# A version directory holds one distribution file; everything else is metadata,
# including install.sh, which the deploy step injects into the guest-side bundle.
NON_PAYLOAD_NAMES: Final = ('manifest.json', 'install.sh')


class HarnessError(RuntimeError):
    """The harness name, version, or distribution directory is invalid."""


def distribution_dir(harness_root: Path, name: str, version: str) -> Path:
    """
    Return the harness distribution directory after validating its layout.

    The distribution file is whatever the vendor ships; install.sh extracts tar
    archives, decompresses gzip files, and copies other payloads.

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

    The script is repository content that src/setup/download/harnesses.py writes
    into the material root, so it travels with the distributions it unpacks,
    the way a tool bundle carries its own install.sh.

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


class HarnessAdapter:
    """Base class for one harness's guest-side setup and run wrapper."""

    name: str

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """Prepare the guest so the harness runs against the relay."""
        raise NotImplementedError

    def run_script(self) -> str:
        """Render the non-interactive wrapper the guest runs as the agent user."""
        raise NotImplementedError

    @staticmethod
    def require_entry(entry: str | None) -> str:
        """Return a declared entry path, or fail when the harness has none."""
        if entry is None:
            raise HarnessError('harness has no declared entry point')
        return entry

    @staticmethod
    def link_entry(qga: QgaClient, entry: str | None, command: str, label: str) -> None:
        """Link an installed entry into /usr/local/bin and check its version."""
        quoted_entry = shlex.quote(HarnessAdapter.require_entry(entry))
        result = qga.run(
            ['/bin/bash', '-c',
             f'ln -sfn {quoted_entry} /usr/local/bin/{command} && '
             f'runuser -u agent -- /usr/local/bin/{command} --version'],
            timeout=60.0,
        )
        if result.exitcode != 0:
            raise HarnessError(f'{label} binary link or smoke check failed: {result.stderr.decode()[:300]}')

    @staticmethod
    def check_version(qga: QgaClient, command: str, label: str) -> None:
        """Run the installed command's version check as the agent user."""
        result = qga.run(['/bin/bash', '-c', f'runuser -u agent -- /usr/local/bin/{command} --version'], timeout=60.0)
        if result.exitcode != 0:
            raise HarnessError(f'{label} binary smoke check failed: {result.stderr.decode()[:300]}')

    @staticmethod
    def write_wrapper(qga: QgaClient, command: str, entry: str | None, runner: str = '') -> None:
        """Write a /usr/local/bin wrapper that execs the installed entry."""
        target = f'{runner} {shlex.quote(HarnessAdapter.require_entry(entry))}'.strip()
        HarnessAdapter.write_guest_file(qga, f'/usr/local/bin/{command}', f'exec {target} "$@"\n', mode='755')

    @staticmethod
    def write_env(qga: QgaClient, values: dict[str, str]) -> None:
        """Write /task/harness-env.sh for the agent's shell."""
        content = ''.join(f'export {key}={shlex.quote(value)}\n' for key, value in values.items())
        HarnessAdapter.write_guest_file(qga, '/task/harness-env.sh', content)

    @staticmethod
    def write_guest_file(qga: QgaClient, path: str, content: str, mode: str = '644', owner: str = 'agent:agent') -> None:
        """Write a guest file and translate a GuestError into HarnessError."""
        try:
            write_file(qga, path, content, mode=mode, owner=owner)
        except GuestError as error:
            raise HarnessError(str(error)) from error


@dataclass(frozen=True)
class HarnessSupport:
    """Where one harness's entry lands and the adapter that runs it."""

    entry: str | None
    adapter: HarnessAdapter | None


class AntigravityAdapter(HarnessAdapter):
    """Antigravity CLI configured for Gemini API key authentication through the Worker relay."""

    name: Final = 'antigravity-cli'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """Configure API key mode and verify the CLI supports the selected model and one-shot prompt."""
        self.link_entry(qga, entry, 'agy', 'agy')

        help_result = qga.run(['/bin/bash', '-c', 'runuser -u agent -- /usr/local/bin/agy --help'], timeout=60.0)
        if help_result.exitcode != 0:
            raise HarnessError(f'agy help check failed: {help_result.stderr.decode()[:300]}')
        help_text = help_result.stdout.decode(errors='replace') + help_result.stderr.decode(errors='replace')
        if '--prompt' not in help_text or '--model' not in help_text or '--output-format' not in help_text:
            raise HarnessError('agy must support --prompt, --model, and --output-format for non-interactive structured output.')

        directory = qga.run([
            '/bin/bash', '-c',
            'install -d -m 0755 -o agent -g agent '
            '/home/agent/.gemini '
            '/home/agent/.gemini/config '
            '/home/agent/.gemini/config/projects '
            '/home/agent/.gemini/antigravity-cli'
        ], timeout=30.0)
        if directory.exitcode != 0:
            raise HarnessError(f'agy config directory setup failed: {directory.stderr.decode()[:300]}')
        settings_update = (
            'import json\n'
            'from pathlib import Path\n'
            'path = Path("/home/agent/.gemini/antigravity-cli/settings.json")\n'
            'contents = path.read_text() if path.is_file() else ""\n'
            'settings = json.loads(contents) if contents.strip() else {}\n'
            'if not isinstance(settings, dict): raise SystemExit("settings.json must contain a JSON object")\n'
            'settings["modelProvider"] = "gemini"\n'
            'permissions = settings.setdefault("permissions", {})\n'
            'if not isinstance(permissions, dict): raise SystemExit("settings.permissions must be a JSON object")\n'
            'allowed = permissions.setdefault("allow", [])\n'
            'if not isinstance(allowed, list): raise SystemExit("settings.permissions.allow must be a JSON array")\n'
            'if "command(*)" not in allowed: allowed.append("command(*)")\n'
            'if "read_file(*)" not in allowed: allowed.append("read_file(*)")\n'
            'permissions["allow"] = [rule for rule in allowed if not (isinstance(rule, str) and rule.startswith("write_file("))]\n'
            'path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\\n")\n'
        )
        result = qga.run([
            '/usr/sbin/runuser', '-u', 'agent', '--', 'python3', '-c', settings_update
        ], timeout=30.0)
        if result.exitcode != 0:
            raise HarnessError(f'agy API key settings update failed: {result.stderr.decode()[:300]}')

        self.write_env(qga, {
            'MODEL': env['MODEL'],
            'GEMINI_API_KEY': 'placeholder',
            'GOOGLE_GEMINI_BASE_URL': GUESTFWD_BASE_URL,
        })

    def run_script(self) -> str:
        """Run one prompt with Gemini API key authentication and preserve the command output."""
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
agy --model "$MODEL" --prompt "$(cat /task/instruction.md)" --output-format stream-json \\
  < /dev/null > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
events = [json.loads(line) for line in Path("/task/agent-logs/agent.stdout.log").read_text().splitlines() if line]
result = next(event for event in reversed(events) if event.get("type") == "result")
Path("/task/output/last-message.txt").write_text(result["result"])'
parse_status=$?
if [ "$parse_status" -ne 0 ] && [ "$status" -eq 0 ]; then status=$parse_status; fi
echo -n "$status" > /task/output/exitcode
exit "$status"
'''


class ClaudeCodeAdapter(HarnessAdapter):
    """Claude Code configured for an Anthropic-compatible endpoint."""

    name: Final = 'claude-code'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """
        Point Claude Code at the host relay without placing the real key in the guest.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: The binary link or smoke check failed, or the guest-side file write failed.
        """
        self.link_entry(qga, entry, 'claude', 'claude')
        self.write_env(qga, {
            'MODEL': env['MODEL'],
            'ANTHROPIC_BASE_URL': GUESTFWD_BASE_URL,
            'ANTHROPIC_API_KEY': 'placeholder',
        })

    def run_script(self) -> str:
        """
        Run one non-interactive Claude task and collect its stream and final answer.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
claude -p --output-format stream-json --verbose --dangerously-skip-permissions --no-session-persistence \\
  --model "$MODEL" "$(cat /task/instruction.md)" \\
  < /dev/null > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
events = [json.loads(line) for line in Path("/task/agent-logs/agent.stdout.log").read_text().splitlines() if line]
record = next(event for event in reversed(events) if event.get("type") == "result")
Path("/task/output/last-message.txt").write_text(record["result"])'
parse_status=$?
if [ "$parse_status" -ne 0 ] && [ "$status" -eq 0 ]; then status=$parse_status; fi
echo -n "$status" > /task/output/exitcode
exit "$status"
'''


class CodeBuddyAdapter(HarnessAdapter):
    """CodeBuddy Code configured with its documented OpenAI-compatible models file."""

    name: Final = 'codebuddy-code'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """
        Set the model catalog and link the packaged CLI.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: The binary link, smoke check, config directory setup, or guest-side file write failed.
        """
        self.link_entry(qga, entry, 'codebuddy', 'codebuddy')
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
        result = qga.run(['/bin/bash', '-c', 'install -d -o agent -g agent /home/agent/.codebuddy'], timeout=30.0)
        if result.exitcode != 0:
            raise HarnessError(f'codebuddy config directory setup failed: {result.stderr.decode()[:300]}')
        self.write_guest_file(qga, '/home/agent/.codebuddy/models.json', json.dumps(models, indent=2) + '\n')
        self.write_env(qga, {
            'MODEL': model,
            'OPENAI_BASE_URL': GUESTFWD_BASE_URL,
            'OPENAI_API_KEY': 'placeholder',
            'CODEBUDDY_IS_SANDBOX': '1',
        })

    def run_script(self) -> str:
        """
        Run CodeBuddy in print mode with the guest sandbox permission profile.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
codebuddy -p --output-format json --dangerously-skip-permissions --model "$MODEL" \\
  "$(cat /task/instruction.md)" < /dev/null \\
  > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
record = json.loads(Path("/task/agent-logs/agent.stdout.log").read_text())
Path("/task/output/last-message.txt").write_text(record.get("result", ""))' \\
  || true
echo -n "$status" > /task/output/exitcode
'''


class CodexAdapter(HarnessAdapter):
    """codex CLI: config.toml points at the guestfwd relay; the wrapper runs codex exec."""

    name: Final = 'codex'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
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
        self.link_entry(qga, entry, 'codex', 'codex')
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
        self.write_guest_file(qga, '/home/agent/.codex/config.toml', config)

    def run_script(self) -> str:
        """
        Render the wrapper; codex lands on PATH via the /usr/local/bin symlink in configure.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''
set -u
mkdir -p /task/output
export OPENAI_API_KEY=placeholder
cd /task/work
codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \\
  --disable responses_websockets --disable responses_websockets_v2 \\
  -C /task/work --json -o /task/output/last-message.txt "$(cat /task/instruction.md)" \\
  < /dev/null > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
echo -n $? > /task/output/exitcode
'''


class DeepSeekHarnessAdapter(HarnessAdapter):
    """DSH one-shot profile configured with an OpenAI-compatible relay route."""

    name: Final = 'deepseek-harness'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """Link DSH and write a profile patch for the selected model and relay."""
        self.link_entry(qga, entry, 'dsh', 'dsh')
        patch = f'''- id: agent-default-model
  config:
    provider: worker-relay
    model: {json.dumps(env['MODEL'])}
- id: llm-pi-ai
  config:
    providers:
      worker-relay:
        displayName: Worker relay
        apiKeyEnv: OPENAI_API_KEY
        api: openai-completions
        baseURL: {json.dumps(GUESTFWD_BASE_URL)}
        models:
          - id: {json.dumps(env['MODEL'])}
            name: {json.dumps(env['MODEL'])}
            contextWindow: 262144
            maxTokens: 8192
'''
        self.write_guest_file(qga, '/task/dsh.patch.yml', patch)
        self.write_env(qga, {'OPENAI_API_KEY': 'placeholder'})
        home = qga.run(
            ['/bin/bash', '-c', 'install -d -o agent -g agent /home/agent/.dsh'], timeout=30.0
        )
        if home.exitcode != 0:
            raise HarnessError(f'dsh home setup failed: {home.stderr.decode()[:300]}')

    def run_script(self) -> str:
        """Run one task through DSH headless mode and extract its final JSON event."""
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
export DSH_HOME=/home/agent/.dsh
cd /task/work
dsh --profile headless --patch /task/dsh.patch.yml --json "$(cat /task/instruction.md)" > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
events = [json.loads(line) for line in Path("/task/agent-logs/agent.stdout.log").read_text().splitlines() if line]
final = next((event for event in reversed(events) if event.get("type") == "final"), {})
Path("/task/output/last-message.txt").write_text(final.get("text", ""))' || true
echo -n "$status" > /task/output/exitcode
'''


class GrokBuildAdapter(HarnessAdapter):
    """Grok Build configured for an OpenAI-compatible custom model endpoint."""

    name: Final = 'grok-build'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
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
        result = qga.run(['/bin/bash', '-c', 'install -d -o agent -g agent /home/agent/.grok'], timeout=30.0)
        if result.exitcode != 0:
            raise HarnessError(f'grok config directory setup failed: {result.stderr.decode()[:300]}')
        self.write_guest_file(qga, '/home/agent/.grok/config.toml', config)
        self.write_env(qga, {'MODEL': env['MODEL'], 'EFFORT': env['EFFORT'], 'XAI_API_KEY': 'placeholder'})

    def run_script(self) -> str:
        """
        Run Grok Build in single-turn mode and persist its structured response.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
grok --model relay --single "$(cat /task/instruction.md)" --reasoning-effort "$EFFORT" \\
  --permission-mode bypassPermissions \\
  --output-format json > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
cp /task/agent-logs/agent.stdout.log /task/output/last-message.txt
echo -n "$status" > /task/output/exitcode
'''


class KimiCodeAdapter(HarnessAdapter):
    """Kimi Code configured for an OpenAI-compatible endpoint."""

    name: Final = 'kimi-code'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """
        Install the Node entry wrapper and provide the relay credentials.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: A guest-side file write failed, or the binary smoke check failed.
        """
        directory = qga.run(
            ['/bin/bash', '-c', 'install -d -o agent -g agent /home/agent/.kimi-code'], timeout=30.0
        )
        if directory.exitcode != 0:
            raise HarnessError(f'kimi config directory setup failed: {directory.stderr.decode()[:300]}')
        configuration = (
            f'default_model = "{env["MODEL"]}"\n\n'
            '[providers.worker-relay]\n'
            'type = "openai"\n'
            f'base_url = "{GUESTFWD_BASE_URL}"\n'
            'api_key_env = "OPENAI_API_KEY"\n\n'
            f'[models."{env["MODEL"]}"]\n'
            'provider = "worker-relay"\n'
            f'model = "{env["MODEL"]}"\n'
            'protocol = "openai"\n'
            'max_context_size = 262144\n'
        )
        self.write_wrapper(qga, 'kimi', entry, runner='/usr/local/bin/node')
        self.write_env(qga, {'MODEL': env['MODEL'], 'OPENAI_API_KEY': 'placeholder'})
        self.write_guest_file(qga, '/home/agent/.kimi-code/config.toml', configuration)
        self.check_version(qga, 'kimi', 'kimi')

    def run_script(self) -> str:
        """
        Run one Kimi prompt headlessly and preserve its stream output.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
kimi --model "$MODEL" --prompt "$(cat /task/instruction.md)" --output-format stream-json \\
  < /dev/null > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
cp /task/agent-logs/agent.stdout.log /task/output/last-message.txt
echo -n "$status" > /task/output/exitcode
'''


class MiniMaxCodeAdapter(HarnessAdapter):
    """MiniMax Code with a non-interactive OpenAI Responses custom provider."""

    name: Final = 'minimax-code'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """
        Register a relay provider using MiniMax's documented environment key input.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL and EFFORT.

        Raises:
            HarnessError: The binary link or smoke check failed, or the guest-side file write failed.
        """
        self.link_entry(qga, entry, 'mcode', 'mcode')
        self.write_env(qga, {
            'MODEL': env['MODEL'],
            'EFFORT': env['EFFORT'],
            'MCODE_PROVIDER_API_KEY': 'placeholder',
        })

    def run_script(self) -> str:
        """
        Register the provider, then run one non-interactive MiniMax task.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return f'''
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
  > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
echo -n "$status" > /task/output/exitcode
'''


class QwenCodeAdapter(HarnessAdapter):
    """Qwen Code configured to use an OpenAI-compatible relay provider."""

    name: Final = 'qwen-code'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """
        Install the custom OpenAI-compatible model in Qwen's user settings.

        Args:
            `qga` (QgaClient): Guest agent client used to run commands and write files.
            `env` (dict[str, str]): Model environment values, including MODEL.

        Raises:
            HarnessError: The binary link or smoke check failed, or the guest-side file write failed.
        """
        self.write_wrapper(qga, 'qwen', entry)
        self.check_version(qga, 'qwen', 'qwen')
        self.write_env(qga, {
            'MODEL': env['MODEL'],
            'OPENAI_API_KEY': 'placeholder',
            'OPENAI_BASE_URL': GUESTFWD_BASE_URL,
        })

    def run_script(self) -> str:
        """
        Run Qwen Code in print mode and save raw and final outputs.

        Returns:
            `script` (str): The bash wrapper the guest runs as the agent user.
        """
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
qwen --prompt "$(cat /task/instruction.md)" --model "$MODEL" --auth-type openai \\
  --openai-api-key "$OPENAI_API_KEY" --openai-base-url "$OPENAI_BASE_URL" \\
  --approval-mode yolo --output-format json \\
  > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
record = json.loads(Path("/task/agent-logs/agent.stdout.log").read_text())
Path("/task/output/last-message.txt").write_text(record.get("response", record.get("result", "")))' \\
  || true
echo -n "$status" > /task/output/exitcode
'''


class ZCodeAdapter(HarnessAdapter):
    """ZCode configured with its personal provider file and OpenAI-compatible relay."""

    name: Final = 'zcode'

    def configure(self, qga: QgaClient, env: dict[str, str], entry: str | None) -> None:
        """Create the selected Z.AI model route and write its default model selection."""
        self.write_wrapper(qga, 'zcode', entry, runner='/usr/local/bin/node')
        directory = qga.run(
            [
                '/bin/bash',
                '-c',
                'install -d -o agent -g agent '
                '/home/agent/.zcode/v2 '
                '/home/agent/.zcode/cli/db '
                '/home/agent/.zcode/cli/exec/bash-startup '
                '/home/agent/.zcode/cli/agents '
                '&& chown -R agent:agent /home/agent/.zcode '
                '&& chmod -R u+rwX /home/agent/.zcode',
            ],
            timeout=30.0,
        )
        if directory.exitcode != 0:
            raise HarnessError(f'zcode provider configuration directory setup failed: {directory.stderr.decode()[:300]}')
        self.check_version(qga, 'zcode', 'zcode')
        configuration = {
            'schemaVersion': 1,
            'config': {
                'providerOrder': ['worker-relay'],
                'providerConfigRules': {
                    'providerRules': [{
                        'providerId': 'worker-relay',
                        'templateId': 'zai-standard-api',
                        'providerName': 'Worker relay',
                        'config': {
                            'group': 'standard-personal',
                            'access': {'type': 'api-key', 'apiKey': 'placeholder'},
                            'api': {'type': 'openai-chat-completions', 'baseUrl': GUESTFWD_BASE_URL},
                            'personalModelIds': [env['MODEL']],
                            'modelOrder': [env['MODEL']]
                        }
                    }]
                },
                'modelConfigRules': {
                    'providerModelRules': [{
                        'providerId': 'worker-relay',
                        'modelId': env['MODEL'],
                        'config': {'enabled': True}
                    }],
                    'manualProviderModelRules': []
                },
                'defaultModelSelection': {
                    'providerId': 'worker-relay',
                    'modelId': env['MODEL'],
                    'options': {'reasoningLevel': env['EFFORT']}
                }
            }
        }
        self.write_guest_file(qga, '/home/agent/.zcode/v2/provider_config.json', json.dumps(configuration, indent=2) + '\n')
        self.write_env(qga, {
            'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE': '/task/harness/zcode/agent/provider/zcode-builtin.json',
            'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE': '/home/agent/.zcode/v2/provider_config.json',
        })

    def run_script(self) -> str:
        """Run a one-shot ZCode prompt and capture its text response."""
        return '''
set -u
mkdir -p /task/output
source /task/harness-env.sh
cd /task/work
zcode --prompt "$(cat /task/instruction.md)" --output-format stream-json --mode yolo --no-color > /task/agent-logs/agent.stdout.log 2> /task/agent-logs/agent.stderr.log
status=$?
python3 -c 'import json
from pathlib import Path
events = [json.loads(line) for line in Path("/task/agent-logs/agent.stdout.log").read_text().splitlines() if line]
result = next(event for event in reversed(events) if event.get("type") == "result")
Path("/task/output/last-message.txt").write_text(result["response"])'
parse_status=$?
if [ "$parse_status" -ne 0 ] && [ "$status" -eq 0 ]; then status=$parse_status; fi
echo -n "$status" > /task/output/exitcode
exit "$status"
'''


# The single harness table: where each installed entry lands and, when the run
# layer supports it, the adapter that configures and runs it. Rows are
# alphabetical by harness name. None as an entry means an uncompressed vendor
# payload is a single binary or AppImage and is its own entry. Nothing infers
# these paths: a new harness is added here by hand, and an entry named here but
# absent after install is a finding, not a guess.
HARNESSES: Final[dict[str, HarnessSupport]] = {
    'antigravity-cli': HarnessSupport('antigravity', AntigravityAdapter()),
    'claude-code': HarnessSupport('package/claude', ClaudeCodeAdapter()),
    'codebuddy-code': HarnessSupport('package/bin/codebuddy', CodeBuddyAdapter()),
    'codex': HarnessSupport('bin/codex', CodexAdapter()),
    'deepseek-harness': HarnessSupport('package/lib/bin.js', DeepSeekHarnessAdapter()),
    'grok-build': HarnessSupport(None, GrokBuildAdapter()),
    'kimi-code': HarnessSupport('package/dist/main.mjs', KimiCodeAdapter()),
    'kiro-cli': HarnessSupport('kirocli/bin/kiro-cli', None),
    'minimax-code': HarnessSupport('package/cli.js', MiniMaxCodeAdapter()),
    'mistral-vibe': HarnessSupport('vibe-acp', None),
    'muse-code': HarnessSupport(None, None),
    'qwen-code': HarnessSupport('qwen-code/bin/qwen', QwenCodeAdapter()),
    'trae-cli': HarnessSupport('traex-linux-x86_64', None),
    'zcode': HarnessSupport('zcode/agent/zcode.cjs', ZCodeAdapter()),
}


def entry_path(name: str) -> str | None:
    """
    Return the guest path of one harness entry.

    Args:
        `name` (str): Harness name registered in HARNESSES.

    Returns:
        `path` (str | None): Absolute path under /task/harness, or None when the vendor payload is the entry.
    """
    support = HARNESSES[name]
    return None if support.entry is None else f'/task/harness/{support.entry}'


def get_harness(name: str) -> HarnessAdapter:
    """
    Return the adapter registered for a harness name.

    Args:
        `name` (str): Harness name to look up in the adapter registry.

    Returns:
        `adapter` (HarnessAdapter): The adapter registered for the name.

    Raises:
        HarnessError: The harness is unknown or has no runnable adapter.
    """
    support = HARNESSES.get(name)
    if support is None:
        raise HarnessError(f'unknown harness {name!r}; known harnesses: {sorted(HARNESSES)}')
    if support.adapter is None:
        raise HarnessError(f'harness {name!r} has no runnable adapter')
    return support.adapter
