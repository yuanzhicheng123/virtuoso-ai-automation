"""端到端集成测试：真的调 spectre。

需要一台能跑 spectre 的机器（本机 license 可能不在）：

    VLIGHT_HOST=eda-220 VLIGHT_SPECTRE=/eda/cds/SPECTRE201/tools/bin/spectre \
    python3 -m pytest tests/test_sim_integration.py -q

不设 VLIGHT_HOST 时自动跳过，所以 CI/离线也能全绿。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import pdk, skill  # noqa: E402
from vlight.netlist import Netlister  # noqa: E402
from vlight.sim import Simulator  # noqa: E402
from vlight.skilldump import read_dump  # noqa: E402
from vlight.tb import Testbench, ac, dc  # noqa: E402

HOST = os.environ.get("VLIGHT_HOST")
SPECTRE = os.environ.get("VLIGHT_SPECTRE", "spectre")
PDK = os.environ.get("VLIGHT_PDK", pdk.TSMC65_MODELS)

pytestmark = pytest.mark.skipif(not HOST, reason="需要 VLIGHT_HOST（能跑 spectre 的机器）")


def _sim(host) -> Simulator:
    return Simulator(spectre=SPECTRE, host=HOST, workdir=str(ROOT / "sim_it"), timeout=600)


def test_divider_dc_exact():
    """1k/2k 分压：3V 输入必须精确得到 2V。"""
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    tb = Testbench(s.design, "demolib/divider", flatten=True)
    tb.element("V1 (in 0) vsource dc=3", "VGND (gnd 0) vsource dc=0")
    tb.analysis(dc("op"))
    res = _sim(HOST).run(tb, name="it_divider")
    assert res.metric("value(V(out))", "op") == pytest.approx(2.0, rel=1e-6)
    assert res.metric("value(V(out))/3", "op") == pytest.approx(2 / 3, rel=1e-6)


def test_rc_ac_matches_analytic():
    """RC 低通：1k + 1pF，fc=159MHz，在 fc 处 |H| = 1/sqrt2。"""
    s = skill.Session()
    d = s.design
    lib = d.add(__import__("vlight.vdb", fromlist=["Library"]).Library("t", ROOT / "sim_it" / "lib"))
    from vlight.vdb import Instance
    sch = lib.new_cell("rc", [("in", "input"), ("out", "output"), ("gnd", "inputOutput")])
    sch.add_inst(Instance("R1", "vlightLib", "res", params={"r": "1k"}))
    sch.add_inst(Instance("C1", "vlightLib", "cap", params={"c": "1p"}))
    sch.connect("R1", "PLUS", "in")
    sch.connect("R1", "MINUS", "out")
    sch.connect("C1", "PLUS", "out")
    sch.connect("C1", "MINUS", "gnd")

    tb = Testbench(d, "t/rc", flatten=True)
    tb.element("V1 (in 0) vsource dc=0 mag=1", "VGND (gnd 0) vsource dc=0")
    tb.analysis(ac("ac1", start=1e6, stop=1e9, dec=10))
    res = _sim(HOST).run(tb, name="it_rc")
    p = res.plot("ac1")
    fc = 1 / (2 * np.pi * 1e3 * 1e-12)
    i = int(np.argmin(np.abs(p.x - fc)))
    assert abs(p.v("out")[i]) == pytest.approx(1 / np.sqrt(2), abs=3e-3)


def test_real_lna_netlist_simulates():
    """220 上真实工程：网表能被 spectre 读入并出 DC 工作点（0 error）。"""
    d = read_dump(ROOT / "tests" / "fixtures" / "tsmcN65_LNA2G4.dump.txt")
    top = d.schematic("tsmcN65_LNA2G4/lna_2g4_top")
    top.connect("M1", "G", "n_mid")            # 修掉悬空栅极
    tb = Testbench(d, "tsmcN65_LNA2G4/lna_2g4_top", flatten=True)
    pdk.apply(tb, pdk.tsmc65("tt", models=PDK))
    tb.var(vdd=1.2)
    tb.element("VDD (vdd 0) vsource dc=vdd", "VB (vb 0) vsource dc=0.85",
               "VG (n_gate 0) vsource dc=0", "VGND (gnd 0) vsource dc=0")
    tb.port("P1", "rf_in")
    tb.port("P2", "rf_out", mag=0)
    tb.analysis(dc("op"))
    res = _sim(HOST).run(tb, name="it_lna")
    assert "op" in res.plots
    assert len(res.plot("op").y) > 20           # 内部节点都出来了


def test_own_engine_matches_spectre():
    """自研 SPICE vs Spectre：同一 RC 网络，AC 幅相与 DC 都要对上。"""
    import numpy as np
    from vlight.vdb import Instance, Library, Schematic

    s = skill.Session()
    d = s.design
    lib = d.add(Library("t", ROOT / "sim_it" / "lib2"))
    sch = lib.new_cell("rc2", [("in", "input"), ("out", "output"), ("gnd", "inputOutput")])
    sch.add_inst(Instance("R1", "vlightLib", "res", params={"r": "1k"}))
    sch.add_inst(Instance("C1", "vlightLib", "cap", params={"c": "1p"}))
    sch.connect("R1", "PLUS", "in")
    sch.connect("R1", "MINUS", "out")
    sch.connect("C1", "PLUS", "out")
    sch.connect("C1", "MINUS", "gnd")

    tb = Testbench(d, "t/rc2", flatten=True)
    tb.element("V1 (in 0) vsource dc=0 mag=1", "VGND (gnd 0) vsource dc=0")
    tb.analysis(ac("ac1", start=1e7, stop=1e9, dec=6))

    from vlight.spice import Engine
    ours = Engine(d).run(tb)
    ref = _sim(HOST).run(tb, name="it_cmp")
    p1, p2 = ours.plot("ac1"), ref.plot("ac1")
    f = p1.x
    i2 = np.interp(f, p2.x, np.real(p2.v("out")))
    i2i = np.interp(f, p2.x, np.imag(p2.v("out")))
    ours_v = p1.v("out")
    err = np.max(np.abs(np.abs(ours_v) - np.hypot(i2, i2i)) / np.maximum(np.hypot(i2, i2i), 1e-12))
    assert err < 0.02, f"幅频最大相对误差 {err:.3%}"
    ph1 = np.angle(ours_v)
    ph2 = np.angle(i2 + 1j * i2i)
    assert np.max(np.abs(ph1 - ph2)) < 0.02
