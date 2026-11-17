"""Phase 1：数据审计——核对总数、去重、识别特殊符号与未确认条目（只读）。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
A = ROOT / "audit" / "2026-10-22"
M = ROOT / "out" / "mine"

IDENT = re.compile(r"^[A-Za-z_][\w?!]*$")


def load_lines(p: Path) -> list[str]:
    return [l.strip() for l in p.read_text(errors="replace").splitlines() if l.strip()]


def main() -> int:
    sym_rows = [l.split("|") for l in load_lines(M / "symbols.txt") if "|" in l]
    fns = {r[0] for r in sym_rows if r[1] == "fn"}
    vars_ = {r[0] for r in sym_rows if r[1] == "var"}
    unb = {r[0] for r in sym_rows if r[1] == "unbound"}
    fnd = json.loads((A / "fnd.json").read_text())["functions"]
    argl = json.loads((M / "arglists.json").read_text())
    union = set(load_lines(M / "runtime_functions_union.txt"))
    oa = json.loads((M / "oa_properties.json").read_text())

    all_syms = fns | vars_ | unb
    dup_kind = {s for s in all_syms
                if sum(s in g for g in (fns, vars_, unb)) > 1}
    special = sorted(s for s in all_syms if not IDENT.match(s))
    oa_accessors = sum(len(v) for v in oa.values())

    report = {
        "counts": {
            "symbols_total": len(sym_rows),
            "functions": len(fns), "variables": len(vars_),
            "unbound": len(unb), "unique_symbols": len(all_syms),
            "fnd_documented": len(fnd), "arglists": len(argl),
            "runtime_union": len(union), "oa_classes": len(oa),
            "oa_properties": oa_accessors,
        },
        "cross_checks": {
            "functions_eq_runtime_union": len(fns) == len(union),
            "fnd_in_functions": len(set(fnd) & fns),
            "fnd_not_in_functions": len(set(fnd) - fns),
            "arglist_in_functions": len(set(argl) & fns),
            "symbols_in_two_kinds": len(dup_kind),
            "special_symbols": len(special),
            "oa_prop_in_unbound": len({p for v in oa.values() for p in v} & unb),
        },
        "special_samples": special[:20],
        "duplicate_samples": sorted(dup_kind)[:20],
        "unconfirmed": {
            "documented_but_not_callable": len([n for n in fnd
                                                if n not in fns]),
            "runtime_without_arglist": len(fns - set(argl)),
        },
        "sources": {
            "symbols": "out/mine/symbols.txt",
            "fnd": "audit/2026-10-22/fnd.json",
            "arglists": "out/mine/arglists.json",
            "runtime_union": "out/mine/runtime_functions_union.txt",
            "oa": "out/mine/oa_properties.json",
        },
    }
    out = A / "phase1" / "phase1_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report["counts"], indent=1))
    print(json.dumps(report["cross_checks"], indent=1))
    print("special:", len(special), special[:6])
    print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
