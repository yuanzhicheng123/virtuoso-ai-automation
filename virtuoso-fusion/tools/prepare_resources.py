#!/usr/bin/env python3
"""Generate immutable resource snapshots, or verify them without source checkouts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

COMMIT = '672b451416b1ef3cc560db4196365075e94c8d4e'
SOURCE = 'https://github.com/Arcadia-1/virtuoso-bridge-lite'
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src/virtuoso_fusion'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--upstream', type=Path)
    parser.add_argument('--legacy', type=Path)
    args = parser.parse_args()
    manifest_path = PACKAGE / '_data/provenance.json'
    if args.check:
        manifest = json.loads(manifest_path.read_text())
        for relative, expected in manifest['files'].items():
            actual = hashlib.sha256((PACKAGE / relative).read_bytes()).hexdigest()
            if actual != expected:
                raise SystemExit(f'resource hash mismatch: {relative}')
        print(f"Verified {len(manifest['files'])} packaged resource hashes")
        return
    if args.upstream is None or args.legacy is None:
        parser.error('generation requires --upstream and --legacy source directories')
    commit = subprocess.check_output(['git', '-C', str(args.upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != COMMIT:
        parser.error(f'expected upstream {COMMIT}, got {commit}')
    files = {}
    sources = [(args.upstream / 'mcp_server.py', '_upstream/mcp_server.py'),
               (args.upstream / 'LICENSE', '_upstream/LICENSE')]
    sources.extend((path, '_upstream/' + str(path.relative_to(args.upstream)))
                   for path in sorted((args.upstream / 'skills').rglob('*'))
                   if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'))
    sources.extend((args.legacy / 'virtuoso_bridge/_data' / name, '_data/' + name)
                   for name in ('api_registry_v2.json.gz', 'engineering_operations.json'))
    for source, relative in sources:
        destination = PACKAGE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        files[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps({
        'schema_version': 1,
        'upstream_source': SOURCE,
        'upstream_commit': COMMIT,
        'legacy_source': 'engineering-source/virtuoso_bridge/_data (local 0.8.1 repair)',
        'files': files,
    }, indent=2, sort_keys=True) + '\n')
    print(f'Generated {len(files)} resource snapshots')


if __name__ == '__main__':
    main()
