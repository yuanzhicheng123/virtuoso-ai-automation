"""sim_align —— 前仿阶段与 Spectre 的逐点对齐。

只比**同一份网表、同一个模型、同一种分析**下的数值，不比 UI/流程。
模型用 Spectre 内置 `mos1`（level=1），这样比的是**引擎**而不是器件模型。

    python3 -m sim_align.run                 # 需要 VLIGHT_HOST（有 spectre）
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import psf, spice, spicein           # noqa: E402
from vlight.sim import Simulator                # noqa: E402

HOST = os.environ.get("VLIGHT_HOST")
SPECTRE = os.environ.get("VLIGHT_SPECTRE", "spectre")
OUT = ROOT / "out" / "sim_align"
OUT.mkdir(parents=True, exist_ok=True)

TOL = {"dc": 1e-4, "ac_mag_db": 0.05, "ac_phase_deg": 0.2,
       "tran_rel": 1e-3, "freq_rel": 0.02, "noise_rel": 0.02}


@dataclass
class Case:
    id: str
    netlist: str
    analysis: str          # spectre 分析语句
    kind: str              # dc / ac / tran
    signal: str = "out"
    xspec: tuple = ()
    tol: str = "dc"
    note: str = ""


def _ckt(case: Case) -> spice.Circuit:
    return spice.Circuit.from_netlist(spicein.parse(case.netlist))


def _spectre(case: Case) -> dict:
    sim = Simulator(spectre=SPECTRE, host=HOST, workdir=str(OUT / "_spec"))
    text = case.netlist.rstrip() + "\n" + case.analysis + "\n"
    res = sim._run_remote(text, f"align_{case.id}")
    p = next(iter(res.plots.values()))
    return {"x": np.asarray(p.x, dtype=float),
            "y": np.asarray(p.y.get(case.signal, p.v(case.signal)), dtype=complex)}


def _ours(case: Case) -> dict:
    ckt = _ckt(case)
    a = case.analysis.split()
    kind = a[1]
    args = dict(kv.split("=", 1) for kv in a[2:] if "=" in kv)
    if kind == "dc":
        r = spice.dc_solve(ckt)
        return {"x": np.zeros(1), "y": np.array([r.v(case.signal) + 0j])}
    if kind == "ac":
        f0 = _num(args.get("start", "1k"))
        f1 = _num(args.get("stop", "1G"))
        dec = int(float(args.get("dec", 10)))
        n = max(int(dec * math.log10(f1 / f0)) + 1, 2)
        f = np.logspace(math.log10(f0), math.log10(f1), n)
        y = spice.ac_solve(ckt, f)[case.signal]
        return {"x": f, "y": y}
    if kind == "tran":
        tstop = _num(args.get("stop", "1u"))
        step = _num(args.get("step", args.get("maxstep", tstop / 500)))
        res = spice.tran_solve(ckt, tstop, step, maxstep=step)
        return {"x": res["t"], "y": res["nodes"][case.signal] + 0j}
    raise ValueError(kind)


def _num(s: str) -> float:
    from vlight.vdb import num
    return num(s) or 0.0


def compare(case: Case) -> dict:
    ours = _ours(case)
    ref = _spectre(case)
    if case.kind in ("ac", "tran"):
        y_ref = np.interp(ours["x"], ref["x"], np.real(ref["y"])) + \
            1j * np.interp(ours["x"], ref["x"], np.imag(ref["y"]))
    else:
        y_ref = np.real(ref["y"])
    y_our = ours["y"]
    if case.tol == "ac_mag_db":
        mag_ref = np.abs(y_ref)
        mask = mag_ref > max(np.max(mag_ref) * 0.1, 1e-6)   # 只比 −20dB 以内的有效带
        err = float(np.max(np.abs(20 * np.log10(np.abs(y_our[mask]) + 1e-300) -
                                  20 * np.log10(mag_ref[mask] + 1e-300))))
        detail = f"最大幅频误差 {err:.4f} dB（限 {TOL[case.tol]}）"
    elif case.tol == "ac_phase_deg":
        err = float(np.max(np.abs(np.degrees(np.angle(y_our)) -
                                  np.degrees(np.angle(y_ref)))))
        detail = f"最大相位误差 {err:.4f} deg"
    elif case.tol == "tran_rel":
        scale = float(np.max(np.abs(y_ref))) or 1.0
        err = float(np.max(np.abs(y_our - y_ref))) / scale
        detail = f"最大相对误差 {err:.2e}（归一化到摆幅 {scale:.3f}）"
    elif case.tol == "freq_rel":
        from vlight import measure
        f1 = measure.frequency(ours["x"], np.real(ours["y"]))
        f2 = measure.frequency(ref["x"], np.real(ref["y"]))
        err = abs(f1 - f2) / max(f2, 1.0)
        detail = f"振荡频率 本实现 {f1/1e9:.3f} GHz / Spectre {f2/1e9:.3f} GHz"
    else:                                            # dc
        scale = max(float(np.max(np.abs(y_ref))), 1e-3)
        err = float(np.max(np.abs(y_our - y_ref))) / scale
        detail = (f"V({case.signal}) 本实现 {np.real(y_our[0]):.6f} / "
                  f"Spectre {float(np.real(y_ref[0])):.6f}")
    ok = err <= TOL[case.tol]
    return {"id": case.id, "kind": case.kind, "tol": case.tol, "err": err,
            "ok": ok, "detail": detail, "note": case.note}


MOS_MODEL = ("model myn mos1 type=n vto=0.4 kp=200u gamma=0.4 phi=0.7 lambda=0.05 "
             "cgso=0 cgdo=0 cgbo=0 cj=0 cjsw=0")
MOS_MODEL_P = ("model myp mos1 type=p vto=-0.4 kp=100u gamma=0.4 phi=0.7 lambda=0.05 "
               "cgso=0 cgdo=0 cgbo=0 cj=0 cjsw=0")
INV = f"""
simulator lang=spectre
global 0
{MOS_MODEL}
{MOS_MODEL_P}
VDD (vdd 0) vsource dc=1.2
VIN (in 0) vsource dc=0.6 mag=1
MP (out in vdd vdd) myp w=4u l=0.1u
MN (out in 0 0) myn w=2u l=0.1u
CL (out 0) capacitor c=20f
"""

CASES = [
    Case("dc.rc", """
simulator lang=spectre
global 0
V1 (in 0) vsource dc=3
R1 (in out) resistor r=1k
R2 (out 0) resistor r=2k
""", "dc1 dc", "dc", "out", tol="dc", note="分压"),
    Case("ac.rc", """
simulator lang=spectre
global 0
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1p
""", "ac1 ac start=1k stop=1G dec=10", "ac", "out", tol="ac_mag_db",
         note="RC 低通幅频"),
    Case("ac.rc_phase", """
simulator lang=spectre
global 0
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1p
""", "ac1 ac start=1k stop=1G dec=10", "ac", "out", tol="ac_phase_deg",
         note="RC 相位"),
    Case("tran.rc", """
simulator lang=spectre
global 0
V1 (in 0) vsource dc=0 type=pulse v1=0 v2=1 delay=0 rise=1p width=10n period=20n
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1p
""", "tr1 tran stop=10n maxstep=1p", "tran", "out", tol="tran_rel",
         note="RC 阶跃"),
    Case("dc.diode", """
simulator lang=spectre
global 0
model myd diode is=1e-14 n=1
V1 (in 0) vsource dc=1.2
R1 (in out) resistor r=1k
D1 (out 0) myd
""", "dc1 dc", "dc", "out", tol="dc", note="二极管"),
    Case("dc.mos1", INV, "dc1 dc", "dc", "out", tol="dc", note="MOS1 反相器工作点"),
    Case("ac.mos1", INV, "ac1 ac start=1k stop=1G dec=10", "ac", "out",
         tol="ac_mag_db", note="MOS1 反相器幅频"),
    Case("tran.mos1", INV.replace("VIN (in 0) vsource dc=0.6 mag=1",
                                  "VIN (in 0) vsource dc=0 type=pulse v1=0 v2=1.2 "
                                  "delay=0.5n rise=0.02n width=5n period=10n"),
         "tr1 tran stop=3n maxstep=1p", "tran", "out", tol="tran_rel",
         note="MOS1 反相器阶跃"),
    Case("tran.ring", f"""
simulator lang=spectre
global 0
{MOS_MODEL}
{MOS_MODEL_P}
VDD (vdd 0) vsource dc=1.2
VK (kick 0) vsource dc=0 type=pulse v1=0 v2=1.2 delay=0 rise=1p width=100p period=100n
RK (kick n1) resistor r=10k
MN1 (n1 n3 0 0) myn w=2u l=0.1u
MP1 (n1 n3 vdd vdd) myp w=4u l=0.1u
MN2 (n2 n1 0 0) myn w=2u l=0.1u
MP2 (n2 n1 vdd vdd) myp w=4u l=0.1u
MN3 (n3 n2 0 0) myn w=2u l=0.1u
MP3 (n3 n2 vdd vdd) myp w=4u l=0.1u
C1 (n1 0) capacitor c=20f
C2 (n2 0) capacitor c=20f
C3 (n3 0) capacitor c=20f
""", "tr1 tran stop=6n maxstep=5p", "tran", "n1", tol="freq_rel",
         note="3 级环振频率"),
]


def run() -> dict:
    rows = []
    for c in CASES:
        try:
            rows.append(compare(c))
        except Exception as exc:                       # noqa: BLE001
            rows.append({"id": c.id, "kind": c.kind, "tol": c.tol,
                         "err": float("inf"), "ok": False,
                         "detail": f"{type(exc).__name__}: {exc}",
                         "note": c.note})
    passed = sum(1 for r in rows if r["ok"])
    return {"cases": rows, "summary": {"pass": passed, "total": len(rows),
                                       "score": round(passed / len(rows), 3)},
            "tolerances": TOL, "host": HOST}


def to_md(rep: dict) -> str:
    lines = ["# 前仿对齐报告（vlight SPICE vs Spectre）", "",
             f"- 环境：{rep['host']}",
             f"- 通过：{rep['summary']['pass']}/{rep['summary']['total']} = "
             f"{rep['summary']['score']:.0%}", "",
             "| 用例 | 分析 | 判据 | 误差 | 结果 | 说明 |", "|---|---|---|---|---|---|"]
    for r in rep["cases"]:
        icon = "✅" if r["ok"] else "❌"
        lim = rep["tolerances"].get(r["tol"])
        lines.append(f"| {r['id']} | {r['kind']} | {r['tol']}≤{lim} | "
                     f"{r['err']:.3g} | {icon} | {r['detail']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    if not HOST:
        print("需要 VLIGHT_HOST（有 spectre 的机器）")
        return 2
    rep = run()
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    md = to_md(rep)
    (OUT / "report.md").write_text(md)
    print(md)
    return 0 if rep["summary"]["score"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
