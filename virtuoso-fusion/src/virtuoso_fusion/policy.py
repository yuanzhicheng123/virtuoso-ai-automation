"""Known-write guard for generic calls; not a security sandbox for arbitrary SKILL."""
from .registry import risk_of

KNOWN_WRITE_ALLOWLIST = frozenset({
    'dbCreateNet', 'dbCreateInstByMasterName', 'dbCreateConnByName',
    'dbReplaceProp', 'dbSave', 'dbClose', 'schCreatePin', 'schCreateWire',
    'ddCreateLib', 'dbOpenCellViewByType',
})


class Policy:
    def __init__(self, write_allowlist=None):
        self.write_allow = set(KNOWN_WRITE_ALLOWLIST if write_allowlist is None else write_allowlist)

    def classify(self, fn):
        return 'allow_write' if fn in self.write_allow else 'unknown'

    def check(self, fn, allow_write=False):
        if risk_of(fn) == 'write' or fn in self.write_allow:
            if allow_write is not True:
                return False, 'WRITE_NEEDS_EXPLICIT_ALLOW'
            if fn not in self.write_allow:
                return False, 'WRITE_NOT_ALLOWLISTED'
        return True, 'OK'
