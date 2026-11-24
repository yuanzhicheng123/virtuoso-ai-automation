"""Explicit Python-to-SKILL values; strings are always data, never expressions."""
from dataclasses import dataclass
import json
import math
import re


@dataclass(frozen=True)
class Symbol:
    name: str

    def __post_init__(self):
        validate_name(self.name)


def to_skill(value):
    if value is None or value is False:
        return 'nil'
    if value is True:
        return 't'
    if isinstance(value, Symbol):
        return "'" + value.name
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('SKILL numbers must be finite')
        return repr(value)
    if isinstance(value, (list, tuple)):
        return 'list(' + ' '.join(to_skill(item) for item in value) + ')'
    raise TypeError(f'Unsupported SKILL value type: {type(value).__name__}')


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_?!]*', name):
        raise ValueError(f'Invalid SKILL function or symbol name: {name!r}')
