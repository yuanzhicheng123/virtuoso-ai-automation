"""conformance —— 一致性/保真度套件（回答"能一比一到什么程度"）。

思路：把"1:1"拆成可测量的轴，每个轴跑一组带**参考值**的用例：

    simulation  数值：自研引擎 vs 解析解 / vs Spectre
    netlist     语义：网表读入-写出是否保持结构
    layout      几何：GDS 往返、pcell 指标、多边形布尔
    verification 验证：LVS 能匹配改名电路、能抓错连；DRC 能抓违规
    automation  自动化：SKILL 建图、OCEAN 扫描、ADE 状态导入
    data        数据：CDF 参数、单位、层映射

跑法：
    python3 -m conformance.run                 # 离线（不需要 spectre）
    VLIGHT_HOST=eda-220 python3 -m conformance.run   # 加跑 vs Spectre 的用例
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import (ade, cdf, connect, drc, extract, gdsio, lvs, measure,  # noqa: E402
                    pcell, polybool, skill, spice, spicein)
from vlight.sim import Simulator  # noqa: E402
from vlight.tb import Testbench, ac, dc  # noqa: E402
from vlight.vdb import Instance, Layout, Library, Schematic, Shape  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
HOST = os.environ.get("VLIGHT_HOST")
SPECTRE = os.environ.get("VLIGHT_SPECTRE", "spectre")


@dataclass
class Case:
    id: str
    axis: str
    fn: object
    ref: str = ""
    tol: str = ""
    offline: bool = True
    result: dict = field(default_factory=dict)


def _ckt(text: str) -> spice.Circuit:
    return spice.Circuit.from_netlist(spicein.parse(text))


# --------------------------------------------------------------------------- #
# simulation：数值
# --------------------------------------------------------------------------- #

def c_rc_dc():
    r = spice.dc_solve(_ckt("""
simulator lang=spectre
V1 (in 0) vsource dc=3
R1 (in out) resistor r=1k
R2 (out 0) resistor r=2k
"""))
    return abs(r.v("out") - 2.0) < 1e-6, f"V(out)={r.v('out'):.9f} 期望 2.0"


def c_rc_ac():
    c = _ckt("""
simulator lang=spectre
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1p
""")
    fc = 1 / (2 * math.pi * 1e3 * 1e-12)
    f = np.array([fc / 10, fc, fc * 10])
    H = spice.ac_solve(c, f)["out"]
    err = float(np.max(np.abs(np.abs(H) - 1 / np.sqrt(1 + (f / fc) ** 2))))
    return err < 1e-6, f"|H| 最大绝对误差 {err:.2e}"


def c_rc_tran():
    c = _ckt("""
simulator lang=spectre
V1 (in 0) vsource dc=0 type=pulse v1=0 v2=1 delay=0 rise=1p width=10n period=20n
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1p
""")
    res = spice.tran_solve(c, 5e-9, 1e-12, maxstep=1e-12)
    t, v = res["t"], res["nodes"]["out"]
    ana = 1 - np.exp(-t / 1e-9)
    m = t > 20e-12
    err = float(np.max(np.abs(v[m] - ana[m])))
    return err < 3e-3, f"阶跃最大误差 {err:.2e} V"


def c_diode():
    c = _ckt("""
simulator lang=spectre
V1 (in 0) vsource dc=1.2
R1 (in out) resistor r=1k
D1 (out 0) diode is=1e-14 n=1
""")
    r = spice.dc_solve(c)
    vt, isat = 1.380649e-23 * 300.15 / 1.602176634e-19, 1e-14
    lo, hi = 0.0, 1.2
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if (1.2 - mid) / 1e3 - isat * (math.exp(mid / vt) - 1) > 0 else (lo, mid)
    err = abs(r.v("out") - (lo + hi) / 2)
    return err < 1e-5, f"Vd 误差 {err:.2e} V"


def c_noise():
    c = _ckt("""
simulator lang=spectre
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1p
""")
    f = np.logspace(1, 12, 400)
    n = spice.noise_solve(c, f, "out")
    trapz = getattr(np, "trapezoid", None) or np.trapz
    tot = float(trapz(n["total"], f))
    kt_c = 1.380649e-23 * 300.15 / 1e-12
    return abs(tot / kt_c - 1) < 0.02, f"积分噪声/kT·C = {tot / kt_c:.4f}"


def c_inverter():
    def inv(vin):
        return _ckt(f"""
simulator lang=spectre
VDD (vdd 0) vsource dc=1.2
VIN (in 0) vsource dc={vin}
MP (out in vdd vdd) pmos w=4u l=0.1u kp=100e-6 vth0=-0.4
MN (out in 0 0) nmos w=2u l=0.1u kp=200e-6 vth0=0.4
""")
    hi = spice.dc_solve(inv(0)).v("out")
    lo = spice.dc_solve(inv(1.2)).v("out")
    ok = hi > 1.19 and lo < 0.01
    return ok, f"Vout(0)={hi:.3f}, Vout(1.2)={lo:.3f}"


def c_ring():
    c = _ckt("""
simulator lang=spectre
VDD (vdd 0) vsource dc=1.2
MN1 (n1 n3 0 0) nmos w=2u l=0.1u kp=200e-6 vth0=0.4
MP1 (n1 n3 vdd vdd) pmos w=4u l=0.1u kp=100e-6 vth0=-0.4
MN2 (n2 n1 0 0) nmos w=2u l=0.1u kp=200e-6 vth0=0.4
MP2 (n2 n1 vdd vdd) pmos w=4u l=0.1u kp=100e-6 vth0=-0.4
MN3 (n3 n2 0 0) nmos w=2u l=0.1u kp=200e-6 vth0=0.4
MP3 (n3 n2 vdd vdd) pmos w=4u l=0.1u kp=100e-6 vth0=-0.4
C1 (n1 0) capacitor c=20f
C2 (n2 0) capacitor c=20f
C3 (n3 0) capacitor c=20f
""")
    d = spice.dc_solve(c)
    x0 = d.x.copy()
    for n, dv in (("n1", -0.3), ("n2", 0.3), ("n3", -0.3)):
        x0[c.node_index[n] - 1] += dv
    r = spice.tran_solve(c, 20e-9, 0.002e-9, maxstep=0.002e-9, uic=x0)
    f = measure.frequency(r["t"], r["nodes"]["n1"])
    return 5e9 < f < 20e9, f"振荡 {f/1e9:.2f} GHz"


def c_vs_spectre():
    """同一 RC：自研引擎 vs Spectre 的 AC 幅相。"""
    s = skill.Session()
    d = s.design
    lib = d.add(Library("cmp", ROOT / "out" / "_cmp"))
    sch = lib.new_cell("rc", [("in", "input"), ("out", "output"), ("gnd", "inputOutput")])
    sch.add_inst(Instance("R1", "vlightLib", "res", params={"r": "1k"}))
    sch.add_inst(Instance("C1", "vlightLib", "cap", params={"c": "1p"}))
    sch.connect("R1", "PLUS", "in")
    sch.connect("R1", "MINUS", "out")
    sch.connect("C1", "PLUS", "out")
    sch.connect("C1", "MINUS", "gnd")
    tb = Testbench(d, "cmp/rc", flatten=True)
    tb.element("V1 (in 0) vsource dc=0 mag=1", "VGND (gnd 0) vsource dc=0")
    tb.analysis(ac("ac1", start=1e7, stop=1e9, dec=6))
    ours = spice.Engine(d).run(tb).plot("ac1")
    ref = Simulator(spectre=SPECTRE, host=HOST, workdir=str(ROOT / "out" / "_cmp_sim")).run(tb).plot("ac1")
    f = ours.x
    rr = np.interp(f, ref.x, np.real(ref.v("out")))
    ri = np.interp(f, ref.x, np.imag(ref.v("out")))
    mag_err = float(np.max(np.abs(np.abs(ours.v("out")) - np.hypot(rr, ri)) /
                           np.maximum(np.hypot(rr, ri), 1e-12)))
    ph_err = float(np.max(np.abs(np.angle(ours.v("out")) - np.angle(rr + 1j * ri))))
    return (mag_err < 0.02 and ph_err < 0.02), f"幅频 {mag_err:.2%}, 相位 {ph_err:.3f} rad"


# --------------------------------------------------------------------------- #
# netlist
# --------------------------------------------------------------------------- #

def c_vco_roundtrip():
    nl = spicein.read(FIX / "vco_core.scs")
    again = spicein.parse(nl.to_scs())
    ok = (again.stats()["elements"] == nl.stats()["elements"]
          and again.stats()["analyses"] == nl.stats()["analyses"]
          and again.parameters == nl.parameters)
    return ok, f"{nl.stats()['elements']} 器件往返一致"


def c_subckt_to_design():
    from vlight.netlist import Netlister
    nl = spicein.parse("""
simulator lang=spectre
subckt inv (a y vdd vss)
  M1 (y a vss vss) nch w=1u l=60n
  M2 (y a vdd vdd) pch w=2u l=60n
ends inv
""")
    d = nl.to_design("imp")
    text = Netlister(d, allow_unknown=True).subckt("imp/inv")
    ok = "M1 (y a vss vss) nch w=1u l=60n" in text
    return ok, text.splitlines()[1].strip()[:48]


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

def c_gds_roundtrip():
    lay, met = pcell.make("spiral_ind", cell="IND", nr=2, rad=60e-6, w=10e-6, spacing=3e-6)
    lib = Library("t", ROOT / "out" / "_gds")
    lib.cell("IND", create=True).layouts["layout"] = lay
    tech = __import__("vlight.skilldump", fromlist=["import_tech"]).import_tech(
        ROOT / "recon" / "tsmcN65.layermap")
    p = gdsio.write_gds(ROOT / "out" / "conf_ind.gds", lib, "IND", tech)
    info = gdsio.verify(p)
    n = info["cells"].get("IND", 0)
    ok = n == len(lay.shapes) and info["klayout"].get("cells", 0) == 1
    return ok, f"{n} 图形读回，KLayout 校验 {info['klayout'].get('shapes')} shapes"


def c_polybool():
    a = [(0, 0), (10, 0), (10, 10), (0, 10)]
    b = [(5, 0), (15, 0), (15, 10), (5, 10)]
    ok = (abs(polybool.union_area([a, b]) - 150) < 1e-9
          and abs(polybool.intersection_area(a, b) - 50) < 1e-9)
    return ok, "并 150 / 交 50"


def c_extract():
    L = Layout("w")
    L.add("M1", [(0, 0), (100e-6, 0), (100e-6, 1e-6), (0, 1e-6)])
    L.shapes.append(Shape(kind="label", layer="M1", text="IN", points=[(0.5e-6, 0.5e-6)]))
    nl = extract.extract(L, extract.ExtractRules.demo())
    s = extract.summary(nl)
    ok = abs(s["R_total_ohm"] - 10.0) < 1e-9
    return ok, f"R={s['R_total_ohm']:.6f} Ω（0.1Ω/sq × 100/1）"


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

BASE = """
simulator lang=spectre
subckt amp (in out vdd vss)
  M1 (n1 in vss vss) nmos w=2u l=0.1u
  M2 (out n1 vss vss) nmos w=2u l=0.1u
  R1 (out vdd) resistor r=10k
ends amp
"""


def c_lvs_match():
    renamed = (BASE.replace("M1", "X9").replace("M2", "X8").replace("R1", "X7")
               .replace("(n1 in vss vss)", "(q a g g)")
               .replace("(out n1 vss vss)", "(b q g g)")
               .replace("(out vdd)", "(b p)")
               .replace("(in out vdd vss)", "(a b p g)"))
    rep = lvs.compare(lvs.Graph.from_netlist(spicein.parse(BASE)),
                      lvs.Graph.from_netlist(spicein.parse(renamed)))
    return rep.ok, f"{len(rep.matched_devices)} 器件配对"


def c_lvs_break():
    bad = BASE.replace("M2 (out n1 vss vss)", "M2 (out in vss vss)")
    rep = lvs.compare(lvs.Graph.from_netlist(spicein.parse(BASE)),
                      lvs.Graph.from_netlist(spicein.parse(bad)))
    return (not rep.ok), "错连被检出"


def c_drc():
    L = Layout("t")
    L.add("M1", [(0, 0), (0.05e-6, 0), (0.05e-6, 10e-6), (0, 10e-6)])
    L.add("M1", [(10e-6, 0), (20e-6, 0), (20e-6, 10e-6), (10e-6, 10e-6)])
    L.add("M1", [(20.1e-6, 0), (30e-6, 0), (30e-6, 10e-6), (20.1e-6, 10e-6)])
    deck = drc.Deck()
    deck.add("M1", min_width=0.1e-6, min_space=0.2e-6, min_area=0.05e-12)
    kinds = {v.kind for v in drc.check(L, deck)}
    return {"min_width", "min_space"} <= kinds, f"检出 {sorted(kinds)}"


def c_short():
    L = Layout("t")
    L.add("M1", [(0, 0), (10e-6, 0), (10e-6, 10e-6), (0, 10e-6)])
    L.add("M1", [(10e-6, 0), (20e-6, 0), (20e-6, 10e-6), (10e-6, 10e-6)])
    L.shapes.append(Shape(kind="label", layer="M1", text="A", points=[(5e-6, 5e-6)]))
    L.shapes.append(Shape(kind="label", layer="M1", text="B", points=[(15e-6, 5e-6)]))
    conn = connect.extract(L)
    return any("短路" in w for w in conn.warnings), "两个 label 落同一节点即报"


# --------------------------------------------------------------------------- #
# automation
# --------------------------------------------------------------------------- #

def c_skill_build():
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    sch = s.design.schematic("demolib/divider")
    ok = sch.net("out").terms == [("R1", "MINUS"), ("R2", "PLUS")]
    return ok, f"{len(sch.insts)} 实例 / {len(sch.nets)} 网络"


def c_ocean():
    from vlight import ocean as ocn
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    oc = ocn.Ocean(s.design, Simulator(engine="vlight"))
    oc.run_script("""
design("demolib" "divider_var" "schematic")
element("V1 (in 0) vsource dc=3")
element("VGND (gnd 0) vsource dc=0")
analysis('dc)
foreach(r '("1k" "2k" "3k")
  desVar("r2" r) run() selectResult('dc) ocnPrint(v("/out")) )
""")
    vals = [float(p.split("last=")[1].split()[0]) for p in oc.printed]
    return vals == [1.5, 2.0, 2.25], f"{vals}"


def c_ade_state():
    st = Path("/home/lihong/projects/RFIC-GPT/recon/wizard/RFIC-GPT_Wizard/"
              "RFIC-GPT_Wizard_Active/CSPA/CSPA_DM_lvt4t/webdisplay_state1")
    if not st.is_dir():
        return None, "样本缺失（跳过）"
    s = ade.import_state_dir(st)
    ok = [a.kind for a in s.analyses] == ["dc", "sp"] and len(s.variables) == 13
    return ok, f"分析 {[a.kind for a in s.analyses]}, 变量 {len(s.variables)}, 输出 {len(s.outputs)}"


def c_cdf():
    db = cdf.CDFdb()
    db.from_dump_text((FIX / "tsmcN65_LNA2G4.dump.txt").read_text())
    top = __import__("vlight.skilldump", fromlist=["read_dump"]).read_dump(
        FIX / "tsmcN65_LNA2G4.dump.txt").schematic("tsmcN65_LNA2G4/lna_2g4_top")
    inst = top.inst("Rmatch")
    inst.params = {"l": "3.21u"}
    from vlight.netlist import DeviceMap, Netlister
    line = Netlister(__import__("vlight.skilldump", fromlist=["read_dump"]).read_dump(
        FIX / "tsmcN65_LNA2G4.dump.txt"), DeviceMap(), cdf=db).instance_line(
        top.inst("Rload"), top)
    ok = "w=10u" in line
    return ok, line


# --------------------------------------------------------------------------- #
# 用例表与执行
# --------------------------------------------------------------------------- #

CASES = [
    Case("sim.rc_dc", "simulation", c_rc_dc, "解析解 2.0 V", "<1e-6"),
    Case("sim.rc_ac", "simulation", c_rc_ac, "解析 1/sqrt(1+(f/fc)²)", "<1e-6"),
    Case("sim.rc_tran", "simulation", c_rc_tran, "解析 1-exp(-t/τ)", "<3e-3 V"),
    Case("sim.diode_dc", "simulation", c_diode, "二分法解", "<1e-5 V"),
    Case("sim.noise", "simulation", c_noise, "kT/C", "<2%"),
    Case("sim.inverter", "simulation", c_inverter, "CMOS 轨到轨", "rail"),
    Case("sim.ring_osc", "simulation", c_ring, "5-20 GHz", "range"),
    Case("sim.vs_spectre", "simulation", c_vs_spectre, "Spectre", "<2%/0.02rad",
         offline=False),
    Case("netlist.vco_roundtrip", "netlist", c_vco_roundtrip, "结构不变", "exact"),
    Case("netlist.subckt_to_design", "netlist", c_subckt_to_design, "回写网表", "exact"),
    Case("layout.gds_roundtrip", "layout", c_gds_roundtrip, "KLayout", "count"),
    Case("layout.polybool", "layout", c_polybool, "解析面积", "<1e-9"),
    Case("extract.rc", "layout", c_extract, "R=rsh·L/W", "<1e-9"),
    Case("verify.lvs_match", "verification", c_lvs_match, "改名后仍匹配", "bool"),
    Case("verify.lvs_break", "verification", c_lvs_break, "错连必须检出", "bool"),
    Case("verify.drc", "verification", c_drc, "规则命中", "bool"),
    Case("verify.short", "verification", c_short, "短路告警", "bool"),
    Case("auto.skill", "automation", c_skill_build, "建图+连线", "bool"),
    Case("auto.ocean", "automation", c_ocean, "1.5/2.0/2.25", "exact"),
    Case("data.ade_state", "automation", c_ade_state, "真实 .state", "bool"),
    Case("data.cdf", "data", c_cdf, "缺省值补齐", "bool"),
]


def run() -> dict:
    rows = []
    for c in CASES:
        if not c.offline and not HOST:
            rows.append({"id": c.id, "axis": c.axis, "status": "SKIP",
                         "detail": "需要 VLIGHT_HOST"})
            continue
        try:
            ok, detail = c.fn()
        except Exception as exc:                       # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        rows.append({"id": c.id, "axis": c.axis, "status": status,
                     "detail": detail, "ref": c.ref, "tol": c.tol})
    axes = {}
    for r in rows:
        a = axes.setdefault(r["axis"], {"pass": 0, "fail": 0, "skip": 0})
        a[{"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}[r["status"]]] += 1
    for a in axes.values():
        total = a["pass"] + a["fail"]
        a["score"] = round(a["pass"] / total, 3) if total else None
    overall = sum(a["pass"] for a in axes.values())
    total = sum(a["pass"] + a["fail"] for a in axes.values())
    return {"cases": rows, "axes": axes,
            "overall": {"pass": overall, "total": total,
                        "score": round(overall / total, 3) if total else None},
            "host": HOST}


def to_markdown(rep: dict) -> str:
    lines = ["# 一致性套件报告", "",
             f"- 环境：{'离线' if not rep['host'] else '含 Spectre 交叉验证（' + rep['host'] + '）'}",
             f"- 总体：{rep['overall']['pass']}/{rep['overall']['total']} = "
             f"{rep['overall']['score']:.1%}", ""]
    lines.append("| 轴 | 通过 | 失败 | 跳过 | 得分 |")
    lines.append("|---|---|---|---|---|")
    for axis, a in rep["axes"].items():
        score = "" if a["score"] is None else f"{a['score']:.0%}"
        lines.append(f"| {axis} | {a['pass']} | {a['fail']} | {a['skip']} | {score} |")
    lines.append("")
    lines.append("| 用例 | 轴 | 结果 | 说明 |")
    lines.append("|---|---|---|---|")
    for r in rep["cases"]:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭"}[r["status"]]
        lines.append(f"| {r['id']} | {r['axis']} | {icon} | {r['detail']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    rep = run()
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "conformance.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    md = to_markdown(rep)
    (out / "conformance.md").write_text(md)
    print(md)
    return 0 if rep["overall"]["score"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
