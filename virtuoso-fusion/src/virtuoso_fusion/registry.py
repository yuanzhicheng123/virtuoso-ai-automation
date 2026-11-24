"""Environment-specific signature inventory, not a catalogue of verified actions."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import re
from threading import Lock

PACKAGED_REGISTRY = Path(__file__).parent / '_data' / 'api_registry_v2.json.gz'
WRITE_PREFIX = (
    'dbCreate', 'dbSet', 'dbReplace', 'dbDelete', 'dbSave', 'dbClose',
    'schCreate', 'schDelete', 'leCreate', 'leDelete', 'rodCreate',
    'ddCreate', 'ddDelete', 'hiSet', 'geCreate', 'deSet', 'techSet',
    'cdfCreate', 'cdfSet', 'cdfUpdate', 'axlSet', 'artSet',
)


class ApiRegistry:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else PACKAGED_REGISTRY
        self.is_packaged = self.path.resolve() == PACKAGED_REGISTRY.resolve()
        self._raw = None
        self._base = None
        self._overlay = {}
        self._load_lock = Lock()
        if not self.is_packaged:
            self.raw  # Custom input retains fail-fast validation at construction.

    @property
    def raw(self):
        if self._raw is None:
            with self._load_lock:
                if self._raw is None:
                    if self._base is not None:
                        records = {
                            name: self._overlay[name] if name in self._overlay else deepcopy(record)
                            for name, record in self._items()
                        }
                        self._base = None
                        self._overlay = {}
                    else:
                        opener = gzip.open if self.path.suffix == '.gz' else open
                        with opener(self.path, 'rt', encoding='utf-8') as stream:
                            records = json.load(stream)
                        _validate_records(records)
                    self._raw = records
        return self._raw

    @raw.setter
    def raw(self, records):
        _validate_records(records)
        self._raw = records
        self._base = None
        self._overlay = {}

    def _session_view(self):
        """Create a mutable session view of a private, read-only base."""
        view = ApiRegistry()
        view.path, view.is_packaged = self.path, self.is_packaged
        view._base = self
        return view

    def get(self, fn):
        if self._base is not None and self._raw is None:
            with self._load_lock:
                if self._raw is not None:
                    return self._raw.get(fn)
                if fn not in self._overlay:
                    record = self._base.get(fn)
                    if record is None:
                        return None
                    self._overlay[fn] = deepcopy(record)
                return self._overlay[fn]
        return self.raw.get(fn)

    def _remember_signature(self, fn, raw):
        record = self.get(fn)
        if record is None:
            records = self._overlay if self._base is not None else self.raw
            record = records.setdefault(fn, {'name': fn})
        record['arglist_raw'] = raw
        record['params_source'] = 'arglist-live'

    def _items(self):
        if self._base is None:
            yield from self.raw.items()
            return
        for name, record in self._base._items():
            yield name, self._overlay.get(name, record)
        for name, record in self._overlay.items():
            if self._base.get(name) is None:
                yield name, record

    def params(self, fn):
        record = self.get(fn) or {}
        return record.get('params') or parse_arglist(record.get('arglist_raw'))

    def search(self, query, limit=20):
        if not isinstance(query, str):
            raise TypeError('query must be a string')
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError('limit must be a non-negative integer')
        needle = query.casefold()
        results = []
        for name, record in self._items():
            if len(results) == limit:
                break
            if needle in name.casefold():
                results.append({**deepcopy(record), 'name': name})
        return results

    @staticmethod
    def has_signature(entry):
        if entry.get('params') or entry.get('zero_args'):
            return True
        raw = (entry.get('arglist_raw') or '').strip()
        return bool(raw) and raw not in ('nil', 'unknown')

    def covered(self):
        return sum(self.has_signature(record) for _, record in self._items())

    def coverage(self):
        total = signed = public = public_signed = read_only_signed = 0
        for name, record in self._items():
            has_signature = self.has_signature(record)
            total += 1
            signed += has_signature
            if not name.startswith('_'):
                public += 1
                public_signed += has_signature
            read_only_signed += has_signature and risk_of(name) == 'read'
        return {
            'total': total, 'with_signature': signed,
            'coverage': round(signed / total, 4) if total else 0.0,
            'public': public, 'public_with_signature': public_signed,
            'public_coverage': round(public_signed / public, 4) if public else 0.0,
            'read_only_with_signature': read_only_signed,
            'meaning': 'signature coverage, not engineering validation',
        }


def risk_of(fn):
    """Name heuristic only: custom functions can have arbitrary side effects."""
    return 'write' if fn.startswith(WRITE_PREFIX) else 'read'


def parse_arglist(raw):
    if not raw:
        return []
    inner = raw.strip()
    if inner in ('nil', 'unknown'):
        return []
    if inner.startswith('(') and inner.endswith(')'):
        inner = inner[1:-1]
    if any(token in inner for token in ('(', ')', '@key', '|')):
        return []
    params = []
    optional = False
    for token in re.findall(r'"[^\"]*"|[^\s]+', inner):
        token = token.lstrip('\\')
        if token == '@optional':
            optional = True
        elif token == '@rest':
            params.append({'name': '...', 'type': 'unknown', 'optional': True})
            break
        elif token.startswith('"'):
            if params:
                params[-1]['type'] = token.strip('"')
        elif re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', token):
            params.append({'name': token, 'type': 'unknown', 'optional': optional})
        else:
            return []
    return params


def _validate_records(raw):
    if not isinstance(raw, dict) or not all(isinstance(record, dict) for record in raw.values()):
        raise ValueError('API registry must map function names to records')
    for record in raw.values():
        params = record.get('params')
        if params is not None and (not isinstance(params, list) or any(not isinstance(param, dict) or not isinstance(param.get('name'), str) for param in params)):
            raise ValueError('API registry params must be a list of named records')
        if record.get('arglist_raw') is not None and not isinstance(record['arglist_raw'], str):
            raise ValueError('API registry arglist_raw must be a string')
        if record.get('contexts') is not None and (not isinstance(record['contexts'], list) or not all(isinstance(context, str) for context in record['contexts'])):
            raise ValueError('API registry contexts must be a list of strings')
