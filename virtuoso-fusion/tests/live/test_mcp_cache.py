"""Verify MCP signature caching against a disposable authenticated CIW."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('VF_LIVE_MCP_CACHE') != '1' or not os.environ.get('VF_LIVE_PORT'),
    reason='requires VF_LIVE_MCP_CACHE=1 and an isolated authenticated live runner',
)


@pytest.fixture(scope='module')
def cache_session():
    from virtuoso_fusion import FusionClient, mcp

    root = Path(os.environ['VF_LIVE_ROOT'])
    token = Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip()
    base = mcp._registry_data()
    report = {
        'scope': 'real MCP handler-to-CIW signature caching; no stdio framing or OA/PDK writes',
        'negative_cache_verified': False, 'steps': [], 'executions': [],
        'registry_file_sha256_before': hashlib.sha256(base.path.read_bytes()).hexdigest(),
    }
    with FusionClient(port=int(os.environ['VF_LIVE_PORT']), timeout=15, log_to_ciw=False,
                      daemon_token=token, evidence_dir=root / 'mcp-cache-calls') as client:
        assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
        original_execute = client.execute_skill

        def counted_execute(code, *args, **kwargs):
            result = original_execute(code, *args, **kwargs)
            report['executions'].append({'skill': code, 'result': result.model_dump(mode='json')})
            return result

        previous = dict(mcp.server._CLIENT)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(client, 'execute_skill', counted_execute)
            mcp.server._CLIENT['obj'] = client
            try:
                yield client, mcp, report
            finally:
                report['status'] = 'PASS' if report['steps'] and all(
                    step['status'] == 'PASS' for step in report['steps']) else 'FAIL'
                report['registry_file_sha256_after'] = hashlib.sha256(base.path.read_bytes()).hexdigest()
                mcp.server._CLIENT.clear()
                mcp.server._CLIENT.update(previous)
                text = json.dumps(report, indent=2, default=str).replace(token, '[REDACTED]')
                (root / 'mcp-cache-report.json').write_text(text + '\n')


@contextmanager
def evidence_step(report, name):
    record = {'name': name, 'status': 'FAIL', 'execution_start': len(report['executions'])}
    report['steps'].append(record)
    try:
        yield record
    except Exception as error:
        record['reason'] = f'{type(error).__name__}: {error}'
        raise
    else:
        record['status'] = 'PASS'
    finally:
        record['execution_end'] = len(report['executions'])


def test_repeated_alias_probes_once(cache_session):
    client, mcp, report = cache_session
    name = 'vfCacheAlias_' + uuid.uuid4().hex[:16]
    probe_code = f"arglist('{name})"
    base = mcp._registry_data()
    with evidence_step(report, 'create_alias_and_observe_actual_arglist') as step:
        step['name_in_ciw'] = name
        assert base.get(name) is None
        assert client.execute_skill(f"null(getd('{name}))").output.strip() == 't'
        created = client.execute_skill(f"putd('{name} getd('plus))")
        step['create_result'] = created.model_dump(mode='json')
        assert created.ok, created
        signature = client.execute_skill(probe_code)
        step['arglist_result'] = signature.model_dump(mode='json')
        assert signature.ok, signature

    with evidence_step(report, 'three_mcp_alias_calls') as step:
        step['requests_and_results'] = []
        first = len(report['executions'])
        for arguments in ([1, 2], [3, 4], [20, 22]):
            request = {'name': name, 'args': arguments, 'timeout': 10}
            result = mcp.server.dispatch_tool('call_registered', request, {})
            step['requests_and_results'].append({'request': request, 'result': result})
            assert not result.get('isError'), result
            assert int(result['structuredContent']['output'].strip()) == sum(arguments)
        probes = [item for item in report['executions'][first:] if item['skill'] == probe_code]
        step['probe_count'] = len(probes)
        step['probe_results'] = probes
        assert len(probes) == 1
        assert probes[0]['result']['output'].strip() == signature.output.strip()
        callers = client._fusion_mcp_callers[1]
        step['cache'] = {
            'probed': name in callers[False]._probed,
            'shared_probe_cache': callers[False]._probed is callers[True]._probed,
            'session_record': client.registry.get(name), 'offline_record': base.get(name),
            'uses_offline_base': client.registry._base is base,
        }
        assert step['cache']['probed'] and step['cache']['shared_probe_cache']
        assert step['cache']['uses_offline_base']
        assert base.get(name) is None
        report['negative_cache_verified'] = signature.output.strip() == 'nil'
        report['alias_cache_kind'] = 'negative_nil' if report['negative_cache_verified'] else 'positive_signature'
        if report['negative_cache_verified']:
            assert client.registry.get(name) is None
        else:
            assert client.registry.get(name)['arglist_raw'] == signature.output.strip()


def test_unknown_function_recovers_after_context_load(cache_session):
    client, mcp, report = cache_session
    name = 'vfCacheLoaded_' + uuid.uuid4().hex[:16]
    probe_code = f"arglist('{name})"
    base = mcp._registry_data()
    with evidence_step(report, 'unknown_function_failure') as step:
        step['name_in_ciw'] = name
        assert base.get(name) is None
        first = len(report['executions'])
        request = {'name': name, 'args': [1, 2], 'timeout': 10}
        failed = mcp.server.dispatch_tool('call_registered', request, {})
        step.update(request=request, result=failed)
        assert failed.get('isError'), failed
        step['probe_count'] = sum(item['skill'] == probe_code for item in report['executions'][first:])
        assert step['probe_count'] == 1
        callers = client._fusion_mcp_callers[1]
        step['cached_after_failure'] = name in callers[False]._probed
        assert not step['cached_after_failure']
        assert client.registry.get(name) is None

    with evidence_step(report, 'load_procedure_in_owned_ciw') as step:
        loaded = client.execute_skill(f'procedure({name}(a b) plus(a b))')
        step['result'] = loaded.model_dump(mode='json')
        assert loaded.ok, loaded

    with evidence_step(report, 'kwargs_after_load_reprobe_and_cache') as step:
        first = len(report['executions'])
        step['requests_and_results'] = []
        for left, right in [(4, 5), (20, 22)]:
            request = {'name': name, 'kwargs': {'b': right, 'a': left}, 'timeout': 10}
            result = mcp.server.dispatch_tool('call_registered', request, {})
            step['requests_and_results'].append({'request': request, 'result': result})
            assert not result.get('isError'), result
            assert int(result['structuredContent']['output'].strip()) == left + right
        executions = report['executions'][first:]
        step['probe_count'] = sum(item['skill'] == probe_code for item in executions)
        assert step['probe_count'] == 1
        assert [item['skill'] for item in executions if item['skill'] != probe_code] == [
            f'{name}(4 5)', f'{name}(20 22)']
        step['cache'] = {
            'probed': name in client._fusion_mcp_callers[1][False]._probed,
            'session_record': client.registry.get(name), 'offline_record': base.get(name),
        }
        assert step['cache']['probed']
        assert step['cache']['session_record']['params_source'] == 'arglist-live'
        assert base.get(name) is None
        assert hashlib.sha256(base.path.read_bytes()).hexdigest() == report['registry_file_sha256_before']
        report['unknown_function_recovery_verified'] = True
