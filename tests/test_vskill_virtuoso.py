"""vskill × virtuoso-bridge：适配器、批量单次往返、结果解码。

用注入的假 `virtuoso_bridge` 模块验证真实调用路径（不需要装它）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vskill                                            # noqa: E402
from vskill.bridges.virtuoso import VirtuosoBridge       # noqa: E402


class FakeClient:
    calls: list[str] = []

    def __init__(self, *a, **kw):
        pass

    @classmethod
    def from_env(cls):
        return cls()

    def execute_skill(self, code):
        FakeClient.calls.append(code)
        if code.startswith("let("):
            return '("M1" "M2")'
        if "dbOpenCellViewByType" in code:
            return '"<cellview cv1>"'
        return "42"


@pytest.fixture()
def fake_module(monkeypatch):
    FakeClient.calls = []
    mod = types.SimpleNamespace(VirtuosoClient=FakeClient)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", mod)
    return FakeClient


def test_adapter_uses_from_env(fake_module):
    b = VirtuosoBridge()
    assert isinstance(b.client, fake_module)
    assert b.call("abs", [-1]) == 42
    assert fake_module.calls[-1] == "(abs -1)"


def test_adapter_accepts_explicit_client(fake_module):
    b = VirtuosoBridge(client=FakeClient())
    assert b.call("car", ["(1 2)"]) == 42


def test_batch_is_single_round_trip(fake_module):
    b = VirtuosoBridge()
    out = b.execute_batch([
        ("dbCreateInst", ["cv", "nch", "M1"]),
        ("dbCreateInst", ["cv", "pch", "M2"]),
    ])
    assert out == ["M1", "M2"]
    assert len(fake_module.calls) == 1
    code = fake_module.calls[0]
    assert code.startswith("let(") and "__vl0" in code and "__vl1" in code


def test_session_batch_fills_refs(fake_module):
    s = vskill.Session(VirtuosoBridge())
    with s.batch() as b:
        r1 = b.dbCreateInst("cv", "nch", "M1")
        r2 = b.dbCreateInst("cv", "pch", "M2")
    assert r1.value == "M1" and r2.value == "M2"
    assert len(fake_module.calls) == 1


def test_handle_decode_from_bridge(fake_module):
    b = VirtuosoBridge()
    val = b.call("dbOpenCellViewByType", ["L", "C", "layout", "r", ""])
    assert isinstance(val, vskill.SkillHandle)      # "<cellview cv1>" → 句柄
    assert val.type == "cellview"


def test_cli_list_and_doc(capsys):
    from vskill import cli
    assert cli.main(["list", "--filter", "dbCreate", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "dbCreate" in out and "匹配" in out
    assert cli.main(["doc", "dbCreateInst"]) == 0
    assert "dbCreateInst(" in capsys.readouterr().out
