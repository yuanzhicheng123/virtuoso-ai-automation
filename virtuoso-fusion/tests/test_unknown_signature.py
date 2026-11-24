"""Cadence's bare unknown/nil arglist results are not parameter lists."""
import pytest

from virtuoso_bridge.models import ExecutionStatus, VirtuosoResult
from virtuoso_fusion.generic import GenericSkill
from virtuoso_fusion.registry import ApiRegistry, parse_arglist


@pytest.mark.parametrize('raw', ['unknown', 'nil', ' unknown ', '\nnil\n'])
def test_unknown_and_nil_are_not_signatures(raw):
    assert parse_arglist(raw) == []
    assert ApiRegistry.has_signature({'arglist_raw': raw, 'params': []}) is False
    assert ApiRegistry.has_signature({'arglist_raw': raw, 'params': [{'name': 'value'}]}) is True
    assert ApiRegistry.has_signature({'arglist_raw': raw, 'zero_args': True}) is True


def test_parenthesized_unknown_remains_a_parameter_name():
    assert parse_arglist('(unknown)') == [{'name': 'unknown', 'type': 'unknown', 'optional': False}]
    assert ApiRegistry.has_signature({'arglist_raw': '(unknown)'}) is True


@pytest.mark.parametrize('raw', ['unknown', 'nil'])
def test_failed_unknown_call_reprobes_only_on_next_explicit_call(raw):
    registry = ApiRegistry()
    registry.raw = {}
    failure = VirtuosoResult(status=ExecutionStatus.FAILURE, errors=['undefined function'])
    success = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='7')
    responses = iter([
        VirtuosoResult(status=ExecutionStatus.SUCCESS, output=raw), failure,
        VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(value)'), success,
    ])
    calls = []
    def execute(code, **options):
        calls.append(code)
        return next(responses)
    generic = GenericSkill(execute, registry)
    assert generic.call('readAfterLoad', 7) is failure
    assert calls == ["arglist('readAfterLoad)", 'readAfterLoad(7)']
    assert registry.get('readAfterLoad') is None
    assert generic.call('readAfterLoad', value=7) is success
    assert calls == ["arglist('readAfterLoad)", 'readAfterLoad(7)',
                     "arglist('readAfterLoad)", 'readAfterLoad(7)']
    assert registry.get('readAfterLoad')['params_source'] == 'arglist-live'


def test_packaged_unknown_record_can_receive_live_signature_in_session():
    base = ApiRegistry()
    name = next(name for name, record in base.raw.items()
                if record.get('arglist_raw') == 'unknown' and not record.get('params'))
    session = base._session_view()
    calls = []
    def execute(code, **options):
        calls.append(code)
        return VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(value)')
    generic = GenericSkill(execute, session)
    generic.ensure_signature(name)
    assert calls == [f"arglist('{name})"]
    assert session.params(name) == [{'name': 'value', 'type': 'unknown', 'optional': False}]
    assert session.get(name)['params_source'] == 'arglist-live'
    assert base.get(name)['arglist_raw'] == 'unknown'
    assert session._raw is None
