"""独立验收测试：DUT/参数/分析/来源/数值 + 三级状态。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vskill.phase2.acceptance import (check_analyses, check_dut, check_numeric,  # noqa: E402
                                      check_params, check_result_source, classify)

A = ROOT / "audit" / "2026-10-22"
NET = A / "m1" / "input.scs"


def test_dut_present_and_model():
    r = check_dut(NET, 1, "nch")
    assert r["ok"] is True and r["devices"] == 1


def test_dut_missing():
    tmp = A / "phase2" / "runs" / "_no_dut.scs"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(l for l in NET.read_text().splitlines()
                     if not l.startswith("M0"))
    tmp.write_text(text)
    assert check_dut(tmp, 1, "nch")["ok"] is False


def test_params_detect_width_mismatch():
    assert check_params(NET, {"l": "60n"})["ok"] is True
    bad = check_params(NET, {"w": "1u"})
    assert bad["ok"] is False and "w" in bad["mismatch"]


def test_analyses_missing():
    r = check_analyses(A / "m2", ["op", "tran", "ac"])
    assert r["ok"] is False and "ac" in r["missing"]


def test_result_source_hash(tmp_path):
    run = tmp_path / "run.json"
    run.write_text('{"netlist_hash": "deadbeef"}')
    assert check_result_source(run, NET)["ok"] is False


def test_numeric_rules():
    assert check_numeric({"x": 1.0}, {"x": {"min": 0.5, "max": 2.0}})["ok"] is True
    assert check_numeric({"x": 1.0}, {"x": {"min": 2.0}})["ok"] is False
    assert check_numeric({}, {"x": {"min": 0}})["ok"] is False


def test_classify_levels():
    assert classify(False, True, True) == "PROCESS_FAILED"
    assert classify(True, False, True) == "EVALUATION_INVALID"
    assert classify(True, True, None) == "EVALUATED_UNVERIFIED"
    assert classify(True, True, True) == "METRICS_MET"
    assert classify(True, True, False) == "METRICS_FAILED"


def test_completeness_rejects_missing_and_unknown():
    from vskill.phase2.acceptance import completeness
    checks = [{"check": "dut_present", "ok": True},
              {"check": "params", "ok": None}]
    r = completeness(checks)
    assert r["ok"] is False and "params" in r["unknown"]
    assert "analyses" in r["missing"]


def test_param_gate_blocks_acceptance():
    from vskill.phase2.acceptance import param_gate
    assert param_gate({"ok": False}) == "DIAGNOSTIC_ONLY"
    assert param_gate({"ok": True}) == "ACCEPTABLE"
