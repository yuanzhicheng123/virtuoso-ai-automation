"""API registry：加载 v2（含 mined arglist）、查询、覆盖率、风险分类。"""
import json, os, re

WRITE_PREFIX = ("dbCreate", "dbSet", "dbReplace", "dbDelete", "dbSave", "dbClose",
                "schCreate", "schDelete", "leCreate", "leDelete", "rodCreate",
                "ddCreate", "ddDelete", "hiSet", "geCreate", "deSet", "techSet",
                "cdfCreate", "cdfSet", "cdfUpdate", "axlSet", "artSet")
_TYPE_RE = re.compile(r'\(\s*([A-Za-z_][A-Za-z0-9_!?\-]*)\s+"([a-z])"')

DEFAULT_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "audit", "2026-10-22", "registry", "api_registry_v2.json"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "audit", "2026-10-22", "registry", "api_registry.json"),
]
DEFAULT_REGISTRY = next((p for p in DEFAULT_CANDIDATES if os.path.isfile(p)), None)


def risk_of(fn):
    return "write" if fn.startswith(WRITE_PREFIX) else "read"


def parse_arglist(raw):
    """把 '(a "t" b "t")' 解析成 [{'name','type'}]；无类型信息时 type='unknown'。"""
    if not raw:
        return []
    pairs = _TYPE_RE.findall(raw)
    if pairs:
        return [{"name": n, "type": t, "optional": False} for n, t in pairs]
    inner = raw.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    return [{"name": t, "type": "unknown", "optional": False}
            for t in re.findall(r"[A-Za-z_][A-Za-z0-9_!?\-]*", inner)]


class ApiRegistry(object):
    def __init__(self, path=None):
        self.path = path or DEFAULT_REGISTRY
        if not self.path or not os.path.isfile(self.path):
            raise IOError("找不到 API registry: %r" % (self.path,))
        with open(self.path) as f:
            self.raw = json.load(f)

    def get(self, fn):
        return self.raw.get(fn)

    def params(self, fn):
        e = self.get(fn) or {}
        if e.get("params"):
            return e["params"]
        return parse_arglist(e.get("arglist_raw"))

    @staticmethod
    def has_signature(entry):
        return bool(entry.get("params") or entry.get("arglist_raw") or entry.get("zero_args"))

    def covered(self):
        return sum(1 for v in self.raw.values() if self.has_signature(v))

    def coverage(self):
        n = len(self.raw)
        sig = self.covered()
        public = {k: v for k, v in self.raw.items() if not k.startswith("_")}
        pub_sig = sum(1 for v in public.values() if self.has_signature(v))
        ro = sum(1 for k, v in self.raw.items()
                 if self.has_signature(v) and risk_of(k) == "read")
        return {"total": n, "with_signature": sig,
                "coverage": round(sig / float(n), 4),
                "public": len(public), "public_with_signature": pub_sig,
                "public_coverage": round(pub_sig / float(len(public)), 4),
                "read_only_with_signature": ro}
