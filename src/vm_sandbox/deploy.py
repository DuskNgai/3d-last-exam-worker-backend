"""Deploy local software archives into a running VMware Guest."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import uuid


def _hash_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _validate_archive(path: Path) -> None:
    with tarfile.open(path) as archive:
        for member in archive:
            name = Path(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsupported archive entry: {member.name}')


def main() -> None:
    """Install pinned packages and retrieve verification artifacts from a Guest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    config_path = args.config.resolve(strict=True)
    config = json.loads(config_path.read_text())

    def local_path(value: str) -> Path:
        return (config_path.parent / value).resolve()

    vmx = local_path(config['vmx'])
    credentials_path = local_path(config['credentials'])
    if not vmx.is_file():
        parser.error('VMX file does not exist')
    if credentials_path.stat().st_mode & 0o077:
        parser.error('Credential file must be private, mode 600')
    credentials = json.loads(credentials_path.read_text())
    packages = config['packages']
    if set(packages) != {'codex', 'python', 'mujoco'}:
        parser.error('Expected codex, python and mujoco packages')
    for name, item in packages.items():
        path = local_path(item['path'])
        if _hash_file(path) != item['sha256']:
            parser.error(f'Package hash mismatch: {name}')
        _validate_archive(path)
    if args.check_only:
        print('Configuration and all three package hashes validated.')
        return

    run_id = uuid.uuid4().hex[:12]
    remote = f'/tmp/benchmark-deploy-{run_id}'
    output = local_path(config['reports']) / run_id
    output.mkdir(parents=True)
    scripts = Path(__file__).resolve().parent / 'scripts'
    timeout = config.get('timeout_seconds', 600)
    prefix = ['vmrun', '-T', 'ws', '-gu', credentials['username'], '-gp', credentials['password']]

    def guest(operation: str, *values: str, required: bool = True) -> bool:
        try:
            result = subprocess.run(prefix + [operation, str(vmx), *values], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Guest request timed out; installation may still be running. Inspect the VM before retrying.') from None
        if result.returncode and required:
            detail = (result.stderr + result.stdout).replace(credentials['password'], '<redacted>')
            raise RuntimeError(f'{operation} failed: {detail.strip()}')
        return result.returncode == 0

    status = 'failed'
    error = None
    guest('runProgramInGuest', '/bin/mkdir', '-m', '700', remote)
    try:
        for name, item in packages.items():
            print(f'Uploading {name} package', flush=True)
            guest('copyFileFromHostToGuest', str(local_path(item['path'])), f'{remote}/{name}.tar')
        for path in scripts.iterdir():
            if path.suffix in {'.sh', '.py'}:
                guest('copyFileFromHostToGuest', str(path), f'{remote}/{path.name}')
        install = '\n'.join([
            '#!/bin/bash', 'set -euo pipefail', f'cd {shlex.quote(remote)}',
            'tar -xf python.tar', 'tar -xf mujoco.tar',
            'rm -f /work/software-check.json /work/render-check.json /work/render-check.png /work/viewer-check.json',
            'bash install_codex.sh "$PWD/codex.tar"',
            'bash install_python.sh "$PWD/python-deployment"',
            'bash install_mujoco.sh "$PWD/mujoco-deployment"',
            'cp check_viewer.py /opt/benchmark/checks/',
        ]) + '\n'
        if config.get('check_viewer', False):
            install += ('cd /work\nrunuser -u agent -- timeout 30 env DISPLAY=:0 XAUTHORITY=/run/user/1001/gdm/Xauthority '
                        'LIBGL_ALWAYS_SOFTWARE=1 /task/software/python/bin/python /opt/benchmark/checks/check_viewer.py\n')
        bootstrap = ('#!/bin/bash\nset -euo pipefail\n'
                     f'exec > {shlex.quote(remote + "/install.log")} 2>&1\n'
                     f'printf "%s\\n" {shlex.quote(credentials["password"])} | sudo -S /bin/bash '
                     f'{shlex.quote(remote + "/install-all.sh")}\n')
        with tempfile.TemporaryDirectory(prefix='vm-deploy-') as temporary:
            for name, content in [('install-all.sh', install), ('bootstrap.sh', bootstrap)]:
                path = Path(temporary) / name
                path.write_text(content)
                path.chmod(0o600)
                guest('copyFileFromHostToGuest', str(path), f'{remote}/{name}')
        print('Installing and validating inside Guest', flush=True)
        guest('runProgramInGuest', '/bin/bash', f'{remote}/bootstrap.sh')
        for name in ['software-check.json', 'render-check.json', 'render-check.png']:
            guest('copyFileFromGuestToHost', f'/work/{name}', str(output / name))
        if config.get('check_viewer', False):
            guest('copyFileFromGuestToHost', '/work/viewer-check.json', str(output / 'viewer-check.json'))
        status = 'passed'
    except (RuntimeError, OSError) as exc:
        error = str(exc).replace(credentials['password'], '<redacted>')
    finally:
        for operation, values in [
            ('copyFileFromGuestToHost', (f'{remote}/install.log', str(output / 'install.log'))),
            ('runProgramInGuest', ('/bin/rm', '-f', f'{remote}/bootstrap.sh')),
        ]:
            try:
                if not guest(operation, *values, required=False):
                    print(f'Cleanup or log retrieval failed: {operation}; inspect {remote}', flush=True)
            except RuntimeError:
                print(f'Cleanup timed out: {operation}; inspect {remote}', flush=True)
        report = {'status': status, 'vmx': str(vmx), 'packages': packages, 'guest_staging': remote,
                  'model_called': False, 'error': error}
        (output / 'deployment.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Deployment {status}; reports: {output}')
    if error:
        parser.exit(1, error + '\n')


if __name__ == '__main__':
    main()
