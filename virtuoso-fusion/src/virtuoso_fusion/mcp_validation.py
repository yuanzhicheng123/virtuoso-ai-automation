"""Validate advertised MCP input schemas before calling tool handlers."""
import json
import math

from jsonschema import Draft202012Validator


def make_dispatch(dispatch, tool_defs, aliases, tool_error):
    validators = {tool['name']: Draft202012Validator(tool['inputSchema'])
                  for tool in tool_defs}

    def validated_dispatch(name, args, params):
        canonical = aliases.get(name, name)
        validator = validators.get(canonical)
        if validator is None:
            return dispatch(name, args, params)
        arguments = {} if args is None else args
        error = next(validator.iter_errors(arguments), None)
        if error is not None:
            path = list(error.absolute_path)
            if error.validator == 'required':
                path.append(next(key for key in error.validator_value if key not in error.instance))
            return tool_error(f'Invalid arguments for {canonical} at {_parameter_path(path)}: '
                              f'violates {error.validator}')
        path = _nonfinite_path(arguments)
        if path is not None:
            return tool_error(f'Invalid arguments for {canonical} at {_parameter_path(path)}: '
                              'number must be finite')
        return dispatch(canonical, arguments, params)

    return validated_dispatch


def _parameter_path(parts):
    path = '$'
    for part in parts:
        if isinstance(part, int):
            path += f'[{part}]'
        else:
            key = str(part)[:80]
            path += '.' + key if key.isidentifier() else '[' + json.dumps(key) + ']'
    return path


def _nonfinite_path(value):
    if isinstance(value, float) and not math.isfinite(value):
        return []
    children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, child in children:
        path = _nonfinite_path(child)
        if path is not None:
            return [key, *path]
    return None
