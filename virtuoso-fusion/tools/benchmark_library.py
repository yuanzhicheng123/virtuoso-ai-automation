#!/usr/bin/env python3
"""Benchmark installed packages in isolated interpreters; never connect to EDA.

Example: python tools/benchmark_library.py --python /venv/bin/python \
    --output evidence/performance.json --release-dir /path/to/release
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--release-dir', type=Path)
    parser.add_argument('--repeat', type=int, default=11)
    parser.add_argument('--clients', type=int, default=5)
    parser.add_argument('--child')
    args = parser.parse_args()
    if args.child:
        with contextlib.redirect_stdout(io.StringIO()):
            result = child(json.loads(args.child))
        print(json.dumps(result))
        return
    if args.repeat < 3 or args.clients < 2 or args.output is None:
        parser.error('require --output, --repeat >= 3 and --clients >= 2')
    specs = [
        {'mode': 'import', 'module': name}
        for name in ('virtuoso_bridge', 'virtuoso_fusion', 'virtuoso_fusion.cli', 'virtuoso_fusion.mcp')
    ]
    for package in ('lite', 'fusion'):
        specs.extend({'mode': 'clients', 'package': package, 'count': count, 'shared_registry': False}
                     for count in (1, args.clients))
    specs.append({'mode': 'clients', 'package': 'fusion', 'count': args.clients, 'shared_registry': False, 'touch_registry': True})
    specs.append({'mode': 'clients', 'package': 'fusion', 'count': args.clients, 'shared_registry': True, 'touch_registry': True})
    specs.append({'mode': 'registry'})
    results = []
    script = str(Path(__file__).resolve())
    for spec in specs:
        samples = []
        for _ in range(args.repeat):
            start = time.perf_counter_ns()
            proc = subprocess.run([args.python, '-I', script, '--child', json.dumps(spec)],
                                  text=True, capture_output=True, check=True)
            elapsed = (time.perf_counter_ns() - start) / 1e6
            sample = json.loads(proc.stdout)
            sample['process_wall_ms'] = elapsed
            sample['stderr'] = proc.stderr
            samples.append(sample)
        numeric = {key for key, value in samples[0].items() if type(value) in (int, float)}
        results.append({'spec': spec, 'summary': {key: summarize([x[key] for x in samples])
                                                for key in sorted(numeric)}, 'samples': samples})
    inventory = subprocess.run([args.python, '-I', script, '--child', '{"mode":"inventory"}'],
                               text=True, capture_output=True, check=True)
    report = {
        'schema_version': 1, 'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'host': platform.node(), 'platform': platform.platform(), 'cpu': cpu_name(),
        'cpu_count': os.cpu_count(), 'benchmark_sha256': hashlib.sha256(Path(script).read_bytes()).hexdigest(),
        'conditions': {'repeats_per_case': args.repeat, 'independent_interpreters': True,
                       'isolation_flag': '-I', 'disk_page_cache': 'not dropped; warm filesystem cache expected',
                       'eda_connection': False, 'rss_units': 'KiB on Linux',
                       'p95_method': 'nearest rank ceil(0.95*N)',
                       'timings': 'perf_counter_ns; import excludes interpreter startup; process_wall includes it',
                       'memory': 'current RSS from /proc/self/status and lifetime peak getrusage; deltas are not allocation attribution'},
        'inventory': json.loads(inventory.stdout), 'results': results,
    }
    if args.release_dir:
        report['release_files'] = [{'name': p.name, 'bytes': p.stat().st_size,
                                    'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                   for p in sorted(args.release_dir.glob('*.whl'))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(str(args.output))


def child(spec):
    result = {'python': sys.version, 'executable': sys.executable}
    if spec['mode'] == 'import':
        start = time.perf_counter_ns()
        module = importlib.import_module(spec['module'])
        result.update(import_ms=(time.perf_counter_ns() - start) / 1e6,
                      module_path=str(module.__file__), imported_modules=len(sys.modules))
    elif spec['mode'] == 'clients':
        module = importlib.import_module('virtuoso_fusion' if spec['package'] == 'fusion' else 'virtuoso_bridge')
        cls = module.FusionClient if spec['package'] == 'fusion' else module.VirtuosoClient
        result['rss_before_kib'] = rss()
        start = time.perf_counter_ns()
        kwargs = {'daemon_token': 'offline-benchmark-no-connect', 'log_to_ciw': False}
        if spec['shared_registry']:
            kwargs['registry'] = module.ApiRegistry()
        clients = [cls(**kwargs) for _ in range(spec['count'])]
        if spec.get('touch_registry'):
            for client in clients:
                assert client.registry.get('strlen') is not None
        result['construct_ms'] = (time.perf_counter_ns() - start) / 1e6
        result['rss_after_kib'] = rss()
        result['rss_delta_kib'] = result['rss_after_kib'] - result['rss_before_kib']
        result['client_registry_accessed_during_measurement'] = spec.get('touch_registry', False)
        start = time.perf_counter_ns()
        for client in clients:
            client.close()
        result['close_ms'] = (time.perf_counter_ns() - start) / 1e6
    elif spec['mode'] == 'registry':
        from virtuoso_fusion import ApiRegistry, GenericSkill
        result['rss_before_kib'] = rss()
        start = time.perf_counter_ns()
        registry = ApiRegistry()
        assert len(registry.raw) > 0
        result['load_ms'] = (time.perf_counter_ns() - start) / 1e6
        result['rss_delta_kib'] = rss() - result['rss_before_kib']
        # No executor is invoked; this is local argument binding, not RPC performance.
        generic = GenericSkill(None, registry=registry, auto_signature=False)
        assert registry.get('dbOpenCellViewByType') is not None
        for label, operation, loops in (
            ('lookup', lambda: registry.get('dbOpenCellViewByType'), 10000),
            ('search_hit', lambda: registry.search('dbOpen', limit=20), 200),
            ('search_miss', lambda: registry.search('__benchmark_missing__', limit=20), 200),
            ('coverage', registry.coverage, 20),
            ('kwargs_bind', lambda: generic.order_args('strlen', (), {'string': 'benchmark'}), 10000),
        ):
            start = time.perf_counter_ns()
            for _ in range(loops):
                operation()
            result[label + '_us_per_call'] = (time.perf_counter_ns() - start) / loops / 1000
        result['registry_entries'] = len(registry.raw)
    elif spec['mode'] == 'inventory':
        result['distributions'] = {dist.metadata['Name']: {'version': dist.version,
                                  'requires': dist.requires or []}
                                  for dist in importlib.metadata.distributions()}
        result['packages'] = {}
        for name in ('virtuoso_bridge', 'virtuoso_fusion'):
            package = importlib.import_module(name)
            root = Path(package.__file__).parent
            files = [p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
            python_files = [p for p in files if p.suffix == '.py']
            result['packages'][name] = {
                'path': str(root), 'files': len(files), 'bytes_excluding_pyc': sum(p.stat().st_size for p in files),
                'python_files': len(python_files), 'python_lines': sum(len(p.read_text().splitlines()) for p in python_files),
                'python_bytes': sum(p.stat().st_size for p in python_files),
                'largest_files': [{'path': str(p.relative_to(root)), 'bytes': p.stat().st_size}
                                  for p in sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:10]],
            }
    else:
        raise ValueError(spec['mode'])
    result['rss_kib'] = rss()
    result['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return result


def rss():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    raise RuntimeError('VmRSS missing; this benchmark requires Linux /proc')


def summarize(values):
    ordered = sorted(values)
    return {'n': len(values), 'median': statistics.median(values),
            'p95': ordered[math.ceil(.95 * len(values)) - 1], 'min': ordered[0], 'max': ordered[-1]}


def cpu_name():
    for line in Path('/proc/cpuinfo').read_text().splitlines():
        if line.startswith('model name'):
            return line.split(':', 1)[1].strip()
    return platform.processor()


if __name__ == '__main__':
    main()
