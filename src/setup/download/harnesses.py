"""
Download and verify the harness material, write the shared install script, and build the distribution that is not downloadable.

    python src/setup/download/harnesses.py --config src/configuration.yaml
    python src/setup/download/harnesses.py --config src/configuration.yaml --work-dir <work dir>

The table pins each vendor distribution under harnesses/<name>/<version>/. Files
already on disk are skipped and re-verified. ZCode is built from its pinned
official tag into harnesses/zcode/<version>/. The shared install.sh the deploy step injects into every harness
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
        '1.4.12',
        '2026-10-30T21:39:59Z',
        'https://github.com/google-antigravity/antigravity-cli/releases/download/1.4.12/agy_cli_linux_x64.tar.gz',
        '3368d1923fc869f21ec8fce83252a8c96c89283aac9393958ac91fbf7a3949e4',
        'harnesses/antigravity-cli/1.4.12/agy_cli_linux_x64.tar.gz'
    ),
    Material(
        'Claude Code',
        '2.1.316',
        '2026-10-31T00:00:00Z',
        'https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.316.tgz',
        '745e1b196eb475161a185a00f5e1144d7324ec6094e21149eae719fa42481000',
        'harnesses/claude-code/2.1.316/claude-code-linux-x64-2.1.316.tgz'
    ),
    Material(
        'CodeBuddy Code',
        '2.179.2',
        '2026-11-01T00:00:00Z',
        'https://registry.npmjs.org/@tencent-ai/codebuddy-code/-/codebuddy-code-2.179.2.tgz',
        '9841071712beeb9ec4518a672872a310aecc499c809d515ba0e3c2383cadd2ef',
        'harnesses/codebuddy-code/2.179.2/codebuddy-code-2.179.2.tgz'
    ),
    Material(
        'Codex CLI',
        '0.166.0',
        '2026-10-29T00:00:00Z',
        'https://github.com/openai/codex/releases/download/rust-v0.166.0/codex-package-x86_64-unknown-linux-musl.tar.gz',
        '1d44b28bc4ac9937d7d3532264baa68a507ce364956a0d4ebb856f331cdee254',
        'harnesses/codex/0.166.0/codex-package-x86_64-unknown-linux-musl.tar.gz'
    ),
    Material(
        'DeepSeek Harness',
        '0.2.1-rc.5',
        '2026-10-29T00:00:00Z',
        'https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-rc.5.tgz',
        '60987566f19e0ded1f7918c00a65c19c56ea25f5977ee0bfd5697c5c113ec4e1',
        'harnesses/deepseek-harness/0.2.1-rc.5/dsh-0.2.1-rc.5.tgz'
    ),
    Material(
        'Grok Build',
        '1.0.73',
        '2026-10-28T00:00:00Z',
        'https://x.ai/cli/grok-1.0.73-linux-x86_64',
        '7b2ca103d269bc7561eb0c8983a179a5e0145bc42b85a91be5aa97dc80912595',
        'harnesses/grok-build/1.0.73/grok-1.0.73-linux-x86_64'
    ),
    Material(
        'Kimi Code CLI',
        '2.4.1',
        '2026-10-30T00:00:00Z',
        'https://registry.npmjs.org/@moonshot-ai/kimi-code/-/kimi-code-2.4.1.tgz',
        'e7dac04fa7a155c506d86099584fe442f97a222e44da2c042749968dfcae8e09',
        'harnesses/kimi-code/2.4.1/kimi-code-2.4.1.tgz'
    ),
    Material(
        'Kiro CLI',
        '2.37.0',
        '2026-10-30T00:00:00Z',
        'https://desktop-release.q.us-east-1.amazonaws.com/2.37.0/kirocli-x86_64-linux.tar.gz',
        '02c49873ba64285f34048aac377ccf0b546b5627f39dd775bae9fa306112ff19',
        'harnesses/kiro-cli/2.37.0/kirocli-x86_64-linux.tar.gz'
    ),
    Material(
        'MiniMax Code',
        '0.7.4',
        '2026-10-30T00:00:00Z',
        'https://registry.npmjs.org/@minimax-ai/code/-/code-0.7.4.tgz',
        'c3c023d890f9168f2eac6b9195cefc37a237ab813e9e6bd1948092dc5fe7dcfc',
        'harnesses/minimax-code/0.7.4/minimax-code-0.7.4.tgz'
    ),
    Material(
        'Mistral Vibe',
        '3.0.4',
        '2026-10-30T00:00:00Z',
        'https://github.com/mistralai/mistral-vibe/releases/download/v3.0.4/vibe-acp-linux-x86_64-3.0.4.tar.gz',
        '83d9cd21758a99719382e1c8c55420e9693d7e3358b3d3a744727e2291a9d5b3',
        'harnesses/mistral-vibe/3.0.4/vibe-acp-linux-x86_64-3.0.4.tar.gz'
    ),
    Material(
        'Muse Code',
        '1.4.9-R6297.1',
        'UNKNOWN',
        'https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=1.4.9-R6297.1&file=muse-x86-linux',
        'a228010021276e112aa8422eb56ae82c0375405701950f6529bed4c9fe8abe05',
        'harnesses/muse-code/1.4.9-R6297.1/muse-x86-linux'
    ),
    Material(
        'Qwen Code',
        '0.26.5',
        '2026-11-01T00:00:00Z',
        'https://github.com/QwenLM/qwen-code/releases/download/v0.26.5/qwen-code-linux-x64.tar.gz',
        '7908e11fad56bc13fe89f425f51103bdee3d9993450b626397921e8dc97908e9',
        'harnesses/qwen-code/0.26.5/qwen-code-linux-x64.tar.gz'
    ),
    Material(
        'TRAE CLI',
        '0.209.1',
        '2026-10-10T06:56:03.726524Z',
        'https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/v2/releases/0.209.1/traex-linux-x86_64.gz',
        '643cf93b876e89ea5c72c87d3448b26690c6b71e45b3a7756b914d2ca53c1405',
        'harnesses/trae-cli/0.209.1/traex-linux-x86_64.gz'
    ),
)

BLOCKED: Final = (
    Blocked(
        'DeepSeek Harness',
        'https://github.com/deepseek-ai/deepseek-harness',
        'no stable release was confirmed; the explicitly selected npm 0.2.1-rc.5 fallback is verified above and remains marked RC'
    ),
    Blocked(
        'ZCode CLI',
        'https://github.com/zai-org/ZCode',
        'the vendor publishes no CLI distribution, so this script builds the command line version '
        'from the official v3.17.1 tag into harnesses/zcode/3.17.1/'
    ),
)

GITHUB_REPOSITORY: Final = 'https://github.com/zai-org/ZCode'
SOURCE_URL_TEMPLATE: Final = 'https://codeload.github.com/zai-org/ZCode/tar.gz/refs/tags/{tag}'
NODE_URL_TEMPLATE: Final = 'https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.gz'
DEFAULT_TAG: Final = 'v3.17.1'
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
    Download and unpack the requested official tag, returning the repository root.

    Args:
        `config` (ZcodeBuildConfig): Build parameters carrying the work directory and repository tag.

    Returns:
        `source` (Path): Unpacked repository root for the requested tag.

    Raises:
        ZcodeBuildError: The archive did not unpack to the expected directory.
        OSError: The source files cannot be created or inspected.
        subprocess.CalledProcessError: tar exits unsuccessfully.
    """
    root = config.work_dir / 'source'
    source = root / f'ZCode-{config.tag.lstrip("v")}'
    if (source / 'package.json').is_file():
        return source
    archive = _download(
        SOURCE_URL_TEMPLATE.format(tag=config.tag), root / f'ZCode-{config.tag}.tar.gz'
    )
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
        `result` (dict[str, object]): Version, manifest, and source repository.

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


def ensure_zcode(
    root: Path,
    work_dir: Path,
    tag: str,
    node_version: str,
) -> None:
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
        result = build_zcode(ZcodeBuildConfig(
            out_dir=out_dir,
            work_dir=scratch,
            tag=tag,
            node_version=node_version,
        ))
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
        '--only',
        choices=tuple(Path(material.path).parts[1] for material in MATERIALS),
        help='download only the named harness package'
    )
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
    selected_materials = tuple(
        material for material in MATERIALS
        if args.only is None or Path(material.path).parts[1] == args.only
    )
    summary = download_material(selected_materials, BLOCKED if args.only is None else (), root)
    write_install_script(root)
    if args.only is None:
        ensure_zcode(root, args.work_dir.resolve(), args.tag, args.node_version)
    print(f'done: {summary["downloaded"]} downloaded, {summary["existing"]} already present', flush=True)


if __name__ == '__main__':
    main()
