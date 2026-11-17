"""vskill：SKILL 函数表 → Python API（桥适配）的测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vskill                                            # noqa: E402
from vskill.bridge import (MockBridge, SkillHandle, Symbol,       # noqa: E402
                           SubprocessSkillBridge, VirtuosoBridgeAdapter)


@pytest.fixture()
def mock():
    b = MockBridge({
        "dbOpenCellViewByType": lambda lib, cell, view, mode, acc: {
            "name": f"{lib}/{cell}/{view}"},
        "dbCreateInst": lambda cv, master, name, point, orient: name,
        "abs": abs,
    })
    vskill.set_bridge(b)
    yield b
    vskill.set_bridge(None)


def test_table_is_complete():
    assert len(vskill.FUNCTIONS) >= 6000
    for name in ("dbOpenCellViewByType", "dbCreateInst", "rodCreateRect",
                 "hiGetCurrentWindow", "leHiCreateRect", "sprintf"):
        assert name in vskill.FUNCTIONS
    cov = vskill.api.coverage()
    assert cov["functions"] >= 6000 and cov["per_manual"]


def test_call_and_skill_code(mock):
    out = vskill.dbOpenCellViewByType("LIB", "CELL", "schematic", "schematic", "r")
    assert out == {"name": "LIB/CELL/schematic"}
    code = vskill.dbCreateInst.skill("cv", "m", "M1", [1, 2], "R0")
    assert code == '(dbCreateInst "cv" "m" "M1" (list 1 2) "R0")'
    assert mock.calls[-1][0] == "dbOpenCellViewByType"


def test_arity_check(mock):
    with pytest.raises(TypeError):
        vskill.dbCreateInst("a", "b", "c", "d", "e", "f", "g", "h", "i")


def test_kwargs_become_skill_keywords(mock):
    fn = vskill.api.get("sprintf")
    code = fn.skill("%s", fmt_string="x")
    assert "?fmt_string" in code


def test_handle_attribute_access(mock):
    cv = mock.add_object("cv1", {"cellName": "lna_2g4", "instances": 14},
                         otype="cellview")
    assert isinstance(cv, SkillHandle)
    assert cv.cellName == "lna_2g4"
    assert cv.instances == 14
    cv.cellName = "renamed"
    assert mock.objects["cv1"]["cellName"] == "renamed"


def test_unknown_name():
    with pytest.raises(AttributeError):
        vskill.api.get("definitelyNotASkillFunction")


def test_stub_file_generated():
    stub = ROOT / "vskill" / "api.pyi"
    assert stub.is_file()
    text = stub.read_text()
    assert text.count("def ") >= 6000
    assert "def dbCreateInst(" in text


def test_virtuoso_bridge_adapter_duck_typed():
    class FakeClient:
        def execute_skill(self, code):
            return "42"

    ad = VirtuosoBridgeAdapter(client=FakeClient())
    assert ad.call("abs", [-1]) == 42
    assert ad.eval("1+1") == "42"


def test_subprocess_bridge_construct():
    b = SubprocessSkillBridge(binary="/bin/echo")
    assert b.binary.endswith("echo")
