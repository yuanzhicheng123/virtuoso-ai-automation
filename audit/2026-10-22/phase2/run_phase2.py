"""M1（网表→IR→语义 diff）+ M2（Spectre 10 次回归 + PSF）实跑。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from vskill.phase2 import EvidenceManifest, SpectreRunner, diff, from_oa, from_scs

A = ROOT / "audit" / "2026-10-22"
OUT = A / "phase2" / "runs"


def m1() -> dict:
    net_ir = from_scs(A / "m1" / "input.scs")
    oa_ir = from_oa(
        instances=[{"name": "M0", "master": "nch",
                    "terminals": ["D", "G", "S", "B"],
                    "props": {"w": "1u", "l": "60n"}}],
        nets=["D", "G", "S", "B"])
    rep = diff(oa_ir, net_ir)
    rep["oa_ir"] = oa_ir.json()
    rep["netlist_ir"] = net_ir.json()
    return rep


def m2(runs: int = 10) -> dict:
    runner = SpectreRunner(OUT, timeout=120, host="eda-220")
    results, values = [], []
    for i in range(runs):
        r = runner.run(A / "m2" / "tb.scs", f"m2_{i:02d}")
        results.append(r.json())
        if r.status == "OK" and "op1.dc" in r.results:
            values.append(r.results["op1.dc"]["values"])
    stable = None
    if len(values) >= 2:
        keys = set(values[0])
        stable = all(abs(v[k] - values[0][k]) < 1e-12
                     for v in values[1:] for k in keys if k in v)
    return {"runs": results, "stability": stable,
            "n_ok": sum(1 for r in results if r["status"] == "OK"),
            "values_first": values[0] if values else None}


def negatives() -> list[dict]:
    runner = SpectreRunner(OUT, timeout=60, host="eda-220")
    cases = {}
    base = (A / "m2" / "tb.scs").read_text()
    cases["missing_model"] = base.replace('section=tt', 'section=no_such_section')
    cases["bad_param"] = base.replace("w=wn l=ln", "w=0 l=ln")
    cases["dangling_net"] = base.replace("M0 (d g s b)", "M0 (d g s b")  # 语法破坏
    out = []
    for name, text in cases.items():
        p = OUT / f"neg_{name}.scs"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        r = runner.run(p, f"neg_{name}")
        out.append({"case": name, "status": r.status,
                    "returncode": r.returncode,
                    "tail": r.stdout_tail[-200:]})
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ev = EvidenceManifest(OUT, "phase2-m1-m2")
    m1rep = m1()
    (OUT / "m1_diff.json").write_text(json.dumps(m1rep, ensure_ascii=False, indent=1))
    ev.add("netlist", A / "m1" / "input.scs", hash16=m1rep["hash_b"])
    ev.add("diff", OUT / "m1_diff.json", ok=m1rep["ok"],
           issues=len(m1rep["issues"]))
    m2rep = m2(10)
    (OUT / "m2_regression.json").write_text(json.dumps(m2rep, ensure_ascii=False, indent=1))
    ev.add("spectre_runs", OUT / "m2_regression.json",
           n_ok=m2rep["n_ok"], stable=m2rep["stability"])
    neg = negatives()
    (OUT / "negatives.json").write_text(json.dumps(neg, ensure_ascii=False, indent=1))
    ev.add("negatives", OUT / "negatives.json",
           rejected=sum(1 for n in neg if n["status"] != "OK"))
    manifest = ev.save()
    print("M1 ok:", m1rep["ok"], "issues:", [i["kind"] for i in m1rep["issues"]])
    print("M2 ok:", m2rep["n_ok"], "/10 stable:", m2rep["stability"])
    print("negatives:", [(n["case"], n["status"]) for n in neg])
    print("manifest:", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
