"""Read-only executable/deck discovery; presence is not license verification."""
import os
from pathlib import Path
import shutil


def discover_tools(*, executables=None, decks=()):
    executables = executables if executables is not None else {
        'virtuoso': 'virtuoso', 'oa2strm': 'oa2strm', 'spectre': 'spectre', 'calibre': 'calibre',
        'assura': 'assura', 'quantus': 'quantus', 'pegasus': 'pegasus', 'pvs': 'pvs',
        'emx': 'emx', 'genus': 'genus', 'innovus': 'innovus', 'xcelium': 'xrun',
    }
    return {
        'tools': {name: {'executable': os.fspath(exe), 'path': shutil.which(exe)}
                  for name, exe in executables.items()},
        'decks': [{'path': str(Path(deck).resolve()), 'exists': Path(deck).is_file()} for deck in decks],
        'license_env': {name: bool(os.environ.get(name)) for name in
                        ('LM_LICENSE_FILE', 'CDS_LIC_FILE', 'MGLS_LICENSE_FILE', 'SNPSLMD_LICENSE_FILE')},
        'license_checked_out': False,
    }
