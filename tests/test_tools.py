"""工具层：可发现（schema）/ 可调用（校验）/ 可验证（证据）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vskill.bridge import MockBridge, SkillHandle, set_bridge        # noqa: E402
from vskill.session import Session                                   # noqa: E402
from vskill.tools import Param, Registry, ToolError                  # noqa: E402
from vskill.tools_builtin import BUILTIN, install                   # noqa: E402


def test_manifest_shape():
    m = BUILTIN.manifest()
    assert m["schema"] == "mcp/tools/v1"
    assert m["count"] >= 12
    names = {t["name"] for t in m["tools"]}
    assert "vl.inst.create" in names and "vl.cell.inspect" in names
    for t in m["tools"]:
        assert t["inputSchema"]["type"] == "object"
        assert "properties" in t["inputSchema"]


def test_list_filters():
    reg = install()
    assert all(t["name"].startswith("vl.prop") for t in reg.list(prefix="vl.prop"))
    assert all(t["annotations"]["risk"] == "read" for t in reg.list(risk="read"))
    assert any(t["name"] == "vl.wire.draw" for t in reg.list(tag="shape"))


def test_missing_required_param():
    reg = install()
    with pytest.raises(ToolError):
        reg.call("vl.inst.create", cv="x")


def test_unknown_param_rejected():
    reg = install()
    with pytest.raises(ToolError):
        reg.call("vl.cell.inspect", cv="x", bogus=1)


def test_call_and_verify_with_mock():
    mb = MockBridge()
    cv = mb.add_object("cv1", {"instances": [], "nets": [], "shapes": []}, "cellview")
    mb.add("ddCreateLib", lambda lib, path: True)
    mb.add("ddGetObj", lambda lib: cv)
    set_bridge(mb)
    reg = install()
    reg.session = Session(mb)
    ev = reg.verify("vl.lib.create", lib="L", path="./L")
    assert ev.ok and ev.verified
    assert "ddGetObj" in ev.detail
    set_bridge(None)


def test_report_counts():
    reg = install()
    rep = reg.report()
    assert rep["tools"] >= 12 and rep["calls"] == 0
