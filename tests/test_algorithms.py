"""算法层测试：自研 SPICE / LVS 图同构 / 多边形布尔 / 寄生提取 / 布线 / 布局 /
优化 / 测量。全部离线，且尽量和解析解对照。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import (extract, geom2d, lvs, measure, opt, place, polybool,  # noqa: E402
                    route, spice, spicein)
from vlight.vdb import Layout, Shape  # noqa: E402


# --------------------------------------------------------------------------- #
# 自研 SPICE
# --------------------------------------------------------------------------- #

def _ckt(text: str) -> spice.Circuit:
    return spice.Circuit.from_netlist(spicein.parse(text))


def test_spice_dc_divider():
    ckt = _ckt("""
    simulator lang=spectre
    V1 (in 0) vsource dc=3
    R1 (in out) resistor r=1k
    R2 (out 0) resistor r=2k
    """)
    r = spice.dc_solve(ckt)
    assert r.converged
    assert r.v("out") == pytest.approx(2.0, rel=1e-6)
    assert r.i("V1") == pytest.approx(-1e-3, rel=1e-6)


def test_spice_ac_rc_matches_analytic():
    ckt = _ckt("""
    simulator lang=spectre
    V1 (in 0) vsource dc=0 mag=1
    R1 (in out) resistor r=1k
    C1 (out 0) capacitor c=1p
    """)
    fc = 1 / (2 * math.pi * 1e3 * 1e-12)
    fs = np.array([fc / 10, fc, fc * 10])
    H = spice.ac_solve(ckt, fs)["out"]
    np.testing.assert_allclose(np.abs(H), 1 / np.sqrt(1 + (fs / fc) ** 2), rtol=1e-6)
    np.testing.assert_allclose(np.degrees(np.angle(H)), -np.degrees(np.arctan(fs / fc)),
                               atol=1e-6)


def test_spice_rlc_resonance():
    ckt = _ckt("""
    simulator lang=spectre
    V1 (in 0) vsource dc=0 mag=1
    R1 (in mid) resistor r=10
    L1 (mid out) inductor l=1u
    C1 (out 0) capacitor c=1p
    """)
    f0 = 1 / (2 * math.pi * math.sqrt(1e-6 * 1e-12))
    H = spice.ac_solve(ckt, [f0])["out"]
    I = H / (1 / (1j * 2 * math.pi * f0 * 1e-12))
    assert abs(I[0]) == pytest.approx(0.1, rel=2e-2)      # 1V / 10Ω


def test_spice_diode_matches_bisection():
    ckt = _ckt("""
    simulator lang=spectre
    V1 (in 0) vsource dc=1.2
    R1 (in out) resistor r=1k
    D1 (out 0) diode is=1e-14 n=1
    """)
    r = spice.dc_solve(ckt)
    vt, isat, R, Vs = 1.380649e-23 * 300.15 / 1.602176634e-19, 1e-14, 1e3, 1.2
    lo, hi = 0.0, 1.2
    for _ in range(200):
        mid = (lo + hi) / 2
        f = (Vs - mid) / R - isat * (math.exp(mid / vt) - 1)
        lo, hi = (mid, hi) if f > 0 else (lo, mid)
    assert r.v("out") == pytest.approx((lo + hi) / 2, abs=1e-5)
    assert r.i("V1") == pytest.approx(-(Vs - r.v("out")) / R, rel=1e-4)


def test_spice_cmos_inverter_dc():
    def inv(vin):
        return _ckt(f"""
        simulator lang=spectre
        VDD (vdd 0) vsource dc=1.2
        VIN (in 0) vsource dc={vin}
        MP (out in vdd vdd) pmos w=4u l=0.1u kp=100e-6 vth0=-0.4
        MN (out in 0 0) nmos w=2u l=0.1u kp=200e-6 vth0=0.4
        """)
    assert spice.dc_solve(inv(0)).v("out") == pytest.approx(1.2, abs=1e-3)
    assert spice.dc_solve(inv(1.2)).v("out") == pytest.approx(0.0, abs=1e-3)
    mid = spice.dc_solve(inv(0.6)).v("out")
    assert 0.4 < mid < 0.8                       # 开关点附近


def test_spice_tran_rc_step():
    ckt = _ckt("""
    simulator lang=spectre
    V1 (in 0) vsource dc=0 type=pulse v1=0 v2=1 delay=0 rise=1p width=10n period=20n
    R1 (in out) resistor r=1k
    C1 (out 0) capacitor c=1p
    """)
    res = spice.tran_solve(ckt, 5e-9, 5e-12)
    t, v = res["t"], res["nodes"]["out"]
    ana = 1 - np.exp(-t / 1e-9)          # 源在步末求值，等效从 t=0 起跳
    m = t > 20e-12
    assert float(np.max(np.abs(v[m] - ana[m]))) < 3e-3


def test_spice_noise_ktoverc():
    ckt = _ckt("""
    simulator lang=spectre
    V1 (in 0) vsource dc=0 mag=1
    R1 (in out) resistor r=1k
    C1 (out 0) capacitor c=1p
    """)
    f = np.logspace(1, 12, 400)
    n = spice.noise_solve(ckt, f, "out")
    trapz = getattr(np, "trapezoid", None) or np.trapz
    total = float(trapz(n["total"], f))
    kT_C = 1.380649e-23 * (27 + 273.15) / 1e-12
    assert total == pytest.approx(kT_C, rel=0.02)


def test_spice_ring_oscillator_period():
    ckt = _ckt("""
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
    dc = spice.dc_solve(ckt)
    x0 = dc.x.copy()
    for n, dv in (("n1", -0.3), ("n2", 0.3), ("n3", -0.3)):
        x0[ckt.node_index[n] - 1] += dv
    res = spice.tran_solve(ckt, 20e-9, 0.002e-9, maxstep=0.002e-9, uic=x0)
    f = measure.frequency(res["t"], res["nodes"]["n1"])
    assert 5e9 < f < 20e9                        # 3 级环振，几十 ps 周期
    assert res["nodes"]["n1"].max() > 0.9


def test_spice_engine_via_testbench():
    from vlight import skill
    from vlight.sim import Simulator
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    from vlight.tb import Testbench, dc
    tb = Testbench(s.design, "demolib/divider", flatten=True)
    tb.element("V1 (in 0) vsource dc=3", "VGND (gnd 0) vsource dc=0")
    tb.analysis(dc("op"))
    res = Simulator(engine="vlight").run(tb)
    assert res.metric("value(V(out))", "op") == pytest.approx(2.0, rel=1e-6)


# --------------------------------------------------------------------------- #
# LVS 图同构
# --------------------------------------------------------------------------- #

BASE = """
simulator lang=spectre
subckt amp (in out vdd vss)
  M1 (n1 in vss vss) nmos w=2u l=0.1u
  M2 (out n1 vss vss) nmos w=2u l=0.1u
  R1 (out vdd) resistor r=10k
ends amp
"""


def test_lvs_renamed_netlist_matches():
    renamed = BASE.replace("M1", "X9").replace("M2", "X8").replace("R1", "X7") \
                  .replace("(n1 in vss vss)", "(q a g g)") \
                  .replace("(out n1 vss vss)", "(b q g g)") \
                  .replace("(out vdd)", "(b p)") \
                  .replace("(in out vdd vss)", "(a b p g)")
    g1 = lvs.Graph.from_netlist(spicein.parse(BASE))
    g2 = lvs.Graph.from_netlist(spicein.parse(renamed))
    rep = lvs.compare(g1, g2)
    assert rep.ok, rep.text()
    assert len(rep.matched_devices) == 3


def test_lvs_detects_broken_connection():
    broken = BASE.replace("M2 (out n1 vss vss)", "M2 (out in vss vss)")
    rep = lvs.compare(lvs.Graph.from_netlist(spicein.parse(BASE)),
                      lvs.Graph.from_netlist(spicein.parse(broken)))
    assert not rep.ok
    assert rep.net_mismatch or rep.only_in_ref


# --------------------------------------------------------------------------- #
# 多边形布尔
# --------------------------------------------------------------------------- #

def test_polybool_union_intersection():
    a = [(0, 0), (10, 0), (10, 10), (0, 10)]
    b = [(5, 0), (15, 0), (15, 10), (5, 10)]
    assert polybool.area(a) == pytest.approx(100)
    assert polybool.union_area([a, b]) == pytest.approx(150)
    assert polybool.intersection_area(a, b) == pytest.approx(50)
    assert len(polybool.to_rects([a, b])) == 1


def test_polybool_triangle():
    tri = [(0, 0), (10, 0), (0, 10)]
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert polybool.area(tri) == pytest.approx(50)
    assert polybool.union_area([tri, sq]) == pytest.approx(100)


# --------------------------------------------------------------------------- #
# 提取
# --------------------------------------------------------------------------- #

def test_extract_wire_resistance_and_cap():
    L = Layout("wire")
    L.add("M1", [(0, 0), (100e-6, 0), (100e-6, 1e-6), (0, 1e-6)])
    L.shapes.append(Shape(kind="label", layer="M1", text="IN", points=[(0.5e-6, 0.5e-6)]))
    nl = extract.extract(L, extract.ExtractRules.demo())
    s = extract.summary(nl)
    assert s["resistors"] == 1
    assert s["R_total_ohm"] == pytest.approx(10.0, rel=1e-9)   # 0.1Ω/sq × 100/1
    assert s["C_total_F"] > 0


def test_extract_coupling_cap():
    L = Layout("stack")
    L.add("M1", [(0, 0), (10e-6, 0), (10e-6, 10e-6), (0, 10e-6)])
    L.add("M2", [(0, 0), (10e-6, 0), (10e-6, 10e-6), (0, 10e-6)])
    nl = extract.extract(L, extract.ExtractRules.demo())
    caps = [e for e in nl.elements if e.master == "capacitor" and "Cc" in e.name]
    assert caps, "应当提取出层间耦合电容"
    c = float(caps[0].params["c"])
    assert c > 0


def test_microstrip_cap_order_of_magnitude():
    # 1um 宽、1um 高、εr=4：量级 ~ 1e-10 F/m
    c = extract.microstrip_cap(1e-6, 1e-6, 4.0, 60, 30)
    assert 1e-11 < c < 1e-9


# --------------------------------------------------------------------------- #
# 布线 / 布局
# --------------------------------------------------------------------------- #

def test_router_avoids_obstacle():
    R = route.Router((0, 0, 40, 40), pitch=1.0)
    R.add_obstacle("M1", [(15, 0), (17, 0), (17, 30), (15, 30)], inflate=1.0)
    p = R.route(("M1", 0, 10), ("M1", 35, 10))
    assert p.ok()
    assert p.length > 35                       # 必须绕路
    for x, y in p.points():
        assert not (15 <= x <= 17 and 0 <= y <= 30)


def test_router_two_layers_with_via():
    R = route.Router((0, 0, 30, 30), pitch=1.0, layers=["M1", "M2"])
    R.add_obstacle("M1", [(5, 0), (25, 0), (25, 30), (5, 30)], inflate=1.0)  # 整堵墙
    p = R.route(("M1", 2, 4), ("M1", 28, 4))
    assert p.ok()
    assert len(p.vias) >= 1                    # 必须换层绕过


def test_placement_reduces_hpwl_and_no_overlap():
    nodes = [f"U{i}" for i in range(8)]
    nets = [{"U0", "U1"}, {"U1", "U2"}, {"U2", "U3"}, {"U3", "U0"},
            {"U4", "U5"}, {"U5", "U6"}, {"U6", "U7"}, {"U7", "U4"},
            {"U0", "U4"}, {"U2", "U6"}]
    import random
    rnd = random.Random(1)
    rand = {n: (rnd.uniform(-50, 50), rnd.uniform(-50, 50)) for n in nodes}
    p = place.force_directed(nodes, nets, size=50, iterations=400)
    assert p.hpwl < place.hpwl(rand, nets)
    q = place.legalize(p, (20, 20), rows=4)
    assert place.overlap_count(q.pos, (20, 20)) == 0


# --------------------------------------------------------------------------- #
# 优化 / 良率
# --------------------------------------------------------------------------- #

def test_nelder_mead_quadratic():
    res = opt.nelder_mead(lambda x: (x[0] - 3) ** 2 + (x[1] + 1) ** 2, [0.0, 0.0])
    assert res.x[0] == pytest.approx(3.0, abs=1e-3)
    assert res.x[1] == pytest.approx(-1.0, abs=1e-3)


def test_monte_carlo_yield_matches_analytic():
    # R ~ N(1k, 25) 的 1/R 分压：均值 1000/E[R] ≈ 1.000625，与 MC 对照
    mc = opt.monte_carlo({"r": (1000.0, 25.0)}, lambda p: 1000.0 / p["r"],
                         n=4000, seed=5)
    st = mc.stats()
    assert st["mean"] == pytest.approx(1.000625, rel=0.02)
    y = opt.yield_estimate(mc.values, lower=0.95, upper=1.05)
    assert 0.9 < y["yield"] <= 1.0


# --------------------------------------------------------------------------- #
# 测量
# --------------------------------------------------------------------------- #

def test_measure_spectrum_thd():
    t = np.linspace(0, 100e-6, 1_000_000, endpoint=False)
    sig = np.sin(2 * np.pi * 1e6 * t) + 0.01 * np.sin(2 * np.pi * 3e6 * t)
    sp = measure.spectrum(t, sig)
    assert sp.thd() == pytest.approx(-40.0, abs=0.5)
    assert sp.sfdr() == pytest.approx(40.0, abs=0.5)


def test_measure_bandwidth_matches_rc():
    f = np.linspace(0, 10e9, 40000)
    H = 1 / (1 + 1j * f / 159.155e6)
    assert measure.bandwidth_3db(f, H) == pytest.approx(159.155e6, rel=2e-2)


def test_measure_phase_margin():
    f = np.linspace(0, 10e9, 40000)
    w = 2 * np.pi * f
    T = 1000 / ((1 + 1j * w / 2e6) * (1 + 1j * w / 50e6))
    pm = measure.phase_margin(f, T)
    assert pm == pytest.approx(9.4, abs=1.5)      # 两级极点、UGF≈50MHz 的解析值


def test_measure_time_domain():
    t = np.linspace(0, 20e-9, 5000)
    y = 1 - np.exp(-t / 1e-9)
    assert measure.rise_time(t, y) == pytest.approx(2.2e-9, rel=0.05)
    assert measure.settling_time(t, y, 1.0, 0.01) == pytest.approx(4.6e-9, rel=0.15)
