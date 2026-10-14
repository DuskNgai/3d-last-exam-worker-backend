"""
Generate the worker's TLS self-signed certificate and private key.

    python scripts/setup/identity.py --config src/configuration.yaml

The device ID and the two output paths come from the setup group of the
configuration file; the certificate's SHA256 fingerprint is printed for
registration with the Master.
"""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess

import yaml

CERTIFICATE_DAYS = 3650
KEY_BITS = 2048


class ConfigError(RuntimeError):
    """The configuration file lacks a setup string the generation needs."""


class IdentityError(RuntimeError):
    """A certificate or key generation step failed."""


def read_identity_settings(config_path: Path) -> tuple[str, Path, Path]:
    """
    Read the device ID and the certificate and key paths from the setup group.

    Args:
        `config_path` (Path): Configuration file holding setup.device_id, setup.tls_certificate_file, and setup.tls_key_file.

    Returns:
        `settings` (tuple[str, Path, Path]): Device ID, certificate path, and key path.

    Raises:
        ConfigError: Any of the three strings is missing or empty under setup.
        OSError: The configuration file cannot be read.
        yaml.YAMLError: The configuration file is not valid YAML.
    """
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    setup = config.get('setup') if isinstance(config, dict) else None
    setup = setup if isinstance(setup, dict) else {}
    missing = [key for key in ('device_id', 'tls_certificate_file', 'tls_key_file') if not setup.get(key)]
    if missing:
        raise ConfigError(f'{config_path} must hold strings under "setup": {", ".join(missing)}')
    return setup['device_id'], Path(setup['tls_certificate_file']), Path(setup['tls_key_file'])


def generate_certificate(device_id: str, certificate_file: Path, key_file: Path) -> None:
    """
    Generate the self-signed certificate and private key with openssl.

    Args:
        `device_id` (str): Device ID used as the certificate CN.
        `certificate_file` (Path): Destination of the PEM certificate; must not exist.
        `key_file` (Path): Destination of the PEM private key, chmod 0600; must not exist.

    Raises:
        IdentityError: openssl is missing, or a destination already exists.
        OSError: A destination directory cannot be created.
        subprocess.CalledProcessError: openssl exits unsuccessfully.
    """
    if shutil.which('openssl') is None:
        raise IdentityError('openssl not found on PATH; install it first')
    for path in (certificate_file, key_file):
        if path.exists():
            raise IdentityError(f'refusing to overwrite existing file: {path}')
    certificate_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        'openssl',
        'req',
        '-x509',
        '-newkey',
        f'rsa:{KEY_BITS}',
        '-nodes',
        '-days',
        str(CERTIFICATE_DAYS),
        '-subj',
        f'/CN={device_id}',
        '-keyout',
        str(key_file),
        '-out',
        str(certificate_file)
    ],
                   check=True)
    key_file.chmod(0o600)


def certificate_fingerprint(certificate_file: Path) -> str:
    """
    Return the colon-separated uppercase SHA256 fingerprint of a certificate.

    Args:
        `certificate_file` (Path): PEM certificate to hash.

    Returns:
        `fingerprint` (str): SHA256 of the DER form, colon-separated uppercase hex.

    Raises:
        subprocess.CalledProcessError: openssl exits unsuccessfully.
    """
    der = subprocess.run(['openssl', 'x509', '-in', str(certificate_file), '-outform', 'DER'], check=True, capture_output=True).stdout
    digest = hashlib.sha256(der).hexdigest().upper()
    return ':'.join(digest[index : index + 2] for index in range(0, len(digest), 2))


def main() -> None:
    """
    CLI entry point.

    Raises:
        SystemExit: Argument parsing fails.
        ConfigError: The configuration lacks a needed setup string.
        IdentityError: openssl is missing, or a destination already exists.
        OSError: A configuration or output file cannot be read or written.
        yaml.YAMLError: The configuration file is not valid YAML.
        subprocess.CalledProcessError: openssl exits unsuccessfully.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='configuration file holding setup.device_id, setup.tls_certificate_file, and setup.tls_key_file'
    )
    args = parser.parse_args()
    device_id, certificate_file, key_file = read_identity_settings(args.config)
    certificate_file = certificate_file.resolve()
    key_file = key_file.resolve()
    generate_certificate(device_id, certificate_file, key_file)
    print(f'certificate: {certificate_file}', flush=True)
    print(f'key: {key_file}', flush=True)
    print(f'sha256 fingerprint: {certificate_fingerprint(certificate_file)}', flush=True)


if __name__ == '__main__':
    main()
