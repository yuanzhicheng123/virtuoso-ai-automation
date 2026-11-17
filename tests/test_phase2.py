"""Phase 2 离线测试：IR / 语义 diff / 结果适配 / 网表参数校验。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vskill.phase2 import diff, from_oa, from_scs                     # noqa: E402
from vskill.phase2.netlist_adapter import NetlistError, export      # noqa: E402
from vskill.phase2.psf_results_adapter import op_point              # noqa: E402

A = ROOT / "audit" / "2026-10-22"


def test_ir_from_real_netlist():
    c = from_scs(A / "m1" / "input.scs")
    assert len(c.devices) == 1
    d = c.devices[0]
    assert d.model == "nch"
    assert d.params["l"] == "60n" and d.params["w"] == "200n"
    assert len(d.terminals) == 4


def test_diff_detects_width_mismatch():
    net = from_scs(A / "m1" / "input.scs")
    oa = from_oa([{"name": "M0", "master": "nch",
                   "terminals": ["D", "G", "S", "B"],
                   "props": {"w": "1u", "l": "60n"}}],
                 ["D", "G", "S", "B"])
    rep = diff(oa, net)
    kinds = {(i["kind"], i.get("param")) for i in rep["issues"]}
    assert ("param", "w") in kinds
    assert rep["ok"] is False


def test_diff_identical():
    a = from_scs(A / "m1" / "input.scs")
    b = from_scs(A / "m1" / "input.scs")
    assert diff(a, b)["ok"] is True


def test_netlist_name_validation():
    with pytest.raises(NetlistError):
        export("bad lib; rm -rf /", "tb")


def test_psf_op_point():
    vals = op_point(A / "m2" / "op1.dc")
    assert abs(vals["d"] - 1.2) < 1e-6
    assert abs(vals["g"] - 0.6) < 1e-6
    assert vals["VD:p"] < 0          # 电流流出节点
