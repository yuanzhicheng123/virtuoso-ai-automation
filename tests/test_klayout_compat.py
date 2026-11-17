"""与 KLayout 的兼容性回归：整份 compat 套件必须 100% 通过。

没有装 KLayout 时自动跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

kdb = pytest.importorskip("klayout.db")

from compat import check as compat_check           # noqa: E402
from vlight import devext, geom2d, gdsio          # noqa: E402
from vlight.region import Region                   # noqa: E402
from vlight.vdb import Layout, Library, Shape, TechFile  # noqa: E402


def test_compat_suite_all_pass():
    rep = compat_check.run()
    failed = [c["id"] for c in rep["checks"] if not c["ok"]]
    assert rep["overall"]["score"] == 1.0, f"未通过: {failed}"


def test_region_boolean_areas():
    a = Region.from_rect(0, 0, 10, 10)
    b = Region.from_rect(5, 0, 15, 10)
    assert (a & b).area() == pytest.approx(50)
    assert (a | b).area() == pytest.approx(150)
    assert (a - b).area() == pytest.approx(50)
    assert (a ^ b).area() == pytest.approx(100)
    assert a.sized(1).area() == pytest.approx(144)
    assert len(a.width_check(11)) == 1
    assert len(a.space_check(1)) == 0


def test_region_hole_like_shape():
    # 凹多边形（C 形）：面积 = 10*10 - 4*6 = 76
    poly = [(0, 0), (10, 0), (10, 10), (6, 10), (6, 4), (4, 4), (4, 10), (0, 10)]
    assert Region([poly]).area() == pytest.approx(88)     # 100 - 2*6 的缺口


def test_gds_text_path_aref_roundtrip(tmp_path):
    lib = Library("t", tmp_path / "t")
    lay = Layout("T", "t")
    lay.add("M1", [(0, 0), (10e-6, 0), (10e-6, 5e-6), (0, 5e-6)])
    lay.shapes.append(Shape(kind="text", layer="M1", text="N1",
                            points=[(1e-6, 1e-6)]))
    lay.shapes.append(Shape(kind="path", layer="M1", width=0.5e-6,
                            points=[(0, 4e-6), (10e-6, 4e-6)]))
    sub = Layout("S", "t")
    sub.add("M1", [(0, 0), (1e-6, 0), (1e-6, 1e-6), (0, 1e-6)])
    lib.cell("T", create=True).layouts["layout"] = lay
    lib.cell("S", create=True).layouts["layout"] = sub
    lay.instances.append(__import__("vlight.vdb", fromlist=["Instance"]).Instance(
        "A", "t", "S", "layout", (20e-6, 0), cols=2, rows=3,
        colspace=2e-6, rowspace=2e-6))
    tech = TechFile()
    tech.add("M1", 1)
    tech.gds_map[("M1", "drawing")] = (1, 0)
    p = gdsio.write_gds(tmp_path / "t.gds", lib, "T", tech)
    info = gdsio.read_gds(p)
    kinds = {sh["kind"] for sh in info["cells"]["T"]["shapes"]}
    assert {"polygon", "text", "path"} <= kinds
    inst = info["cells"]["T"]["instances"][0]
    assert (inst["cols"], inst["rows"]) == (2, 3)
    assert inst["colspace"] == pytest.approx(2e-6)


def test_mos_extraction_counts():
    L = Layout("m")
    L.add("OD", [(0, 0), (6e-6, 0), (6e-6, 1e-6), (0, 1e-6)])
    L.add("PO", [(2e-6, -0.2e-6), (2.1e-6, -0.2e-6), (2.1e-6, 1.2e-6), (2e-6, 1.2e-6)])
    nl = devext.extract_mos(L)
    st = devext.stats(nl)
    assert st["devices"] == 1
    assert float(nl.elements[0].params["l"]) == pytest.approx(0.1e-6, rel=1e-6)
    assert float(nl.elements[0].params["w"]) == pytest.approx(1.0e-6, rel=1e-6)
