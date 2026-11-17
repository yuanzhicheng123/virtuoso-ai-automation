"""核心链路测试：导入 / 网表 / PSF / 计算器 / 库落盘 / SKILL / 版图。

跑法：python3 -m pytest tests -q       （不需要 spectre、不需要网络）
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import calc, gdsio, pcell, psf, render, skill  # noqa: E402
from vlight.netlist import DeviceMap, Netlister, fmt_value  # noqa: E402
from vlight.skilldump import import_tech, read_dump  # noqa: E402
from vlight.vdb import Design, Instance, Library, Schematic  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
DUMP = FIX / "tsmcN65_LNA2G4.dump.txt"
LNA = "tsmcN65_LNA2G4/lna_2g4_top"


@pytest.fixture(scope="module")
def design() -> Design:
    return read_dump(DUMP)


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #

def test_import_hierarchy(design):
    lib = design.libs["tsmcN65_LNA2G4"]
    assert sorted(lib.cells) == ["gain_stage", "input_match", "lna_2g4_top", "output_buffer"]
    top = lib.schematic("lna_2g4_top")
    assert len(top.insts) == 14
    assert len(top.nets) == 11
    assert top.pin_names() == ["rf_in", "rf_out", "vdd", "vb", "gnd"]


def test_import_cdf_params(design):
    top = design.schematic(LNA)
    m1 = top.inst("M1")
    assert m1.master == "tsmcN65/nmos_rf_25ud18"
    assert m1.pf("wr") == pytest.approx(10e-6)
    assert m1.pf("lr") == pytest.approx(350e-9)
    assert m1.pf("nr") == 29
    assert m1.pf("m") == 3
    assert top.inst("Cb").master == "tsmcN65/mimcap"


def test_net_of(design):
    top = design.schematic(LNA)
    assert top.net_of("M1", "D") == "d1"
    assert top.net_of("M1", "S") == "g_s"
    assert top.net_of("Cb", "PLUS") == "n_mid"


def test_check_flags_open_gate(design):
    issues = design.schematic(LNA).check()
    assert any("n_gate" in i for i in issues)


def test_connect_rewires(design):
    d = read_dump(DUMP)
    top = d.schematic(LNA)
    top.connect("M1", "G", "n_mid")
    assert top.net_of("M1", "G") == "n_mid"
    assert ("M1", "G") not in top.net("n_gate").terms


# --------------------------------------------------------------------------- #
# 网表
# --------------------------------------------------------------------------- #

def test_fmt_value():
    assert fmt_value("30u") == "30u"
    assert fmt_value(350e-9) == "350n"
    assert fmt_value(2) == "2"
    assert fmt_value(9) == "9"
    assert fmt_value(1.5) == "1.5"
    assert fmt_value(2.4e9) == "2.4G"


def test_netlist_device_mapping(design):
    text = Netlister(design).subckt("tsmcN65_LNA2G4/input_match")
    assert "subckt input_match rf_in rf_mid gnd" in text
    assert "L1 (rf_mid rf_in gnd) spiral_std_mu_z" in text
    assert "nr=3" in text and "lay=9" in text
    assert "Cb (rf_mid rsh) mimcap_sin lt=10u wt=10u mf=68 mimflag=2" in text
    assert "Rmatch (rsh gnd) rppolywo l=3.21u w=10u mf=1" in text
    assert "ends input_match" in text


def test_netlist_skips_pin_symbols(design):
    text = Netlister(design).subckt(LNA)
    assert "ipin" not in text and "opin" not in text and "iopin" not in text


def test_netlist_mos_multiplier(design):
    text = Netlister(design).subckt(LNA)
    assert "M2 (g_out vb d1 gnd) nmos_rf_18 lr=350.0n wr=10u nr=29 m=3" in text


def test_netlist_cdl(design):
    cdl = Netlister(design).cdl(LNA)
    assert cdl.splitlines()[0].startswith(".SUBCKT lna_2g4_top ")
    assert "/" not in cdl.splitlines()[0]
    assert cdl.splitlines()[-1] == ".ENDS"


def test_topology_order(design):
    # 这个顶层是平面电路（只挂 PDK 器件），topo 里只有自己
    assert design.topo(LNA) == [LNA]


def test_topology_hierarchical():
    from vlight.vdb import Cell
    d = Design()
    lib = Library("L", Path("/tmp/vlight_hier"))
    d.add(lib)
    leaf = Schematic("leaf", "L")
    leaf.add_pin("a")
    leaf.add_pin("b")
    lib.cells["leaf"] = Cell("leaf", "L", {"schematic": leaf})
    top = Schematic("top", "L")
    top.add_inst(Instance("X1", "L", "leaf"))
    top.connect("X1", "a", "n1")
    top.connect("X1", "b", "n2")
    lib.cells["top"] = Cell("top", "L", {"schematic": top})
    assert d.topo("L/top") == ["L/leaf", "L/top"]


def test_strict_missing_connection():
    d = Design()
    s = Schematic("c", "L")
    s.add_inst(Instance("R1", "L", "res"))          # 没连任何端子
    s.add_inst(Instance("R2", "L", "res"))
    d.add(Library("L", Path("/tmp/vlight_never")))
    d.lib("L").cells["c"] = __import__("vlight.vdb", fromlist=["Cell"]).Cell("c", "L", {"schematic": s})
    with pytest.raises(Exception):
        Netlister(d).subckt("L/c")


# --------------------------------------------------------------------------- #
# PSF / 计算器
# --------------------------------------------------------------------------- #

def test_psf_ac():
    p = psf.read_psf(FIX / "ac1.ac")
    assert p.kind == "ac" and p.xname == "freq"
    assert len(p.x) == 21
    assert np.iscomplexobj(p.v("out"))
    assert p.v("in")[0].real == pytest.approx(1.0, rel=1e-6)


def test_psf_tran():
    p = psf.read_psf(FIX / "tr1.tran.tran")
    assert p.xname == "time" and len(p.x) == 104
    assert p.x[0] == 0.0


def test_psf_dc_single_point():
    p = psf.read_psf(FIX / "dc1.dc")
    assert p.xname == "index" and len(p.x) == 1
    assert float(np.real(p.v("out")[0])) == pytest.approx(0.0476190, rel=1e-4)


def test_calc_basic():
    p = psf.read_psf(FIX / "ac1.ac")
    db = calc.evaluate("dB20(V(out))", p)
    ref = 20 * math.log10(abs(complex(p.y["out"][0])))
    assert float(np.real(db[0])) == pytest.approx(ref, rel=1e-9)
    assert calc.measure("value(V(in))", p) == pytest.approx(1.0, rel=1e-6)
    assert np.isnan(calc.evaluate("cross(dB20(V(out)),0)", p))


def test_calc_deriv_matches_finite_difference():
    p = psf.read_psf(FIX / "tr1.tran.tran")
    d = calc.evaluate("deriv(V(out), f)", p)
    i = len(p.x) // 2
    fd = (p.y["out"][i + 1] - p.y["out"][i - 1]) / (p.x[i + 1] - p.x[i - 1])
    assert float(d[i]) == pytest.approx(float(fd), rel=2e-2)


# --------------------------------------------------------------------------- #
# 库落盘
# --------------------------------------------------------------------------- #

def test_library_roundtrip(tmp_path):
    d = read_dump(DUMP)
    lib = d.libs["tsmcN65_LNA2G4"]
    lib.path = tmp_path / "tsmcN65_LNA2G4"
    lib.save()
    again = Library.open("tsmcN65_LNA2G4", lib.path)
    a = lib.schematic("input_match")
    b = again.schematic("input_match")
    assert len(a.insts) == len(b.insts)
    assert a.net("rsh").terms == b.net("rsh").terms
    assert int(float(b.inst("Cb").params["m"])) == 68


# --------------------------------------------------------------------------- #
# SKILL
# --------------------------------------------------------------------------- #

def test_skill_builds_schematic():
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    sch = s.design.schematic("demolib/divider")
    assert sch.pin_names() == ["in", "out", "gnd"]
    assert sch.net("out").terms == [("R1", "MINUS"), ("R2", "PLUS")]
    assert sch.inst("R1").params["r"] == "1k"


def test_skill_netlists_to_spectre():
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    text = Netlister(s.design).subckt("demolib/divider")
    assert "R1 (in out) resistor r=1k" in text
    assert "R2 (out gnd) resistor r=2k" in text


def test_skill_language_core():
    s = skill.Session()
    out = s.run("""
        procedure( sq (x) x * x )
        printf("sq=%L\\n" sq(7))
        acc = 0
        foreach( v '(1 2 3) acc = acc + v )
        printf("sum=%L\\n" acc)
        sprintf(nil "%d/%s" 42 "ok")
    """)
    assert out == "42/ok"


# --------------------------------------------------------------------------- #
# 版图 / GDS / 渲染
# --------------------------------------------------------------------------- #

def test_pcell_inductor_geometry():
    lay, met = pcell.make("spiral_ind", nr=2, rad=60e-6, w=10e-6, spacing=3e-6)
    assert met["D_out"] == pytest.approx(120e-6)
    assert 0.2e-9 < met["L_H"] < 3e-9
    bb = lay.bbox()
    assert bb[2] - bb[0] > 100e-6


def test_pcell_res_and_cap():
    lay, met = pcell.make("poly_res", l=10e-6, w=1e-6, segments=2, rsh=250)
    assert met["R_ohm"] == pytest.approx(5000)
    lay2, met2 = pcell.make("mim_cap", lt=10e-6, wt=10e-6, m=4)
    assert 0 < met2["C_F"] < 1e-12


def test_gds_roundtrip(tmp_path):
    lay, _ = pcell.make("spiral_ind", nr=2, rad=60e-6, w=10e-6, spacing=3e-6)
    lib = Library("t", tmp_path / "t")
    lib.cell("IND", create=True).layouts["layout"] = lay
    tech = import_tech(ROOT / "recon" / "tsmcN65.layermap")
    out = gdsio.write_gds(tmp_path / "ind.gds", lib, "IND", tech)
    info = gdsio.read_gds(out)
    assert "IND" in info["cells"] and len(info["cells"]["IND"]["shapes"]) > 10
    assert all(len(s["points"]) >= 4 for s in info["cells"]["IND"]["shapes"])


def test_render_svg_contains_shapes(design):
    svg = render.schematic_svg(design.schematic(LNA))
    assert svg.startswith("<svg") and "M1" in svg and "rf_out" in svg
    lay, _ = pcell.make("spiral_ind", nr=1, rad=30e-6, w=5e-6, spacing=2e-6)
    assert "<polygon" in render.layout_svg(lay)
