"""Shared plumbing for the scripts under scripts/.

`material` runs one pinned download table and is shared by the two download scripts; `material_check` owns the
offline VM, bundle upload, and command probing shared by the two install checks.
"""

from .material import Artifact, ArtifactError, Blocked, download_material, fetch, run, sha256
from .material_check import (
    discover_specs,
    install_bundle,
    MaterialCheckError,
    offline_vm,
    run_commands,
    stamp,
    summarize,
    write_report,
)

__all__ = [
    'Artifact',
    'ArtifactError',
    'Blocked',
    'MaterialCheckError',
    'discover_specs',
    'download_material',
    'fetch',
    'install_bundle',
    'offline_vm',
    'run',
    'run_commands',
    'sha256',
    'stamp',
    'summarize',
    'write_report',
]
