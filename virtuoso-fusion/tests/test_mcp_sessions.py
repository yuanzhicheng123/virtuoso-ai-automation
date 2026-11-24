"""Reuse MCP session state without sharing permissions or mutable records."""
import gzip
import json

import pytest
from virtuoso_bridge import VirtuosoClient, VirtuosoResult, ExecutionStatus
from virtuoso_fusion import FusionClient, ApiRegistry, mcp


@pytest.fixture
def session(tmp_path, monkeypatch):
    path = tmp_path/'registry.json'
    path.write_text('{}')
    calls = []
    def execute(self, code, **options):
        calls.append(code)
        return VirtuosoResult(status=ExecutionStatus.SUCCESS,
                              output='nil' if code.startswith('arglist(') else '3')
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', execute)
    with FusionClient(registry=path) as client:
        monkeypatch.setitem(mcp.server._CLIENT, 'obj', client)
        yield client, calls


def call(name='audit_nil_signature', allow_write=False):
    return mcp.server.dispatch_tool('call_registered',
        {'name': name, 'args': [1, 2], 'allow_write': allow_write}, {})


def test_nil_probe_cached_across_calls_and_permissions(session):
    client, calls = session
    for permission in [False, True, False]:
        assert not call(allow_write=permission).get('isError')
    assert calls == ["arglist('audit_nil_signature)"] + ['audit_nil_signature(1 2)']*3
    assert client.allow_write is False


def test_write_permission_is_per_request(session):
    client, calls = session
    assert not call('dbSave', True).get('isError')
    count = len(calls)
    assert call('dbSave', False)['isError']
    assert len(calls) == count and client.allow_write is False


def test_failed_probe_is_retried_without_replaying_execution(session, monkeypatch):
    client, calls = session
    original = client.execute_skill
    def execute(code, **options):
        if not calls:
            calls.append(code)
            return VirtuosoResult(status=ExecutionStatus.ERROR, errors=['probe failed'])
        return original(code, **options)
    monkeypatch.setattr(client, 'execute_skill', execute)
    assert call()['isError']
    assert not call().get('isError')
    assert not call().get('isError')
    assert calls.count("arglist('audit_nil_signature)") == 2
    assert calls.count('audit_nil_signature(1 2)') == 2


def test_registry_replacement_resets_mcp_probe_cache(session):
    client, calls = session
    assert not call().get('isError')
    other = ApiRegistry(client.registry.path)
    client.registry = other
    assert not call().get('isError')
    assert client.registry is other
    assert calls.count("arglist('audit_nil_signature)") == 2


@pytest.mark.parametrize('search_first', [True, False])
def test_mcp_search_and_calls_load_one_base_without_mutation(monkeypatch, search_first):
    monkeypatch.setattr(mcp, '_registry', None)
    original = gzip.open
    loads = []
    def read(*args, **kwargs):
        loads.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(gzip, 'open', read)
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', lambda *args, **kwargs:
                        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='3'))
    with FusionClient() as client:
        monkeypatch.setitem(mcp.server._CLIENT, 'obj', client)
        if search_first:
            mcp._registry_search({'query':'plus', 'limit':1})
        assert not call('plus').get('isError')
        mcp._registry_search({'query':'plus', 'limit':1})
        assert len(loads) == 1
        base = mcp._registry_data()
        assert client.registry is not base and client.registry._raw is None
        assert client.generic.reg is client.registry
        client.registry.get('plus')['audit_changed'] = True
        assert 'audit_changed' not in base.get('plus')


def test_preloaded_registry_keeps_user_modifications(monkeypatch):
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', lambda *args, **kwargs:
                        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='3'))
    with FusionClient() as client:
        registry = client.registry
        registry.get('plus')['audit_changed'] = True
        monkeypatch.setitem(mcp.server._CLIENT, 'obj', client)
        assert not call('plus').get('isError')
        assert client.registry is registry and client.registry.get('plus')['audit_changed']


def test_nil_probe_followed_by_failed_call_can_learn_later_signature(session, monkeypatch):
    client, calls = session
    def execute(code, **options):
        calls.append(code)
        if code.startswith('arglist('):
            return VirtuosoResult(status=ExecutionStatus.SUCCESS,
                                  output='nil' if len(calls)==1 else '(first second)')
        if len(calls)==2:
            return VirtuosoResult(status=ExecutionStatus.ERROR, errors=['function not loaded'])
        return VirtuosoResult(status=ExecutionStatus.SUCCESS, output='7')
    monkeypatch.setattr(client, 'execute_skill', execute)
    assert call()['isError']
    response = mcp.server.dispatch_tool('call_registered',
        {'name':'audit_nil_signature','kwargs':{'first':3,'second':4}}, {})
    assert not response.get('isError'), response
    assert calls == ["arglist('audit_nil_signature)", 'audit_nil_signature(1 2)',
                     "arglist('audit_nil_signature)", 'audit_nil_signature(3 4)']


def test_argument_failure_does_not_preserve_nil_probe(session):
    client, calls = session
    response = mcp.server.dispatch_tool('call_registered',
        {'name':'audit_nil_signature','kwargs':{'first':1}}, {})
    assert response['isError']
    assert not call().get('isError')
    assert calls.count("arglist('audit_nil_signature)") == 2
