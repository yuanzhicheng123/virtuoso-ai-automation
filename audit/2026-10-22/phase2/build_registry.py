"""Phase 2+3：语义恢复 + 能力注册（只读，生成 6 个数据结构）。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
A = ROOT / "audit" / "2026-10-22"
M = ROOT / "out" / "mine"
R = A / "registry"

TYPE_BY_PREFIX = [("dbobject", "object"), ("d_", "object"), ("txl_", "layerPurpose"),
                  ("t_", "string"), ("S_", "symbol"), ("n_", "number"),
                  ("x_", "integer"), ("f_", "float"), ("l_", "list"),
                  ("w_", "window"), ("g_", "general"), ("b_", "boolean")]

FAMILIES = [("dd", "library"), ("db", "database"), ("dm", "data-management"),
            ("sch", "schematic"), ("ge", "editor"), ("hi", "ui"),
            ("de", "design-editor"), ("le", "layout"), ("rod", "pcell"),
            ("pc", "pcell"), ("tech", "techfile"), ("cdf", "cdf"),
            ("mae", "ade"), ("asi", "simulation"), ("ocn", "ocean"),
            ("hnl", "netlist"), ("axl", "xl"), ("lmg", "xl"),
            ("abs", "abstract"), ("sv", "netlist"), ("vlog", "netlist"),
            ("drc", "verification"), ("lvs", "verification")]


def family(name: str) -> str:
    for p, f in FAMILIES:
        if name.startswith(p):
            return f
    return "other"


def ptype(arg: str) -> str:
    for prefix, t in TYPE_BY_PREFIX:
        if arg.startswith(prefix):
            return t
    return "unknown"


def load_set(path: Path) -> set[str]:
    return {l.strip() for l in path.read_text().splitlines() if l.strip()}


def main() -> int:
    fnd = json.loads((A / "fnd.json").read_text())["functions"]
    argl = json.loads((M / "arglists.json").read_text())
    vrep = json.loads((M / "verified.json").read_text())
    verified, returns_value = set(vrep["callable"]), set(vrep["non_nil"])
    headless = load_set(M / "runtime_functions.txt")
    sch = load_set(M / "runtime_functions_editor.txt")
    lay = load_set(M / "runtime_functions_layout.txt")
    rows = [l.split("|") for l in (M / "symbols.txt").read_text().splitlines() if "|" in l]
    funcs = {r[0] for r in rows if r[1] == "fn"}
    vars_ = [r[0] for r in rows if r[1] == "var"]
    unb = [r[0] for r in rows if r[1] == "unbound"]
    oa = json.loads((M / "oa_properties.json").read_text())

    api, ops = {}, {}
    for name in sorted(funcs):
        e = fnd.get(name, {})
        args = [{"name": a["name"], "type": ptype(a["name"]),
                 "optional": bool(a.get("optional"))} for a in e.get("args", [])]
        al = [a for a in argl.get(name, {}).get("args", [])
              if a not in ("@optional", "@rest", "@key")]
        if not args and al:
            args = [{"name": a, "type": ptype(a), "optional": False} for a in al]
        ctx = [c for c, s in (("headless", headless), ("schematic-window", sch),
                              ("layout-window", lay)) if name in s]
        evid = ["DISCOVERED"]
        if e: evid.append("DOCUMENTED")
        if ctx: evid.append("CALLABLE")
        if name in verified: evid.append("EXECUTED")
        if name in returns_value: evid.append("READBACK_VERIFIED")
        api[name] = {"id": f"skill:{name}", "name": name, "family": family(name),
                     "documented": bool(e), "manuals": e.get("manuals", []),
                     "tools": e.get("tools", []),
                     "syntax": e.get("syntax", "").replace("\n", " ")[:240],
                     "description": e.get("desc", "")[:200],
                     "params": args[:12],
                     "param_source": "fnd" if e.get("args") else ("arglist" if al else "unknown"),
                     "contexts": ctx, "evidence": evid,
                     "confidence": "high" if e and args else ("medium" if e or al else "low")}

    for name in sorted(verified):
        meta = api.get(name, {})
        ops[name] = {"operation": f"op:{name}", "skill": name,
                     "risk": "read" if re.search(r"(Get|Is|Find|Check|Query)", name)
                             else "write",
                     "verified": True, "family": meta.get("family", family(name)),
                     "params": meta.get("params", [])}

    fam = Counter(v["family"] for v in api.values())
    loaded = Counter(v["family"] for v in api.values() if v["contexts"])
    version = {"IC2310": {
        "documented": sum(1 for v in api.values() if v["documented"]),
        "loaded_headless": sum(1 for v in api.values() if "headless" in v["contexts"]),
        "loaded_schematic": sum(1 for v in api.values() if "schematic-window" in v["contexts"]),
        "loaded_layout": sum(1 for v in api.values() if "layout-window" in v["contexts"]),
        "executed": len(verified), "readback_verified": len(returns_value),
        "families": dict(fam), "loaded_by_family": dict(loaded)}}
    pdk = {"pdk": "tsmcN65 (TSMC065_1P9M)",
           "note": "未对 PDK 私有 SKILL 做扩展；PDK 能力经 tech/CDF 数据面体现",
           "tech_api": sorted(n for n in api if n.startswith("tech"))[:20],
           "cdf_api": sorted(n for n in api if n.startswith("cdf"))[:20],
           "pdk_specific_symbols": []}
    policy = {"default": "deny",
              "allow": "operation_registry 中 verified=true",
              "risk_levels": {"read": "允许自动执行",
                              "write": "需隔离库 + 前后快照",
                              "destructive": "禁止"},
              "blocked_patterns": ["delete", "destroy", "exit", "quit", "unlink"],
              "no_raw_skill": True, "private_prefix_denied": ["_"],
              "requires_snapshot": ["write", "destructive"]}

    R.mkdir(parents=True, exist_ok=True)
    (R / "api_registry.json").write_text(json.dumps(api, ensure_ascii=False))
    (R / "symbol_vocabulary.json").write_text(json.dumps(
        {"variables": sorted(vars_), "unbound": sorted(unb), "oa": oa},
        ensure_ascii=False))
    (R / "operation_registry.json").write_text(json.dumps(ops, ensure_ascii=False))
    (R / "version_capability_matrix.json").write_text(json.dumps(version, indent=1, ensure_ascii=False))
    (R / "pdk_capability_matrix.json").write_text(json.dumps(pdk, indent=1, ensure_ascii=False))
    (R / "execution_policy.json").write_text(json.dumps(policy, indent=1, ensure_ascii=False))
    print("api", len(api), "| ops", len(ops), "| vars", len(vars_), "| unbound", len(unb))
    print("families:", fam.most_common(6))
    print("param_source:", Counter(v["param_source"] for v in api.values()))
    print("evidence:", Counter(tuple(v["evidence"]) for v in api.values()).most_common(4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
