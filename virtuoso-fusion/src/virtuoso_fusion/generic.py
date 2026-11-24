"""Registry-assisted calls over the same executor as the engineering interfaces."""
import re

from .policy import Policy
from .registry import ApiRegistry
from .values import to_skill, validate_name

PREFIXES = ('n_', 't_', 'S_', 'l_', 'g_', 'x_', 'd_', 'u_', 'b_', 'o_', 'f_', 's_', 'w_', 'p_')


class GenericSkill:
    def __init__(self, executor, registry=None, allow_write=False, auto_signature=True):
        self.executor = executor
        self.reg = registry if isinstance(registry, ApiRegistry) else ApiRegistry(registry)
        self.allow_write = allow_write
        self.auto_signature = auto_signature
        self.policy = Policy()
        self._probed = set()

    def call(self, fn, *args, timeout=None, **kwargs):
        """Return the complete upstream result; timeout is per executor operation.

        A SKILL argument named timeout must be supplied positionally.
        """
        validate_name(fn)
        allowed, reason = self.policy.check(fn, allow_write=self.allow_write)
        if not allowed:
            raise PermissionError(f'{reason}: {fn}')
        options = {'retry_connect': False}
        if timeout is not None:
            options['timeout'] = timeout
        if self.auto_signature:
            probe = self._ensure_signature(fn, options)
            if probe is not None and not probe.ok:
                return probe
        ordered = self.order_args(fn, args, kwargs)
        code = fn + '(' + ' '.join(to_skill(value) for value in ordered) + ')'
        return self.executor(code, **options)

    def ensure_signature(self, fn, *, timeout=None):
        """Cache successful live arglist output in memory and retain probe evidence."""
        validate_name(fn)
        options = {'retry_connect': False}
        if timeout is not None:
            options['timeout'] = timeout
        return self._ensure_signature(fn, options)

    def order_args(self, fn, args, kwargs):
        if not kwargs:
            return list(args)
        params = self._positional_params(fn)
        if not params:
            raise TypeError(f'{fn} has no unambiguous signature; use positional arguments')
        names = [_strip_prefix(param['name']) for param in params]
        slots = list(args) + [None] * max(0, len(params) - len(args))
        given = set(range(len(args)))
        for key, value in kwargs.items():
            name = _strip_prefix(key)
            if name not in names:
                raise TypeError(f'{fn}: unknown argument {key!r}; available: {names}')
            index = names.index(name)
            if index in given:
                raise TypeError(f'{fn}: duplicate argument {key!r}')
            slots[index] = value
            given.add(index)
        for index, param in enumerate(params):
            if index not in given and not param.get('optional'):
                raise TypeError(f'{fn}: missing required argument {names[index]!r}')
        while slots and len(slots) - 1 not in given:
            slots.pop()
        return slots

    def _positional_params(self, fn):
        params = self.reg.params(fn)
        if any('...' in param['name'] for param in params[:-1]):
            return []
        if params and '...' in params[-1]['name']:
            params = params[:-1]
        names = [_strip_prefix(param['name']) for param in params]
        if len(set(names)) != len(names) or any(name in ('nil', '___') or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in names):
            return []
        return params

    def _ensure_signature(self, fn, options):
        record = self.reg.get(fn)
        if fn in self._probed or record is not None and self.reg.has_signature(record):
            return None
        result = self.executor(f"arglist('{fn})", **options)
        if not result.ok:
            return result
        self._probed.add(fn)
        raw = result.output.strip()
        if raw and raw != 'nil':
            record = self.reg.raw.setdefault(fn, {'name': fn})
            record['arglist_raw'] = raw
            record['params_source'] = 'arglist-live'
        return result

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.call(name, *args, **kwargs)


def _strip_prefix(name):
    name = name.strip()
    return name[2:] if name.startswith(PREFIXES) and len(name) > 2 else name
