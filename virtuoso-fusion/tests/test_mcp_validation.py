"""Use the advertised tool schemas and count actual dispatch entry calls."""
import math

import pytest

from virtuoso_fusion import mcp


@pytest.fixture
def guarded():
    from virtuoso_fusion.mcp_validation import make_dispatch
    calls = []

    def dispatch(name, arguments, params):
        calls.append((name, arguments, params))
        return {'accepted': True}

    wrapped = make_dispatch(dispatch, mcp.server._tool_defs(), mcp.server.ALIASES,
                            mcp.server._tool_error)
    return wrapped, calls


@pytest.mark.parametrize('name,arguments,path,constraint', [
    ('read_waveform', {'path': '/tmp/wave', 'at': ['private-input']}, '$.at[0]', 'type'),
    ('read_waveform', {'path': '/tmp/wave', 'at': [0] * 201}, '$.at', 'maxItems'),
    ('registry_search', {'query': 'getVersion', 'limit': 201}, '$.limit', 'maximum'),
    ('registry_search', {'query': 'getVersion', 'limit': True}, '$.limit', 'type'),
    ('registry_search', {'query': 'getVersion', 'extra': 'private-input'}, '$', 'additionalProperties'),
    ('evaluate', {}, '$.expr', 'required'),
    ('evaluate', {'expr': 'private-input', 'timeout': '60'}, '$.timeout', 'type'),
    ('status', [], '$', 'type'),
    ('status', 'private-input', '$', 'type'),
    ('status', False, '$', 'type'),
    ('call_registered', {'name': 'plus', 'kwargs': []}, '$.kwargs', 'type'),
])
def test_invalid_input_never_enters_handler(guarded, name, arguments, path, constraint):
    wrapped, calls = guarded
    result = wrapped(name, arguments, {})
    assert result.get('isError') is True
    message = result['content'][0]['text']
    assert name in message and path in message and constraint in message
    assert 'private-input' not in message
    assert len(message) < 300
    assert calls == []


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize('name,arguments,path', [
    ('read_waveform', {'path': '/tmp/wave', 'at': []}, '$.at[0]'),
    ('call_registered', {'name': 'plus', 'kwargs': {}}, '$.kwargs.payload[0]'),
])
def test_nonfinite_numbers_rejected_even_in_open_nested_values(guarded, value, name, arguments, path):
    wrapped, calls = guarded
    arguments = dict(arguments)
    if name == 'read_waveform':
        arguments['at'] = [value]
    else:
        arguments['kwargs'] = {'payload': [value]}
    result = wrapped(name, arguments, {})
    assert result.get('isError') is True
    message = result['content'][0]['text']
    assert name in message and path in message and 'finite' in message
    assert calls == []


def test_alias_is_validated_using_canonical_schema(guarded):
    wrapped, calls = guarded
    result = wrapped('skill_eval', {'expr': 42}, {})
    assert result.get('isError') is True
    assert 'evaluate' in result['content'][0]['text']
    assert calls == []
    assert wrapped('skill_eval', {'expr': '1+1'}, {'marker': 7}) == {'accepted': True}
    assert calls == [('evaluate', {'expr': '1+1'}, {'marker': 7})]


def test_none_is_empty_object_without_default_injection(guarded):
    wrapped, calls = guarded
    assert wrapped('status', None, {}) == {'accepted': True}
    arguments = {'name': 'getVersion'}
    assert wrapped('call_registered', arguments, {}) == {'accepted': True}
    assert calls == [('status', {}, {}), ('call_registered', {'name': 'getVersion'}, {})]
    assert calls[-1][1] is arguments
    assert arguments == {'name': 'getVersion'}


def test_valid_open_arguments_preserved(guarded):
    wrapped, calls = guarded
    arguments = {'name': 'probe', 'args': [1, None, {'nested': [False, 'symbol']}],
                 'kwargs': {'arbitrary': {'children': [2.5, True]}}}
    assert wrapped('call_registered', arguments, {}) == {'accepted': True}
    assert calls[0][1] is arguments


def test_schema_validators_built_once(monkeypatch):
    from virtuoso_fusion import mcp_validation
    original = mcp_validation.Draft202012Validator
    constructed = []

    def validator(schema):
        constructed.append(schema)
        return original(schema)

    monkeypatch.setattr(mcp_validation, 'Draft202012Validator', validator)
    definitions = mcp.server._tool_defs()
    wrapped = mcp_validation.make_dispatch(lambda *args: {'accepted': True}, definitions,
                                          mcp.server.ALIASES, mcp.server._tool_error)
    assert len(constructed) == len(definitions) == 32
    for _ in range(10):
        assert wrapped('registry_search', {'query': 'getVersion'}, {})['accepted']
        assert wrapped('registry_search', {'query': 'getVersion', 'limit': 201}, {})['isError']
    assert len(constructed) == 32


def test_unknown_tool_preserves_dispatch_behavior(guarded):
    wrapped, calls = guarded
    assert wrapped('not_a_tool', {}, {}) == {'accepted': True}
    assert calls == [('not_a_tool', {}, {})]


def test_all_advertised_fusion_schemas_are_valid():
    from virtuoso_fusion import mcp
    from jsonschema import Draft202012Validator
    definitions = mcp._tool_defs()
    assert len({tool['name'] for tool in definitions}) == len(definitions)
    for tool in definitions:
        Draft202012Validator.check_schema(tool['inputSchema'])
