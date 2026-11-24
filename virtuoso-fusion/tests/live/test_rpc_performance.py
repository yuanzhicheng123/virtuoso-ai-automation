"""Measured loopback RPC cost; not a solver-speed or production-SLO claim."""
import json
import math
import os
from pathlib import Path
import statistics
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('VF_LIVE_BENCHMARK'), reason='explicit live RPC benchmark required')


def test_rpc_serial_batch_and_logging():
    from virtuoso_bridge import VirtuosoClient
    from virtuoso_fusion import FusionClient
    root = Path(os.environ['VF_LIVE_ROOT'])
    options = dict(port=int(os.environ['VF_LIVE_PORT']), timeout=30, log_to_ciw=False,
                   daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip())
    repeats, operations = 11, 20
    measured = {}
    for name, cls, logging in [('lite', VirtuosoClient, False), ('fusion', FusionClient, False), ('fusion_evidence', FusionClient, True)]:
        config = dict(options)
        if logging:
            config['evidence_dir'] = root/'benchmark-calls'
        with cls(**config) as client:
            assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
            assert client.execute_skill('plus(1 2)').ok
            if name == 'fusion':
                started = time.perf_counter_ns()
                first = client.call('plus', 1, 2)
                assert first.ok and first.output.strip() == '3'
                measured['first_generic_includes_registry_ms'] = (time.perf_counter_ns()-started)/1e6
            for batch in (False, True):
                samples=[]
                for _ in range(repeats):
                    started=time.perf_counter_ns()
                    if batch:
                        result=client.execute_skill(f'let((total) total=0 for(i 1 {operations} total=total+plus(1 2)) total)')
                        assert result.ok and result.output.strip()==str(3*operations)
                    else:
                        for _ in range(operations):
                            result=client.execute_skill('plus(1 2)')
                            assert result.ok and result.output.strip()=='3'
                    samples.append((time.perf_counter_ns()-started)/1e6)
                key=name+('_batch' if batch else '_serial')
                measured[key]={'samples_ms':samples,'median_ms':statistics.median(samples),
                               'p95_ms':sorted(samples)[math.ceil(.95*len(samples))-1],
                               'operations_per_sample':operations,'rpc_per_sample':1 if batch else operations}
    report={'scope':'installed clients, authenticated localhost TCP on 220; arithmetic operations only',
            'semantics':'Same number of plus evaluations; batching changes RPC count. No geometry or solver speed claim.',
            'repeats':repeats,'measurements':measured}
    (root/'rpc-performance.json').write_text(json.dumps(report,indent=2))
