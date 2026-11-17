"""新补齐的核心模块测试：CDF / ADE / OCEAN / spiceIn / 连接性 / DRC。

全部离线：OCEAN 用假仿真器，几何用合成版图。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import ade, cdf, connect, drc, geom2d, ocean, skill, spicein  # noqa: E402
from vlight.netlist import DeviceMap, Netlister  # noqa: E402
from vlight.psf import Plot  # noqa: E402
from vlight.sim import Results  # noqa: E402
from vlight.skilldump import read_dump  # noqa: E402
from vlight.vdb import Design, Instance, Layout, Schematic, Shape, Library  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
DUMP = FIX / "tsmcN65_LNA2G4.dump.txt"
STATE = Path("/home/lihong/projects/RFIC-GPT/recon/wizard/RFIC-GPT_Wizard/"
             "RFIC-GPT_Wizard_Active/CSPA/CSPA_DM_lvt4t/webdisplay_state1")


# --------------------------------------------------------------------------- #
# CDF
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def cdfdb() -> cdf.CDFdb:
    db = cdf.CDFdb()
    db.from_dump_text(DUMP.read_text())
    return db


def test_cdf_from_dump(cdfdb):
    assert len(cdfdb) == 7
    c = cdfdb.get("tsmcN65", "rppolywo")
    assert c is not None and len(c.params) >= 20
    assert c.param("w").default == "10u"
    assert c.param("l").default == "6.675u"


def test_cdf_ipar_eval(cdfdb):
    c = cdfdb.get("tsmcN65", "mimcap")
    assert cdf.evaluate_expr(c.param("model").default, c.defaults()) == "mimcap_sin"
    assert cdf.evaluate_expr(c.param("mf").default, c.defaults()) == "68"


def test_cdf_fills_netlist_defaults(cdfdb):
    d = read_dump(DUMP)
    top = d.schematic("tsmcN65_LNA2G4/lna_2g4_top")
    inst = top.inst("Rmatch")
    inst.params = {"l": "3.21u", "res": "3.89519K"}       # 只留一个参数
    line = Netlister(d, DeviceMap(), cdf=cdfdb).instance_line(inst, top)
    assert line == "Rmatch (rsh gnd) rppolywo l=3.21u w=10u mf=1"
    line2 = Netlister(d, DeviceMap()).instance_line(inst, top)
    assert line2 == "Rmatch (rsh gnd) rppolywo l=3.21u"    # 没 CDF 就没默认值


def test_netlist_procedure_dispatch():
    d = Design()
    lib = d.add(Library("L", Path("/tmp/vlight_nlproc")))
    sch = lib.new_cell("c")
    sch.add_inst(Instance("R9", "L", "res", params={"r": "2k"}))
    sch.connect("R9", "PLUS", "a")
    sch.connect("R9", "MINUS", "b")
    db = cdf.CDFdb()
    c = db.require("L", "res")
    c.add(cdf.CDFParam("r", "1k"))
    c.netlist_procedure = "vlight_res"
    line = Netlister(d, DeviceMap(), cdf=db).instance_line(sch.inst("R9"), sch)
    assert line == "R9 (a b) resistor r=2k"


# --------------------------------------------------------------------------- #
# ADE
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not STATE.is_dir(), reason="需要真实 ADE 状态目录")
def test_ade_import_real_state():
    st = ade.import_state_dir(STATE)
    assert [a.kind for a in st.analyses] == ["dc", "sp"]
    sp_a = st.analyses[1]
    assert sp_a.args["stop"] == pytest.approx(80e9)
    assert sp_a.args["step"] == pytest.approx(1e9)
    assert st.options["temp"] == "85.0"
    assert len(st.variables) == 13 and "Vdd" in st.variables
    names = [o.name for o in st.outputs]
    assert names == ["B1f_min", "Kf_min", "Kf", "B1f"]
    assert st.outputs[3].expression.startswith("b1f(sp(1, 1")
    assert st.model_files[0][1] == "top_tt"


@pytest.mark.skipif(not STATE.is_dir(), reason="需要真实 ADE 状态目录")
def test_ade_state_roundtrip(tmp_path):
    st = ade.import_state_dir(STATE)
    p = st.save(tmp_path / "s.json")
    back = ade.ADELState.load(p)
    assert [a.kind for a in back.analyses] == ["dc", "sp"]
    assert back.variables.keys() == st.variables.keys()
    assert [o.name for o in back.outputs] == [o.name for o in st.outputs]


@pytest.mark.skipif(not STATE.is_dir(), reason="需要真实 ADE 状态目录")
def test_ade_to_testbench():
    st = ade.import_state_dir(STATE)
    tb = st.to_testbench(Design(), None)
    scs = tb.scs()
    assert "temp=85" in scs
    assert "sp1 sp" in scs or "sp sp" in scs


# --------------------------------------------------------------------------- #
# OCEAN（假仿真器）
# --------------------------------------------------------------------------- #

class FakeSim:
    def __init__(self):
        self.calls = 0

    def run(self, tb, name=None):
        self.calls += 1
        val = float(tb.variables.get("r2", "2k").rstrip("k")) / 2.0
        p = Plot(name="dc", kind="dc", xname="index", x=np.zeros(1),
                 y={"out": np.array([val])})
        return Results(plots={"dc": p}, netlist=tb.scs())


def _ocean_design() -> Design:
    s = skill.Session()
    s.run_file(ROOT / "skill" / "build_rc.il")
    return s.design


def test_ocean_run_and_print():
    oc = ocean.Ocean(_ocean_design(), FakeSim())
    oc.run_script("""
        design("demolib" "divider_var" "schematic")
        desVar("r2" "2k")
        element("V1 (in 0) vsource dc=3")
        element("VGND (gnd 0) vsource dc=0")
        analysis('dc ?oppoint "rawfile")
        run()
        selectResult('dc)
        ocnPrint(v("/out"))
    """)
    assert oc.printed == ["out: last=1 n=1"]         # 2k/2 -> 1.0
    assert oc.sim.calls == 1
    assert "V1 (in 0) vsource dc=3" in oc.results.netlist


def test_ocean_foreach_sweep():
    oc = ocean.Ocean(_ocean_design(), FakeSim())
    oc.run_script("""
        design("demolib" "divider_var" "schematic")
        analysis('dc)
        foreach(r '("1k" "2k" "3k")
          desVar("r2" r)
          run()
          selectResult('dc)
          ocnPrint(v("/out")) )
    """)
    assert len(oc.printed) == 3
    assert [float(p.split("last=")[1].split()[0]) for p in oc.printed] == [0.5, 1.0, 1.5]


# --------------------------------------------------------------------------- #
# spiceIn
# --------------------------------------------------------------------------- #

def test_spicein_vco():
    nl = spicein.read(FIX / "vco_core.scs")
    assert len(nl.elements) == 10
    assert [a.kind for a in nl.analyses] == ["dc", "tran", "pss"]
    m1 = next(e for e in nl.elements if e.name == "M1")
    assert m1.master == "n12ll_ckt_rf_mis"
    assert m1.nodes == ["TANKP", "TANKN", "TAIL", "0"]
    assert m1.params["wr"] == "2.0u"
    assert len(nl.includes) == 3


def test_spicein_roundtrip():
    for name in ("vco_core.scs", "diff_amp.scs"):
        nl = spicein.read(FIX / name)
        again = spicein.parse(nl.to_scs())
        assert again.stats()["elements"] == nl.stats()["elements"]
        assert again.stats()["analyses"] == nl.stats()["analyses"]
        assert again.parameters == nl.parameters


def test_spicein_subckt_to_design():
    text = """
    simulator lang=spectre
    global 0
    subckt inv (a y vdd vss)
      M1 (y a vss vss) nch w=1u l=60n
      M2 (y a vdd vdd) pch w=2u l=60n
    ends inv
    """
    nl = spicein.parse(text)
    d = nl.to_design("imp")
    sch = d.schematic("imp/inv")
    assert sch.pin_names() == ["a", "y", "vdd", "vss"]
    assert len(sch.insts) == 2
    assert sch.net_of("M1", "D") == "y"
    # 回到网表：端子顺序按 subckt 位置
    assert "M1 (y a vss vss) nch w=1u l=60n" in Netlister(d, allow_unknown=True).subckt("imp/inv")


def test_spicein_to_testbench():
    nl = spicein.read(FIX / "diff_amp.scs")
    tb = spicein.to_testbench(nl)
    scs = tb.scs()
    assert "include " in scs and "section=tt" in scs
    assert "parameters WP=20u" in scs
    assert "ac ac start=1k stop=1G dec=20" in scs
    assert "save voutp voutn vinp vinn" in scs


# --------------------------------------------------------------------------- #
# 连接性 / DRC
# --------------------------------------------------------------------------- #

def _demo_layout() -> Layout:
    L = Layout("t")
    L.add("M1", [(0, 0), (10e-6, 0), (10e-6, 10e-6), (0, 10e-6)])
    L.add("M1", [(10e-6, 0), (20e-6, 0), (20e-6, 10e-6), (10e-6, 10e-6)])
    L.add("M8", [(40e-6, 0), (50e-6, 0), (50e-6, 10e-6), (40e-6, 10e-6)])
    L.add("VIA8", [(44e-6, 4e-6), (46e-6, 4e-6), (46e-6, 6e-6), (44e-6, 6e-6)])
    L.add("M9", [(42e-6, 2e-6), (48e-6, 2e-6), (48e-6, 8e-6), (42e-6, 8e-6)])
    L.shapes.append(Shape(kind="label", layer="M1", text="IN", points=[(5e-6, 5e-6)]))
    L.shapes.append(Shape(kind="label", layer="M1", text="MID",
                          points=[(15e-6, 5e-6)]))
    L.shapes.append(Shape(kind="label", layer="M9", text="VDD", points=[(45e-6, 5e-6)]))
    return L


def test_connect_extract():
    c = connect.extract(_demo_layout())
    assert set(c.nets) == {"IN", "VDD"}          # IN/MID 贴在一起 -> 短路（保留 IN）
    assert any("短路" in w for w in c.warnings)
    assert len(c.nets["VDD"]) == 3               # M8 + via + M9


def test_lvs_reports_short_and_missing():
    c = connect.extract(_demo_layout())
    sch = Schematic("t")
    for n in ("IN", "MID", "VDD", "OUT"):
        sch.add_pin(n)
    rep = connect.lvs(sch, c)
    assert not rep.ok
    assert "OUT" in rep.missing_in_layout
    assert rep.shorts


def test_lvs_pass():
    L = Layout("t")
    L.add("M1", [(0, 0), (10e-6, 0), (10e-6, 10e-6), (0, 10e-6)])
    L.add("M1", [(20e-6, 0), (30e-6, 0), (30e-6, 10e-6), (20e-6, 10e-6)])
    L.shapes.append(Shape(kind="label", layer="M1", text="A", points=[(5e-6, 5e-6)]))
    L.shapes.append(Shape(kind="label", layer="M1", text="B", points=[(25e-6, 5e-6)]))
    sch = Schematic("t")
    sch.add_pin("A")
    sch.add_pin("B")
    rep = connect.lvs(sch, connect.extract(L))
    assert rep.ok and rep.matched == ["A", "B"]


def test_drc_violations():
    L = Layout("t")
    L.add("M1", [(0, 0), (0.05e-6, 0), (0.05e-6, 10e-6), (0, 10e-6)])       # 太窄
    L.add("M1", [(10e-6, 0), (20e-6, 0), (20e-6, 10e-6), (10e-6, 10e-6)])
    L.add("M1", [(20.1e-6, 0), (30e-6, 0), (30e-6, 10e-6), (20.1e-6, 10e-6)])  # 太近
    deck = drc.Deck()
    deck.add("M1", min_width=0.1e-6, min_space=0.2e-6, min_area=0.05e-12)
    viol = drc.check(L, deck)
    kinds = {v.kind for v in viol}
    assert "min_width" in kinds and "min_space" in kinds
    assert all(v.layer == "M1" for v in viol)


def test_drc_clean_layout():
    lay, _ = __import__("vlight.pcell", fromlist=["make"]).make(
        "poly_res", l=10e-6, w=1e-6, segments=1)
    deck = drc.Deck()
    deck.add("PO", min_width=0.1e-6, min_space=0.2e-6, min_area=0.0)
    deck.add("CO", min_width=0.1e-6, min_space=0.1e-6)
    assert not [v for v in drc.check(lay, deck) if v.kind != "grid"]


def test_geom2d_basics():
    sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert geom2d.area(sq) == pytest.approx(4)
    assert geom2d.point_in_poly((1, 1), sq)
    assert not geom2d.point_in_poly((3, 1), sq)
    assert geom2d.min_width(sq) == pytest.approx(2)
    assert geom2d.polys_touch(sq, [(2, 0), (4, 0), (4, 2), (2, 2)])
    assert geom2d.poly_poly_dist(sq, [(4, 0), (6, 0), (6, 2), (4, 2)]) == pytest.approx(2)


# --------------------------------------------------------------------------- #
# SKILL 扩展（defstruct 的 ->、OCEAN 关键字参数）
# --------------------------------------------------------------------------- #

def test_skill_arrow_accessor_and_struct():
    s = skill.Session()
    v = s.run("""
        st = make_sevOutputStruct()
        st->name = "gain"
        st->expr = list('dB20 3)
        sprintf(nil "%s %L" st->name st->expr)
    """)
    assert v == 'gain (dB20 3)'


def test_skill_keyword_args_passthrough():
    s = skill.Session()
    seen = {}

    def f(*args):
        seen["args"] = args
        return 1

    s.env.vars["f"] = f
    s.run('f(1 ?start 2 ?stop 3)')
    assert seen["args"][0] == 1
    assert str(seen["args"][1]) == "?start"
    assert seen["args"][2] == 2
