"""Registry-assisted calls over the same executor as the engineering interfaces."""
import re

from .diagnostics import call_failure
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
        result, _ = self._call(fn, args, kwargs, timeout)
        return result

    def _call(self, fn, args, kwargs, timeout):
        # Keep SKILL keyword data separate from Python's fn/self/timeout controls.
        stage = 'validation'
        reason = None
        try:
            validate_name(fn)
            stage = 'policy'
            allowed, reason = self.policy.check(fn, allow_write=self.allow_write)
            if not allowed:
                raise PermissionError(f'{reason}: {fn}')
            options = {'retry_connect': False}
            if timeout is not None:
                options['timeout'] = timeout
            if self.auto_signature:
                stage = 'signature_probe'
                probe = self._ensure_signature(fn, options)
                if probe is not None and not probe.ok:
                    return probe, stage
            stage = 'argument_binding'
            ordered = self.order_args(fn, args, kwargs)
            stage = 'encoding'
            code = fn + '(' + ' '.join(to_skill(value) for value in ordered) + ')'
            stage = 'execution'
            result = self.executor(code, **options)
        except Exception as exc:
            if stage != 'validation':
                self._probed.discard(fn)
            exc.fusion_error = call_failure(stage, fn, str(exc),
                                           code=reason if stage == 'policy' else None,
                                           metadata=getattr(getattr(exc, 'result', None), 'metadata', None))
            raise
        if self.auto_signature and not result.ok:
            # A failed call must not freeze a missing signature before its context loads.
            self._probed.discard(fn)
        return result, stage

    def ensure_signature(self, fn, *, timeout=None, force=False):
        """Cache live arglist output; force discards stale metadata before probing.

        A failed forced probe leaves the signature uncached for the next call.
        This probes the signature only and never executes the target function.
        """
        stage = 'validation'
        try:
            validate_name(fn)
            stage = 'signature_probe'
            if force:
                self._probed.discard(fn)
                self.reg._forget_signature(fn)
            options = {'retry_connect': False}
            if timeout is not None:
                options['timeout'] = timeout
            return self._ensure_signature(fn, options)
        except Exception as exc:
            exc.fusion_error = call_failure(stage, fn, str(exc))
            raise

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
        if raw and raw not in ('nil', 'unknown'):
            self.reg._remember_signature(fn, raw)
        return result

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.call(name, *args, **kwargs)


def _strip_prefix(name):
    name = name.strip()
    return name[2:] if name.startswith(PREFIXES) and len(name) > 2 else name
