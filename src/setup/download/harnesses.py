"""
Download and verify the harness material, write the shared install script, and build the distribution that is not downloadable.

    python src/setup/download/harnesses.py --config src/configuration.yaml
    python src/setup/download/harnesses.py --config src/configuration.yaml --work-dir <work dir>

The table pins each vendor distribution under harnesses/<name>/<version>/. Files
already on disk are skipped and re-verified, so one run over a complete root is a
full verification pass. ZCode is the exception: the vendor publishes no CLI
distribution, so this script builds it from source into harnesses/zcode/<version>/
when nothing is there, and otherwise checks the tarball against the manifest.json
that build wrote. The shared install.sh the deploy step injects into every harness
bundle is repository content at src/setup/download/assets/install-harness.sh; each
run refreshes the copy at harnesses/install.sh. The Ubuntu ISO lives in isos.py
and tool packages in tools.py.
"""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from configuration import read_materials_directory
from setup.common import Blocked, download_material, fetch, Material, sha256

MATERIALS: Final = (
    Material(
        'Antigravity CLI',
        '1.2.11',
        '2026-09-25T04:22:11Z',
        'https://github.com/google-antigravity/antigravity-cli/releases/download/1.2.11/agy_cli_linux_x64.tar.gz',
        'c91c62c5e6fa954f5a7e1d7b9ad417d749db4aa60a4ba0b3d604dec1b645d190',
        'harnesses/antigravity-cli/1.2.11/agy_cli_linux_x64.tar.gz'
    ),
    Material(
        'Claude Code',
        '2.1.283',
        '2026-09-25T18:49:30.967Z',
        'https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.283.tgz',
        'd14ec0fca400151092c926928fea7e8e38231f7a90e6e1f8eeb2a55c24676afb',
        'harnesses/claude-code/2.1.283/claude-code-linux-x64-2.1.283.tgz'
    ),
    Material(
        'CodeBuddy Code',
        '2.158.0',
        '2026-09-24T14:09:51.432Z',
        'https://registry.npmjs.org/@tencent-ai/codebuddy-code/-/codebuddy-code-2.158.0.tgz',
        '4396a3997906521fe3a8ead7059d61840025b664ca4f65ce6f205960a9f44e0e',
        'harnesses/codebuddy-code/2.158.0/codebuddy-code-2.158.0.tgz'
    ),
    Material(
        'Codex CLI',
        '0.157.0',
        '2026-09-25T02:31:06Z',
        'https://github.com/openai/codex/releases/download/rust-v0.157.0/codex-package-x86_64-unknown-linux-musl.tar.gz',
        '042f851ea3fc1083c45157520520944fc790632b53ebc580fc98eaca55862a25',
        'harnesses/codex/0.157.0/codex-package-x86_64-unknown-linux-musl.tar.gz'
    ),
    Material(
        'DeepSeek Harness',
        '0.1.7-rc.2',
        '2026-09-24T14:18:22Z',
        'https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.1.7-rc.2.tgz',
        '5f2da7272d9485abc223e681075809a8d929697c5232ee445718e1b7e066bff8',
        'harnesses/deepseek-harness/0.1.7-rc.2/dsh-0.1.7-rc.2.tgz'
    ),
    Material(
        'Grok Build',
        '1.0.41',
        '2026-09-22T16:34:24Z',
        'https://x.ai/cli/grok-1.0.41-linux-x86_64',
        '9ce03ed23e16ea01072b4496263d6213a27899e1e3e107f008d36edf82e70407',
        'harnesses/grok-build/1.0.41/grok-1.0.41-linux-x86_64'
    ),
    Material(
        'Kimi Code CLI',
        '2.1.1',
        '2026-09-24T07:27:15.480Z',
        'https://registry.npmjs.org/@moonshot-ai/kimi-code/-/kimi-code-2.1.1.tgz',
        '6690a29d7b5e14812754dd100136b7f4ee2577add8895f00fe27025b4412049f',
        'harnesses/kimi-code/2.1.1/kimi-code-2.1.1.tgz'
    ),
    Material(
        'Kiro CLI',
        '2.24.1',
        '2026-09-25T02:20:13.596492Z',
        'https://desktop-release.q.us-east-1.amazonaws.com/2.24.1/kirocli-x86_64-linux.tar.gz',
        '9ddc94fe5d1c7530e14bf93b98e78b0bc74e3a36e14cfcf756373ba65ce41a2a',
        'harnesses/kiro-cli/2.24.1/kirocli-x86_64-linux.tar.gz'
    ),
    Material(
        'MiniMax Code',
        '0.5.5',
        '2026-09-25T16:49:11.277Z',
        'https://registry.npmjs.org/@minimax-ai/code/-/code-0.5.5.tgz',
        '1aee10f29045618d46fd37f341b4708f4ea3c0592cc35b8cb15f23671dbf0456',
        'harnesses/minimax-code/0.5.5/minimax-code-0.5.5.tgz'
    ),
    Material(
        'Mistral Vibe',
        '2.25.8',
        '2026-09-23T15:29:18Z',
        'https://github.com/mistralai/mistral-vibe/releases/download/v2.25.8/vibe-acp-linux-x86_64-2.25.8.tar.gz',
        'dbe693ee66f29fdfcf9029dcbf3b2d4da138554b193e432b089840148ecf08b3',
        'harnesses/mistral-vibe/2.25.8/vibe-acp-linux-x86_64-2.25.8.tar.gz'
    ),
    Material(
        'Muse Code',
        '1.4.0-R4161.1',
        'UNKNOWN',
        'https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=1.4.0-R4161.1&file=muse-x86-linux',
        '1b68bd4518d53a2aaff063915df4d141b0a205e6d79038299d04e3a14e85a5b9',
        'harnesses/muse-code/1.4.0-R4161.1/muse-x86-linux'
    ),
    Material(
        'Qwen Code',
        '0.24.5',
        '2026-09-24T17:16:56.822Z',
        'https://github.com/QwenLM/qwen-code/releases/download/v0.24.5/qwen-code-linux-x64.tar.gz',
        '61490a0fe061d9fd539f238ef2b6a133ea203eeb1682f65b21b6af2c1c428869',
        'harnesses/qwen-code/0.24.5/qwen-code-linux-x64.tar.gz'
    ),
    Material(
        'TRAE CLI',
        '0.120.52',
        'UNKNOWN',
        'https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_0.120.52_linux_amd64.tar.gz',
        'b92505d7ebfe1956387f4c9368bc66cfbdeb3b5e81ce773e2b0cbca2fb3e4c39',
        'harnesses/trae-cli/0.120.52/trae-cli_0.120.52_linux_amd64.tar.gz'
    ),
)

BLOCKED: Final = (
    Blocked(
        'DeepSeek Harness',
        'https://github.com/deepseek-ai/deepseek-harness',
        'no stable release was confirmed; the explicitly selected npm 0.1.7-rc.2 fallback is verified above and remains marked RC'
    ),
    Blocked(
        'ZCode CLI',
        'https://github.com/zai-org/ZCode',
        'the vendor publishes no CLI distribution: the CDN carries Electron desktop assets and '
        'ZCODE_DIST_BASE_URL has no public default, so this script builds the command line version '
        'from tag v3.14.3 into harnesses/zcode/3.14.3/'
    ),
)

GITHUB_REPOSITORY: Final = 'https://github.com/zai-org/ZCode'
SOURCE_URL_TEMPLATE: Final = 'https://codeload.github.com/zai-org/ZCode/tar.gz/refs/tags/{tag}'
NODE_URL_TEMPLATE: Final = 'https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.gz'
DEFAULT_TAG: Final = 'v3.14.3'
DEFAULT_NODE_VERSION: Final = '24.14.0'
DEFAULT_WORK_DIR: Final = Path(tempfile.gettempdir())
SCRATCH_NAME: Final = 'zcode-build'
INSTALL_SCRIPT_SOURCE: Final = Path(__file__).resolve().parent / 'assets' / 'install-harness.sh'
INSTALL_SCRIPT_NAME: Final = 'install.sh'
# mise.toml pins the toolchain; corepack activates exactly this pnpm.
PNPM_VERSION: Final = '10.33.2'
DIST_BASE_URL_PLACEHOLDER: Final = 'https://example.invalid/zcode/'
PACKAGE_NAME: Final = 'zcode'
# A build fetches tens of megabytes from codeload, which drops connections; the budget is larger than the
# pinned tables use, and each retry restarts the transfer.
DOWNLOAD_RETRIES: Final = 10
DOWNLOAD_RETRY_DELAY: Final = 5
# The build inherits the caller's PATH; the activated toolchain directory goes in front of it.
INHERITED_PATH: Final = os.environ.get('PATH', '')
# The command line distribution never touches Electron, a desktop-only dependency,
# so its binary download is skipped. The rest keeps pnpm from asking questions in
# a non-interactive build.
BUILD_ENVIRONMENT: Final = {
    'COREPACK_ENABLE_PROJECT_SPEC': '0',
    'HUSKY': '0',
    'ELECTRON_SKIP_BINARY_DOWNLOAD': '1'
}


class ZcodeBuildError(RuntimeError):
    """A build step failed."""


def write_install_script(root: Path) -> Path:
    """
    Write the shared harness install script into the material root.

    The script is repository content carried at assets/install-harness.sh; the
    deploy step looks for it at harnesses/install.sh, so every download pass
    refreshes that copy. An up-to-date copy is left untouched.

    Args:
        `root` (Path): Material root the harness tree lives under.

    Returns:
        `destination` (Path): The script path inside the material root.

    Raises:
        OSError: The source or destination file cannot be read or written.
    """
    content = INSTALL_SCRIPT_SOURCE.read_bytes()
    destination = root.resolve() / 'harnesses' / INSTALL_SCRIPT_NAME
    if destination.is_file() and destination.read_bytes() == content:
        print(f'unchanged: {destination}', flush=True)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    print(f'wrote: {destination}', flush=True)
    return destination


@dataclass(frozen=True)
class ZcodeBuildConfig:
    """
    Parameters of one ZCode CLI build.

    Attributes:
        out_dir (Path): Harness version directory for the tarball and manifest.json.
        work_dir (Path): Dedicated scratch directory for source, toolchain, and build output; created and removed by the build.
        tag (str): Repository tag to build.
        node_version (str): Node version from the repository's mise.toml.
    """

    out_dir: Path
    work_dir: Path
    tag: str = DEFAULT_TAG
    node_version: str = DEFAULT_NODE_VERSION


def _download(url: str, destination: Path) -> Path:
    """
    Download a URL to a path, retrying whole transfers on a flaky link.

    codeload redirects to a signed URL that rejects byte ranges, so a dropped
    download restarts rather than resumes; the retry budget and the rename at
    the end are what keep a 40 MB fetch tolerable.

    Args:
        `url` (str): Source URL to fetch.
        `destination` (Path): File to write; an existing file is returned untouched.

    Returns:
        `destination` (Path): The downloaded file path.

    Raises:
        MaterialError: A partial file from an earlier attempt is still there.
        OSError: The destination directory cannot be created.
        subprocess.CalledProcessError: curl exits unsuccessfully.
    """
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f'== download {url}', flush=True)
    fetch(url, destination, retries=DOWNLOAD_RETRIES, retry_delay=DOWNLOAD_RETRY_DELAY)
    print(f'   {destination.name}: sha256={sha256(destination)}', flush=True)
    return destination


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    """
    Run one build command.

    Args:
        `command` (list[str]): Command and arguments to execute.
        `cwd` (Path): Working directory for the command.
        `env` (dict[str, str]): Environment for the command.

    Raises:
        OSError: The command cannot be started.
        subprocess.CalledProcessError: The command exits unsuccessfully.
    """
    print(f'== {" ".join(command)}', flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _toolchain(config: ZcodeBuildConfig) -> Path:
    """
    Install the pinned Node under the scratch directory and return its bin directory.

    Args:
        `config` (ZcodeBuildConfig): Build parameters carrying the work directory and Node version.

    Returns:
        `bin_dir` (Path): Directory holding the installed node and npm executables.

    Raises:
        ZcodeBuildError: The archive did not unpack to the expected directory.
        OSError: The toolchain files cannot be created or inspected.
        subprocess.CalledProcessError: tar exits unsuccessfully.
    """
    root = config.work_dir / 'toolchain'
    bin_dir = root / f'node-v{config.node_version}-linux-x64' / 'bin'
    if (bin_dir / 'node').is_file():
        return bin_dir
    root.mkdir(parents=True, exist_ok=True)
    archive = _download(NODE_URL_TEMPLATE.format(version=config.node_version), root / f'node-v{config.node_version}.tar.gz')
    subprocess.run(['tar', '-xzf', str(archive), '-C', str(root)], check=True)
    if not (bin_dir / 'node').is_file():
        raise ZcodeBuildError(f'node {config.node_version} did not unpack to {bin_dir}')
    return bin_dir


def _prepare_source(config: ZcodeBuildConfig) -> Path:
    """
    Download and unpack the source tag, returning the repository root.

    Args:
        `config` (ZcodeBuildConfig): Build parameters carrying the work directory and tag.

    Returns:
        `source` (Path): Unpacked repository root for the tag.

    Raises:
        ZcodeBuildError: The archive did not unpack to the expected directory.
        OSError: The source files cannot be created or inspected.
        subprocess.CalledProcessError: tar exits unsuccessfully.
    """
    root = config.work_dir / 'source'
    source = root / f'ZCode-{config.tag.lstrip("v")}'
    if (source / 'package.json').is_file():
        return source
    archive = _download(SOURCE_URL_TEMPLATE.format(tag=config.tag), root / f'ZCode-{config.tag}.tar.gz')
    if source.exists():
        shutil.rmtree(source)
    subprocess.run(['tar', '-xzf', str(archive), '-C', str(root)], check=True)
    if not (source / 'package.json').is_file():
        raise ZcodeBuildError(f'source tag {config.tag} did not unpack to {source}')
    return source


def build_zcode(config: ZcodeBuildConfig) -> dict[str, object]:
    """
    Build the distribution tarball into config.out_dir and record its sha256.

    Args:
        `config` (ZcodeBuildConfig): Build parameters carrying the output directory, work directory, and tag.

    Returns:
        `result` (dict[str, object]): Version, manifest, and the source revision the tarball came from.

    Raises:
        ZcodeBuildError: A step failed or produced no tarball.
        subprocess.CalledProcessError: A build command exited non-zero.
    """
    bin_dir = _toolchain(config)
    environment = {
        **os.environ,
        **BUILD_ENVIRONMENT, 'PATH': f'{bin_dir}:{INHERITED_PATH}'
    }
    source = _prepare_source(config)
    _run(['corepack', 'enable', '--install-directory', str(bin_dir)], cwd=source, env=environment)
    _run(['corepack', 'prepare', f'pnpm@{PNPM_VERSION}', '--activate'], cwd=source, env=environment)
    _run(['pnpm', 'install', '--frozen-lockfile'], cwd=source, env=environment)
    # tsc -b emits packages/*/dist, which the SEA asset collector reads. @zcode/shared
    # has no build script of its own, so nothing else produces its dist.
    _run(['pnpm', 'typecheck'], cwd=source, env=environment)
    _run(['pnpm', '-r', '--filter', './packages/*', '--filter', '!@zcode/desktop', 'build'], cwd=source, env=environment)
    # build-zcode.mjs packages the CLI, server, and web entries; --base-url only lands
    # in the install.sh we do not use.
    _run(['node', 'scripts/build-zcode.mjs', '--base-url', DIST_BASE_URL_PLACEHOLDER], cwd=source, env=environment)
    version = json.loads((source / 'package.json').read_text())['version']
    tarball = source / 'dist' / PACKAGE_NAME / 'releases' / version / f'{PACKAGE_NAME}-{version}.tar.gz'
    if not tarball.is_file():
        raise ZcodeBuildError(f'build produced no tarball at {tarball}')
    config.out_dir.mkdir(parents=True, exist_ok=True)
    destination = config.out_dir / tarball.name
    shutil.copyfile(tarball, destination)
    manifest = [{
        'file': destination.name,
        'sha256': sha256(destination)
    }]
    (config.out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'== {destination} ({destination.stat().st_size} bytes) sha256={manifest[0]["sha256"]}', flush=True)
    shutil.rmtree(config.work_dir)
    return {
        'version': version,
        'manifest': manifest,
        'repository': GITHUB_REPOSITORY,
        'tag': config.tag
    }


def ensure_zcode(root: Path, work_dir: Path, tag: str, node_version: str) -> None:
    """
    Build the ZCode distribution when it is absent, otherwise verify it in place.

    A build writes manifest.json next to the tarball, so a later run over a
    complete root verifies the tarball against that record rather than rebuilding.
    The build itself needs about 6 GB and the network; it works only inside the
    SCRATCH_NAME subdirectory under work_dir and removes just that subdirectory on success.

    Args:
        `root` (Path): Material root the distribution is written under.
        `work_dir` (Path): Directory the build scratch subdirectory is created under.
        `tag` (str): Repository tag of the distribution.
        `node_version` (str): Node version from the repository's mise.toml.

    Raises:
        ZcodeBuildError: The manifest is invalid, or the tarball does not match its recorded digest.
        OSError: The manifest or distribution file cannot be read.
        ValueError: The manifest is not valid JSON.
    """
    out_dir = root / 'harnesses' / PACKAGE_NAME / tag.lstrip('v')
    manifest_path = out_dir / 'manifest.json'
    if not manifest_path.is_file():
        scratch = work_dir / SCRATCH_NAME
        result = build_zcode(ZcodeBuildConfig(out_dir=out_dir, work_dir=scratch, tag=tag, node_version=node_version))
        print(f'built: zcode {result["version"]} from {result["tag"]}', flush=True)
        return
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, list):
        raise ZcodeBuildError(f'{manifest_path} must hold a list of file records')
    for item in manifest:
        if not isinstance(item, dict) or not isinstance(item.get('file'), str) or not isinstance(item.get('sha256'), str):
            raise ZcodeBuildError(f'{manifest_path} entries must be objects with file and sha256 strings: {item!r}')
        destination = out_dir / item['file']
        actual = sha256(destination) if destination.is_file() else None
        if actual != item['sha256']:
            raise ZcodeBuildError(f'{destination} does not match {manifest_path}: expected {item["sha256"]}, got {actual}')
        print(f'verified: zcode {destination.name} sha256={actual}', flush=True)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Worker configuration file')
    parser.add_argument(
        '--work-dir',
        type=Path,
        default=DEFAULT_WORK_DIR,
        help='directory the ZCode build scratch is created under, about 6 GB on local storage; used only when that '
        'distribution is absent, defaults to the system temp directory'
    )
    parser.add_argument('--tag', default=DEFAULT_TAG, help='ZCode repository tag to build')
    parser.add_argument('--node-version', default=DEFAULT_NODE_VERSION, help='Node version from the ZCode repository mise.toml')
    args = parser.parse_args()
    root = read_materials_directory(args.config)
    summary = download_material(MATERIALS, BLOCKED, root)
    write_install_script(root)
    ensure_zcode(root, args.work_dir.resolve(), args.tag, args.node_version)
    print(f'done: {summary["downloaded"]} downloaded, {summary["existing"]} already present', flush=True)


if __name__ == '__main__':
    main()
