"""MCP session views share input loading, never mutable session records."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import json

import pytest

from virtuoso_bridge.models import ExecutionStatus, VirtuosoResult
from virtuoso_fusion.generic import GenericSkill
from virtuoso_fusion.registry import ApiRegistry


@pytest.fixture
def base(tmp_path):
    path = tmp_path / 'registry.json'
    path.write_text(json.dumps({
        'readA': {'params': [{'name': 'value', 'type': 'number'}], 'contexts': ['headless']},
        'readB': {'contexts': ['layout']},
        '_hidden': {'zero_args': True},
    }))
    return ApiRegistry(path)


def test_views_are_lazy_and_load_packaged_input_once(monkeypatch):
    original = gzip.open
    reads = []
    def counted(*args, **kwargs):
        reads.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(gzip, 'open', counted)
    base = ApiRegistry()
    first, second = base._session_view(), base._session_view()
    assert not reads
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda view: view.coverage()['total'], [first, second]))
    assert counts[0] == counts[1] > 0
    assert len(reads) == 1
    assert first._raw is second._raw is None
    assert first._overlay == second._overlay == {}


def test_get_isolates_nested_records_and_keeps_changes(base):
    first, second = base._session_view(), base._session_view()
    entry = first.get('readA')
    entry['params'][0]['name'] = 'changed'
    entry['contexts'].append('session')
    assert first.get('readA') is entry
    assert [row['name'] for row in first.search('read')] == ['readA', 'readB']
    assert second.get('readA')['params'][0]['name'] == 'value'
    assert base.get('readA')['contexts'] == ['headless']
    assert set(first._overlay) == {'readA'}
    assert first._raw is None
    assert first.get('absent') is None


def test_search_copies_only_results_and_coverage_does_not_materialize(base):
    session = base._session_view()
    found = session.search('readA')[0]
    found['params'][0]['name'] = 'search mutation'
    found['contexts'].clear()
    assert session._overlay == {}
    assert session.get('readA')['params'][0]['name'] == 'value'
    session.get('readB')['zero_args'] = True
    session.search('readB')[0]['contexts'].clear()
    assert session.get('readB')['contexts'] == ['layout']
    session._remember_signature('newName', '(arg)')
    assert session.coverage()['total'] == 4
    assert session.covered() == session.coverage()['with_signature'] == 4
    assert base.coverage()['with_signature'] == 2
    assert session._raw is None
    assert set(session._overlay) == {'readA', 'readB', 'newName'}


def test_raw_materializes_independent_full_mutable_dictionary(base):
    first, second = base._session_view(), base._session_view()
    entry = first.get('readA')
    records = first.raw
    assert records is first.raw
    assert records['readA'] is entry
    records['readB']['contexts'].append('raw mutation')
    del records['_hidden']
    records['rawAdded'] = {'zero_args': True}
    assert first.get('rawAdded') == {'zero_args': True}
    assert first.get('_hidden') is None
    assert second.get('readB')['contexts'] == ['layout']
    assert base.get('_hidden') == {'zero_args': True}
    assert first.coverage()['total'] == 3


def test_raw_setter_replaces_view_and_retains_validation(base):
    session = base._session_view()
    session.get('readA')
    with pytest.raises(ValueError):
        session.raw = {'bad': {'params': [{'name': 3}]}}
    assert session.get('readA') is not None
    replacement = {'replacement': {'zero_args': True}}
    session.raw = replacement
    assert session.raw is replacement
    assert session.get('readA') is None
    assert session.coverage()['total'] == 1
    assert base.get('readA') is not None


def test_live_signatures_stay_in_session_overlay(base):
    session = base._session_view()
    calls = []
    def execute(code, **kwargs):
        calls.append(code)
        return VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(value)')
    generic = GenericSkill(execute, session)
    generic.ensure_signature('readB')
    generic.ensure_signature('unknown')
    assert calls == ["arglist('readB)", "arglist('unknown)"]
    assert session.get('readB') == {
        'contexts': ['layout'], 'arglist_raw': '(value)', 'params_source': 'arglist-live',
    }
    assert session.get('unknown') == {
        'name': 'unknown', 'arglist_raw': '(value)', 'params_source': 'arglist-live',
    }
    assert base.get('readB') == {'contexts': ['layout']}
    assert base.get('unknown') is None
    assert session._raw is None
    assert set(session._overlay) == {'readB', 'unknown'}
