import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from virtuoso_bridge import ExecutionStatus, VirtuosoClient, VirtuosoResult


def test_public_client_is_a_real_upstream_client():
    from virtuoso_fusion import FusionClient

    with FusionClient() as client:
        assert isinstance(client, VirtuosoClient)
        for domain in ('schematic', 'layout', 'symbol', 'library', 'maestro', 'sos', 'dialogs', 'requests'):
            assert getattr(client, domain) is not None
        assert len(client.registry.raw) == 74785


def test_domain_and_generic_calls_share_execution_and_keep_metadata(monkeypatch, tmp_path):
    from virtuoso_fusion import FusionClient

    calls = []
    expected = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['busy'], warnings=['detail'],
                              metadata={'outcome': 'unknown', 'recovery_handle': 'handle-1'})
    def execute(self, code, timeout=None, *, retry_connect=True):
        calls.append((self, code, timeout, retry_connect))
        return expected
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', execute)
    with FusionClient(evidence_dir=tmp_path) as client:
        result = client.call('plus', 1, 2, 3, timeout=2)
        assert result is expected
        assert calls == [(client, 'plus(1 2 3)', 2, False)]
        client.execute_skill('getVersion()', retry_connect=True)
        assert calls[-1][-1] is False
        assert result.metadata['recovery_handle'] == 'handle-1'
    events = [json.loads(line) for p in tmp_path.glob('*.jsonl') for line in p.read_text().splitlines()]
    assert len(events) == 2
    assert events[0]['result']['metadata']['outcome'] == 'unknown'
    assert events[0]['result']['status'] == 'error'


def test_reserved_argument_aliases_pass_through_both_call_layers(monkeypatch):
    from virtuoso_fusion import FusionClient, Symbol

    calls = []
    result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='t')

    def execute(self, code, timeout=None, *, retry_connect=True):
        calls.append((code, timeout, retry_connect))
        return result

    monkeypatch.setattr(VirtuosoClient, 'execute_skill', execute)
    with FusionClient() as client:
        assert client.call('geixGetNumRows', g_self=None) is result
        assert client.call('_ciMemoizeCmd', g_fn=Symbol('plus')) is result
        assert client.call('mpsDPut', pt=None, g_timeout=3, timeout=5) is result
        assert client.call(fn='plus', op1=1, op2=2) is result
    assert calls == [
        ('geixGetNumRows(nil)', None, False),
        ("_ciMemoizeCmd('plus)", None, False),
        ('mpsDPut(nil 3)', 5, False),
        ('plus(1 2)', None, False),
    ]


def test_same_client_execute_is_serialized(monkeypatch):
    from virtuoso_fusion import FusionClient

    active = 0
    peak = 0
    lock = threading.Lock()
    def execute(self, code, timeout=None, *, retry_connect=True):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.002)
        with lock:
            active -= 1
        return VirtuosoResult(status=ExecutionStatus.SUCCESS, output=code)
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', execute)
    with FusionClient() as client:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(client.execute_skill, ['plus(1 2)'] * 12))
    assert all(r.ok for r in results)
    assert peak == 1


def test_close_is_idempotent_and_does_not_reopen(monkeypatch):
    from virtuoso_fusion import FusionClient

    closes = []
    monkeypatch.setattr(VirtuosoClient, 'close', lambda self: closes.append(self))
    client = FusionClient()
    client.close()
    client.close()
    assert closes == [client]
    with pytest.raises(RuntimeError, match='closed'):
        client.execute_skill('plus(1 2)')


def test_evidence_failure_preserves_the_executed_result(monkeypatch, tmp_path):
    from virtuoso_fusion import FusionClient

    result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='3')
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', lambda *a, **k: result)
    bad_dir = tmp_path / 'a-file'
    bad_dir.write_text('not a directory')
    with FusionClient(evidence_dir=bad_dir) as client:
        assert client.execute_skill('plus(1 2)') is result
    assert result.ok
    assert result.metadata['fusion_evidence_error']
    assert result.warnings


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan'), True])
def test_invalid_budget_never_reaches_upstream(monkeypatch, timeout):
    from virtuoso_fusion import FusionClient

    monkeypatch.setattr(VirtuosoClient, 'execute_skill', lambda *a, **k: pytest.fail('must not execute'))
    with FusionClient() as client:
        with pytest.raises(ValueError, match='timeout'):
            client.execute_skill('plus(1 2)', timeout=timeout)
