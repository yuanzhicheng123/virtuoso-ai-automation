#!/usr/bin/env python3
"""Fingerprint a completed isolated Maestro attempt, excluding authentication files."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempt', type=Path)
    args = parser.parse_args()
    root = args.attempt.resolve()
    result = json.loads((root / 'result.json').read_text())
    assert result['process_stopped'] and result['daemon_stopped'] and result['xvfb_stopped']
    for source in root.glob('vf_maestro_*/rc_native/maestro/results/maestro/Interactive.*.rdb'):
        conn = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
        tables = ['point', 'corner', 'error', 'testStatus', 'result', 'resultValue', 'specValue']
        evidence = {}
        for table in tables:
            columns = [entry[1] for entry in conn.execute(f'pragma table_info({table})')]
            evidence[table] = [dict(zip(columns, row)) for row in conn.execute(f'select * from {table}')]
        conn.close()
        evidence['source'] = str(source.relative_to(root))
        evidence['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        (root / (source.stem + '-rdb.json')).write_text(json.dumps(evidence, indent=2))
    records = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or relative.parts[0] == 'private' or path.name == 'artifact-manifest.json':
            continue
        records[str(relative)] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    (root / 'artifact-manifest.json').write_text(json.dumps({'artifacts': records}, indent=2))
    print(f'{root}: {len(records)} artifacts fingerprinted')


if __name__ == '__main__':
    main()
