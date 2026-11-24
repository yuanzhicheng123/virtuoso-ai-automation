"""Sustained localhost call measurements, with explicit cold and queue costs."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('VF_LIVE_SMOOTHNESS') != '1', reason='explicit call smoothness benchmark required')


def summary(samples):
    values = sorted(samples)
    return {'n': len(values), 'median_ms': statistics.median(values),
            'p95_ms': values[math.ceil(.95*len(values))-1],
            'p99_ms': values[math.ceil(.99*len(values))-1], 'max_ms': values[-1],
            'samples_ms': samples}


def rss_kib(pid):
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    raise RuntimeError('RSS unavailable')


def repeated(operation, count):
    samples = []
    for _ in range(count):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns()-start)/1e6)
    return summary(samples)


def python_plus(result):
    assert result.ok and result.output.strip() == '3', result


# Bind the owned session explicitly; this measures shipped MCP framing/dispatch,
# not profile discovery or SSH tunnel startup. No alternate handlers are installed.
MCP_BOOT = '''import os
from pathlib import Path
from virtuoso_fusion import FusionClient, mcp
mcp.server._CLIENT['obj'] = FusionClient(
    port=int(os.environ['VF_LIVE_PORT']),
    daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip(),
    timeout=10, log_to_ciw=False)
mcp.main()
'''


class McpPipe:
    def __init__(self, process):
        self.process = process
        self.sequence = 0
        self.responses = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.responses.put(json.loads(line))
        self.responses.put(None)

    def send(self, method, params=None):
        self.sequence += 1
        request = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method}
        if params is not None:
            request['params'] = params
        self.process.stdin.write(json.dumps(request)+'\n')
        self.process.stdin.flush()
        return self.sequence

    def receive(self, expected):
        reply = self.responses.get(timeout=15)
        assert reply is not None and reply['id'] == expected and 'error' not in reply, reply
        return reply['result']

    def request(self, method, params=None):
        return self.receive(self.send(method, params))

    def plus(self, registered=False):
        args = {'name': 'plus', 'args': [1, 2]} if registered else {'expr': 'plus(1 2)', 'timeout': 10}
        result = self.request('tools/call', {'name': 'call_registered' if registered else 'evaluate',
                                           'arguments': args})
        assert not result.get('isError'), result
        assert result['structuredContent']['output'].strip() == '3', result


@contextmanager
def mcp_pipe(root, name):
    with (root/(name+'.stderr')).open('w') as stderr:
        process = subprocess.Popen([sys.executable, '-I', '-c', MCP_BOOT], cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, text=True, bufsize=1)
        try:
            yield McpPipe(process)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()


def test_sustained_python_and_mcp_calls():
    from virtuoso_fusion import FusionClient, __version__
    root = Path(os.environ['VF_LIVE_ROOT'])
    report = {
        'scope': 'authenticated localhost TCP on 220; isolated owned GUI; arithmetic only',
        'version': __version__, 'python': sys.version,
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'startup_measurement': 'includes launch of main and independent control MCP workers',
        'limitations': ['not WAN/SSH latency', 'not production SLO or large OA/solver throughput',
                        'MCP client bound to owned session; profile discovery not timed'],
        'screening_targets': {'steady_p95_ms': 50, 'steady_p99_ms': 100,
                              'failures': 0, 'python_rss_growth_mib': 10},
        'measurements': {},
    }
    output = root/'call-smoothness.json'
    output.write_text(json.dumps(report, indent=2))
    options = dict(port=int(os.environ['VF_LIVE_PORT']), timeout=10, log_to_ciw=False,
                   daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip())
    measurements = report['measurements']
    with FusionClient(**options) as client:
        assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
        measurements['first_raw'] = repeated(lambda: python_plus(client.execute_skill('plus(1 2)')), 1)
        measurements['first_generic'] = repeated(lambda: python_plus(client.call('plus', 1, 2)), 1)
        before = rss_kib(os.getpid())
        measurements['python_raw_500'] = repeated(lambda: python_plus(client.execute_skill('plus(1 2)')), 500)
        measurements['python_generic_500'] = repeated(lambda: python_plus(client.call('plus', 1, 2)), 500)
        client.evidence_dir = root/'smoothness-evidence'
        measurements['python_evidence_500'] = repeated(lambda: python_plus(client.call('plus', 1, 2)), 500)
        after = rss_kib(os.getpid())
        report['python_rss'] = {'before_kib': before, 'after_kib': after,
                                'growth_mib': (after-before)/1024}
        client.evidence_dir = None
        def batch():
            result = client.execute_skill('let((total) total=0 for(i 1 20 total=total+plus(1 2)) total)')
            assert result.ok and result.output.strip() == '60', result
        measurements['python_batch20_100'] = repeated(batch, 100)
    def recreate():
        with FusionClient(**options) as client:
            python_plus(client.execute_skill('plus(1 2)'))
    measurements['python_recreate_raw_100'] = repeated(recreate, 100)
    with FusionClient(**{**options, 'log_to_ciw': True}) as logged:
        python_plus(logged.execute_skill('plus(1 2)'))
        measurements['python_ciw_logging_100'] = repeated(lambda: python_plus(logged.execute_skill('plus(1 2)')), 100)
    output.write_text(json.dumps(report, indent=2))
    start = time.perf_counter_ns()
    with mcp_pipe(root, 'mcp-main') as pipe, mcp_pipe(root, 'mcp-control') as control:
        pipe.request('ping')
        measurements['mcp_start_to_first_ping_ms'] = (time.perf_counter_ns()-start)/1e6
        control.request('ping')
        measurements['mcp_first_evaluate'] = repeated(pipe.plus, 1)
        report['mcp_rss'] = {'before_registry_kib': rss_kib(pipe.process.pid)}
        def search():
            result = pipe.request('tools/call', {'name': 'registry_search', 'arguments': {'query': 'plus', 'limit': 1}})
            assert not result.get('isError') and result['structuredContent']['matches']
        measurements['mcp_first_registry_search'] = repeated(search, 1)
        report['mcp_rss']['after_search_kib'] = rss_kib(pipe.process.pid)
        measurements['mcp_first_registered_after_search'] = repeated(lambda: pipe.plus(True), 1)
        report['mcp_rss']['after_registered_kib'] = rss_kib(pipe.process.pid)
        measurements['mcp_evaluate_500'] = repeated(pipe.plus, 500)
        measurements['mcp_registered_500'] = repeated(lambda: pipe.plus(True), 500)
        report['mcp_rss']['after_1000_calls_kib'] = rss_kib(pipe.process.pid)
        output.write_text(json.dumps(report, indent=2))
        start = time.perf_counter_ns()
        slow_id = pipe.send('tools/call', {'name': 'evaluate', 'arguments': {'expr': 'progn(sleep(1) 3)', 'timeout': 10}})
        ping_id = pipe.send('ping')
        measurements['independent_channel_ping'] = repeated(lambda: control.request('ping'), 20)
        slow = pipe.receive(slow_id)
        assert not slow.get('isError') and slow['structuredContent']['output'].strip() == '3', slow
        pipe.receive(ping_id)
        measurements['same_channel_ping_behind_1s_call_ms'] = (time.perf_counter_ns()-start)/1e6
        pipe.plus()
    steady = {k:v for k,v in measurements.items() if isinstance(v, dict) and v.get('n') == 500}
    report['completed_without_call_failures'] = True
    report['screening_passed'] = all(v['p95_ms'] <= 50 and v['p99_ms'] <= 100 for v in steady.values()) and report['python_rss']['growth_mib'] <= 10
    report['steady_calls'] = sum(v['n'] for v in steady.values())
    output.write_text(json.dumps(report, indent=2))
    assert report['screening_passed'], 'Call latency or RSS exceeded the declared screening targets; inspect call-smoothness.json'
