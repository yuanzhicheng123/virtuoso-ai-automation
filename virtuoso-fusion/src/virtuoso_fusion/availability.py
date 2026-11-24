"""Registry hints about execution context, without claiming live verification."""


def availability(reg, fn):
    entry = reg.get(fn)
    base = {'fn': fn, 'verified': False, 'basis': 'registry-only'}
    if entry is None:
        return {**base, 'state': 'NOT_IN_REGISTRY', 'reason': 'Function absent from registry'}
    contexts = entry.get('contexts') or []
    base['contexts'] = contexts
    if any(context in ('schematic-window', 'layout-window') for context in contexts) and 'headless' not in contexts:
        return {**base, 'state': 'NEEDS_WINDOW', 'reason': 'Registry requires a schematic or layout window'}
    if not reg.has_signature(entry):
        return {**base, 'state': 'NO_SIGNATURE', 'reason': 'Only explicit positional calls are available'}
    return {**base, 'state': 'HEADLESS_CANDIDATE', 'reason': 'Signature exists; live availability still requires verification'}
