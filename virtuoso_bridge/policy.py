"""写操作允许清单 + 可用性判定（不靠名字猜风险）。"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(ROOT, "registry", "engineering_operations.json")
FALLBACK_WRITE = {
    "dbCreateNet", "dbCreateInstByMasterName", "dbCreateConnByName",
    "dbReplaceProp", "dbSave", "dbClose", "schCreatePin", "schCreateWire",
    "ddCreateLib", "dbOpenCellViewByType",
}


def load_write_allowlist(path=OPS):
    """已验证的写操作：engineering_operations 里 risk=write 且带证据的 operation。

    注意：operation 是工程动作（可能由多个 SKILL 调用组成），这里同时给出
    这些操作实际会用到的底层 SKILL 写函数白名单。
    """
    allow = set(FALLBACK_WRITE)
    try:
        with open(path) as f:
            ops = json.load(f)["operations"]
        for name, op in ops.items():
            if (op.get("risk") or "").lower() == "write" and op.get("evidence"):
                for fn in (op.get("skill_calls") or []):
                    allow.add(fn)
    except Exception:
        pass
    return allow


class Policy(object):
    def __init__(self, write_allowlist=None):
        self.write_allow = set(write_allowlist or load_write_allowlist())

    def classify(self, fn):
        if fn in self.write_allow:
            return "allow_write"
        return "unknown"

    def check(self, fn, allow_write=False):
        """返回 (allowed, reason)。"""
        if fn in self.write_allow and not allow_write:
            return False, "WRITE_NEEDS_EXPLICIT_ALLOW"
        return True, "OK"
