"""Failures identify the phase without claiming an unexecuted target ran."""
import json

import pytest
from virtuoso_bridge import VirtuosoClient, VirtuosoResult, ExecutionStatus
from virtuoso_fusion import FusionClient, mcp


@pytest.fixture
def session(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'auditFn': {'arglist_raw': '(a)'}}))
    calls = []
    result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='1')
    def execute(self, code, **options):
        calls.append(code)
        return result
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', execute)
    with FusionClient(registry=registry) as client:
        monkeypatch.setitem(mcp.server._CLIENT, 'obj', client)
        yield client, calls


def dispatch(**arguments):
    return mcp.server.dispatch_tool('call_registered', arguments, {})


@pytest.mark.parametrize('arguments,stage,code', [
    ({'name': 'dbSave'}, 'policy', 'WRITE_NEEDS_EXPLICIT_ALLOW'),
    ({'name': 'dbDeleteObject', 'allow_write': True}, 'policy', 'WRITE_NOT_ALLOWLISTED'),
    ({'name': 'auditFn', 'kwargs': {'wrong': 1}}, 'argument_binding', 'ARGUMENT_BINDING_FAILED'),
    ({'name': 'auditFn', 'args': [{}]}, 'encoding', 'VALUE_ENCODING_FAILED'),
    ({'name': 'invalid)'}, 'validation', 'INVALID_FUNCTION_NAME'),
])
def test_pre_execution_failures_are_actionable(session, arguments, stage, code):
    _, calls = session
    response = dispatch(**arguments)
    detail = response['structuredContent']['fusion_error']
    assert response['isError'] and response['structuredContent']['outcome'] == 'not_started'
    assert detail['stage'] == stage and detail['code'] == code
    assert detail['tool'] == 'call_registered'
    assert detail['target_execution'] == 'not_started'
    assert detail['next_action'] and detail['message']
    assert code in '\n'.join(item['text'] for item in response['content'])
    assert calls == []


def test_mcp_skill_keywords_do_not_collide_with_python_controls(session):
    client, calls = session
    client.registry.raw['auditFn'] = {'arglist_raw': '(self fn timeout)'}
    response = dispatch(name='auditFn', kwargs={'self': 1, 'fn': 2, 'timeout': 3}, timeout=5)
    assert not response.get('isError'), response
    assert calls == ['auditFn(1 2 3)']


def test_probe_failure_preserves_evidence_and_distinguishes_target(session, monkeypatch):
    client, calls = session
    failed = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['connection lost'],
                            metadata={'request_id': 'probe-receipt', 'outcome': 'unknown'})
    def execute(code, **options):
        calls.append(code)
        return failed
    monkeypatch.setattr(client, 'execute_skill', execute)
    response = dispatch(name='missingFn', kwargs={'a': 1})
    body = response['structuredContent']
    assert body['metadata'] == failed.model_dump(mode='json')['metadata']
    assert body['errors'] == ['connection lost']
    assert body['fusion_error']['stage'] == 'signature_probe'
    assert body['fusion_error']['target_execution'] == 'not_started'
    assert calls == ["arglist('missingFn)"]


@pytest.mark.parametrize('exception_type', [TypeError, PermissionError, ConnectionError])
def test_executor_exceptions_remain_unknown_and_are_not_replayed(session, monkeypatch, exception_type):
    client, calls = session
    error = exception_type('lost after dispatch')
    def execute(code, **options):
        calls.append(code)
        raise error
    monkeypatch.setattr(client, 'execute_skill', execute)
    response = dispatch(name='auditFn', args=[1])
    assert response['structuredContent']['outcome'] == 'unknown'
    detail = response['structuredContent']['fusion_error']
    assert detail['stage'] == 'execution' and detail['target_execution'] == 'unknown'
    assert 'Do not resubmit' in detail['next_action']
    assert calls == ['auditFn(1)']
    client.generic.executor = execute
    with pytest.raises(exception_type) as caught:
        client.generic.call('auditFn', 1)
    assert caught.value is error
    assert error.fusion_error['stage'] == 'execution'


def test_schema_failure_explains_expected_type_without_input_values(session):
    _, calls = session
    response = mcp.server.dispatch_tool('evaluate', {'expr': 'secret', 'timeout': 'private-value'}, {})
    detail = response['structuredContent']['fusion_error']
    assert detail['stage'] == 'validation' and detail['path'] == '$.timeout'
    assert detail['constraint'] == 'type' and detail['expected'] == 'integer'
    assert detail['actual_type'] == 'str'
    assert response['structuredContent']['outcome'] == 'not_started'
    assert 'private-value' not in json.dumps(response) and 'secret' not in json.dumps(response)
    assert calls == []


def test_refresh_tool_only_probes_and_updates_binding(session, monkeypatch):
    client, calls = session
    def execute(code, **options):
        calls.append(code)
        return VirtuosoResult(status=ExecutionStatus.SUCCESS,
                              output='(b)' if code.startswith('arglist(') else '2')
    monkeypatch.setattr(client, 'execute_skill', execute)
    response = mcp.server.dispatch_tool('refresh_signature', {'name': 'auditFn'}, {})
    assert not response.get('isError'), response
    assert calls == ["arglist('auditFn)"]
    assert not dispatch(name='auditFn', kwargs={'b': 2}).get('isError')
    assert calls == ["arglist('auditFn)", 'auditFn(2)']


@pytest.mark.parametrize('tool', ['call_registered', 'refresh_signature'])
def test_invalid_function_name_rejected_before_probe(session, tool):
    _, calls = session
    response = mcp.server.dispatch_tool(tool, {'name': 'bad)'}, {})
    assert response['structuredContent']['fusion_error']['stage'] == 'validation'
    assert response['structuredContent']['outcome'] == 'not_started'
    assert calls == []


def test_additional_property_reports_the_unknown_key(session):
    response = mcp.server.dispatch_tool('registry_search', {'query': 'plus', 'limt': 1}, {})
    detail = response['structuredContent']['fusion_error']
    assert detail['unexpected_fields'] == ['limt']


def test_missing_field_is_reported_as_missing(session):
    response = mcp.server.dispatch_tool('call_registered', {}, {})
    detail = response['structuredContent']['fusion_error']
    assert detail['path'] == '$.name' and detail['actual_type'] == 'missing'


@pytest.mark.parametrize('tool', ['call_registered', 'refresh_signature'])
def test_client_setup_failure_is_not_target_execution(monkeypatch, tool):
    def no_configuration():
        raise ValueError('No remote host configured; set VB_REMOTE_HOST')
    monkeypatch.setattr(mcp.server, '_client', no_configuration)
    response = mcp.server.dispatch_tool(tool, {'name': 'plus'}, {})
    detail = response['structuredContent']['fusion_error']
    assert detail['stage'] == 'connection_setup' and detail['target_execution'] == 'not_started'
    assert response['structuredContent']['outcome'] == 'not_started'
    assert 'VB_REMOTE_HOST' in detail['message']
    assert 'guidance' not in response['structuredContent']


@pytest.mark.parametrize('metadata,state', [
    ({'request_sent': False, 'outcome': 'not_started'}, 'not_started'),
    ({'request_sent': False}, 'not_started'),
    ({'request_sent': True, 'outcome': 'completed'}, 'completed'),
    ({'request_sent': True, 'outcome': 'unknown'}, 'unknown'),
])
@pytest.mark.parametrize('as_exception', [False, True])
def test_execution_diagnostic_obeys_original_evidence(session, monkeypatch, metadata, state, as_exception):
    client, calls = session
    result = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['original failure'], metadata=metadata)
    def execute(code, **options):
        calls.append(code)
        if as_exception:
            error = RuntimeError('original failure')
            error.result = result
            raise error
        return result
    monkeypatch.setattr(client, 'execute_skill', execute)
    response = dispatch(name='auditFn', args=[1])
    assert response['structuredContent']['metadata'] == metadata
    assert response['structuredContent']['fusion_error']['target_execution'] == state
    assert calls == ['auditFn(1)']


def test_refresh_validation_has_no_reconciliation_guidance(session):
    response = mcp.server.dispatch_tool('refresh_signature', {'name': 'bad)'}, {})
    assert response['structuredContent']['outcome'] == 'not_started'
    assert 'reconcile' not in json.dumps(response)
