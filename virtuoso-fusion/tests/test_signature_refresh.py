from copy import deepcopy

import pytest

from virtuoso_bridge.models import ExecutionStatus, VirtuosoResult
from virtuoso_fusion.generic import GenericSkill
from virtuoso_fusion.registry import ApiRegistry


@pytest.fixture
def refresh_session():
    base = ApiRegistry()
    base.raw = {'auditFn': {
        'name': 'auditFn', 'arglist_raw': '(old)',
        'params': [{'name': 'old'}], 'zero_args': True,
        'params_source': 'packaged', 'contexts': ['layout'],
    }}
    calls = []
    responses = []

    def execute(code, **options):
        calls.append((code, options))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    return base, GenericSkill(execute, base._session_view()), calls, responses


def test_forced_refresh_replaces_all_signature_forms_and_preserves_isolation(refresh_session):
    base, generic, calls, responses = refresh_session
    original = deepcopy(base.raw)
    sibling = base._session_view()
    sibling.get('auditFn')
    refreshed = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(newName)')
    executed = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='7')
    responses.extend([refreshed, executed])

    assert generic.ensure_signature('auditFn') is None
    assert generic.ensure_signature('auditFn', force=True, timeout=2.5) is refreshed
    assert generic.call('auditFn', newName=7) is executed
    assert calls == [
        ("arglist('auditFn)", {'retry_connect': False, 'timeout': 2.5}),
        ('auditFn(7)', {'retry_connect': False}),
    ]
    assert generic.reg.get('auditFn') == {
        'name': 'auditFn', 'arglist_raw': '(newName)',
        'params_source': 'arglist-live', 'contexts': ['layout'],
    }
    assert base.raw == original
    assert sibling.get('auditFn') == original['auditFn']


@pytest.mark.parametrize('output', ['nil', 'unknown', ''])
def test_forced_missing_signature_does_not_retain_stale_parameters(refresh_session, output):
    _, generic, calls, responses = refresh_session
    result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output=output)
    responses.append(result)
    assert generic.ensure_signature('auditFn', force=True) is result
    assert not generic.reg.has_signature(generic.reg.get('auditFn'))
    assert generic.reg.params('auditFn') == []
    with pytest.raises(TypeError, match='no unambiguous signature'):
        generic.call('auditFn', old=7)
    assert [code for code, _ in calls] == ["arglist('auditFn)"]


@pytest.mark.parametrize('failure', [
    VirtuosoResult(status=ExecutionStatus.ERROR, errors=['offline'], metadata={'request_id': 'refresh'}),
    ConnectionError('offline'),
])
def test_failed_refresh_preserves_evidence_and_allows_next_call_to_probe(refresh_session, failure):
    _, generic, calls, responses = refresh_session
    generic._probed.add('auditFn')
    responses.extend([
        failure,
        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(newName)'),
        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='7'),
    ])
    if isinstance(failure, Exception):
        with pytest.raises(ConnectionError) as caught:
            generic.ensure_signature('auditFn', force=True)
        assert caught.value is failure
    else:
        assert generic.ensure_signature('auditFn', force=True) is failure
    assert not generic.reg.has_signature(generic.reg.get('auditFn'))
    assert generic.call('auditFn', newName=7).ok
    assert [code for code, _ in calls] == [
        "arglist('auditFn)", "arglist('auditFn)", 'auditFn(7)',
    ]


def test_force_refresh_reprobes_a_previous_live_signature(refresh_session):
    _, generic, calls, responses = refresh_session
    responses.extend([
        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(first)'),
        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(second)'),
    ])
    assert generic.ensure_signature('auditFn', force=True).ok
    assert generic.ensure_signature('auditFn') is None
    assert generic.ensure_signature('auditFn', force=True).ok
    assert generic.reg.params('auditFn')[0]['name'] == 'second'
    assert len(calls) == 2
