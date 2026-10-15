"""Shared plumbing for Worker deployment commands.

`material` runs one pinned download table and is shared by the download scripts under src/setup/download/;
`material_check` owns the offline VM, bundle upload, and command probing shared by the two install checks.
"""

from .material import Blocked, download_material, fetch, Material, MaterialError, run, sha256
from .material_check import (
    discover_base_image,
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
    'Blocked',
    'Material',
    'MaterialCheckError',
    'MaterialError',
    'discover_base_image',
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
