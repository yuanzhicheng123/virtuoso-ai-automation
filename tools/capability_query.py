#!/usr/bin/env python3
"""能力查询：按需求/类别/风险筛选工程操作，并解释为何不可用。"""
import argparse, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "registry", "engineering_operations.json")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirement", help="按需求号过滤，如 D03")
    ap.add_argument("--category")
    ap.add_argument("--risk")
    ap.add_argument("--explain", help="查询某 operation 的可用性原因")
    a = ap.parse_args()
    ops = json.load(open(REG))["operations"]
    if a.explain:
        o = ops.get(a.explain)
        if not o:
            print(json.dumps({"operation_id": a.explain, "status": "NOT_REGISTERED"}))
            return 1
        print(json.dumps({"operation_id": a.explain, "risk": o.get("risk"),
                          "evidence": o.get("evidence", "UNKNOWN"),
                          "preconditions": o.get("preconditions", [])}, ensure_ascii=False, indent=1))
        return 0
    sel = {k: v for k, v in ops.items()
           if (not a.requirement or a.requirement in (v.get("requirement") or []))
           and (not a.category or v.get("category") == a.category)
           and (not a.risk or v.get("risk") == a.risk)}
    print(json.dumps({"count": len(sel), "operations": sorted(sel)}, ensure_ascii=False, indent=1))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
