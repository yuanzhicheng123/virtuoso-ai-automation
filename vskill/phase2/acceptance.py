"""独立验收：区分"进程成功 / 评估有效 / 指标达标"，防止假成功。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .circuit_ir import from_scs


def check_dut(netlist: str | Path, expect_devices: int = 1,
              expect_model: str | None = None) -> dict:
    ir = from_scs(netlist)
    n = len(ir.devices)
    ok = n == expect_devices
    detail = {"devices": n, "expected": expect_devices}
    if expect_model:
        models = {d.model for d in ir.devices}
        ok = ok and expect_model in models
        detail["models"] = sorted(models)
        detail["expected_model"] = expect_model
    return {"check": "dut_present", "ok": ok, **detail}


def check_params(netlist: str | Path, expect: dict,
                 rel_tol: float = 1e-6) -> dict:
    ir = from_scs(netlist)
    if not ir.devices:
        return {"check": "params", "ok": False, "reason": "no device"}
    params = ir.devices[0].params
    bad = {}
    for k, v in expect.items():
        got = params.get(k)
        if str(got) != str(v):
            bad[k] = {"expected": v, "got": got}
    return {"check": "params", "ok": not bad, "mismatch": bad}


def check_analyses(raw_dir: str | Path, expect: list[str]) -> dict:
    d = Path(raw_dir)
    have = sorted(p.name for p in d.glob("*") if p.suffix in (".dc", ".tran", ".ac", ".noise")) if d.is_dir() else []
    missing = [e for e in expect if not any(h.startswith(e) for h in have)]
    return {"check": "analyses", "ok": not missing, "have": have, "missing": missing}


def check_result_source(run_json: str | Path, netlist: str | Path) -> dict:
    rj = json.loads(Path(run_json).read_text())
    h = hashlib.sha256(Path(netlist).read_bytes()).hexdigest()[:16]
    ok = rj.get("netlist_hash") == h
    return {"check": "result_source", "ok": ok,
            "run_hash": rj.get("netlist_hash"), "netlist_hash": h}


def check_numeric(values: dict, assertions: dict, rel_tol: float = 1e-3) -> dict:
    bad = {}
    for key, rule in assertions.items():
        got = values.get(key)
        if got is None:
            bad[key] = "missing"
            continue
        lo, hi = rule.get("min"), rule.get("max")
        if lo is not None and got < lo:
            bad[key] = {"got": got, "min": lo}
        if hi is not None and got > hi:
            bad[key] = {"got": got, "max": hi}
    return {"check": "numeric", "ok": not bad, "violations": bad}


MANDATORY = ("dut_present", "params", "analyses", "result_source")


def completeness(checks: list[dict], required: tuple = MANDATORY) -> dict:
    """必选检查完整性：NOT_RUN/UNKNOWN/空指标 一律不通过。"""
    missing = [c["check"] for c in checks if c["check"] in required]
    missing = [r for r in required if r not in missing]
    unknown = [c["check"] for c in checks
               if c.get("ok") is None or c.get("ok") == "UNKNOWN"]
    not_run = [c["check"] for c in checks if c.get("status") == "NOT_RUN"]
    empty = [c["check"] for c in checks if c.get("empty")]
    ok = not (missing or unknown or not_run or empty)
    return {"check": "completeness", "ok": ok, "missing": missing,
            "unknown": unknown, "not_run": not_run, "empty": empty}


def param_gate(param_check: dict) -> str:
    """参数不一致时，仿真数值只能作诊断，不能作设计验收。"""
    return "DIAGNOSTIC_ONLY" if not param_check.get("ok") else "ACCEPTABLE"


def classify(process_ok: bool, eval_ok: bool, metrics_ok: bool | None) -> str:
    if not process_ok:
        return "PROCESS_FAILED"
    if not eval_ok:
        return "EVALUATION_INVALID"
    if metrics_ok is None:
        return "EVALUATED_UNVERIFIED"
    return "METRICS_MET" if metrics_ok else "METRICS_FAILED"


def run_all(netlist, raw_dir, run_json, expect_params, assertions,
            analyses=("op",)) -> dict:
    checks = [
        check_dut(netlist, 1, "nch"),
        check_params(netlist, expect_params),
        check_analyses(raw_dir, list(analyses)),
        check_result_source(run_json, netlist),
    ]
    ok_map = {c["check"]: c["ok"] for c in checks}
    eval_ok = all(ok_map.values())
    vals = {}
    cands = sorted(Path(raw_dir).glob("*.dc"))
    p = next((c for c in cands if c.name.startswith("op")), cands[0] if cands else None)
    if p is not None and p.is_file():
        from vlight import psf
        plot = psf.read_psf(p)
        vals = {k: float(plot.y[k][0]) for k in plot.y}
    complete = completeness(checks)
    checks.append(complete)
    if not complete["ok"] or not eval_ok:
        return {"checks": checks, "eval_ok": False, "status": "EVALUATION_INVALID",
                "values": vals, "gate": param_gate(checks[1])}
    num = check_numeric(vals, assertions or {})
    checks.append(num)
    gate = param_gate(checks[1])
    status = classify(True, eval_ok, num.get("ok")) if gate == "ACCEPTABLE" \
        else "DIAGNOSTIC_ONLY"
    return {"checks": checks, "eval_ok": eval_ok, "status": status,
            "values": vals, "gate": gate}
