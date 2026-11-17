#!/usr/bin/env python3
"""按 operation_id 从注册表分发能力（当前仅 simulate.run_unified）。"""
import argparse, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
REG = os.path.join(ROOT, "registry", "engineering_operations.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--operation", required=True)
    ap.add_argument("--request", required=True, help="请求 JSON 文件")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    reg = json.load(open(REG))["operations"]
    op = reg.get(a.operation)
    if op is None:
        print(json.dumps({"operation_id": a.operation, "status": "NOT_REGISTERED"}))
        return 1
    req = json.load(open(a.request))
    rec = {"operation_id": a.operation, "impl": "vskill/phase2/capabilities.py::simulate",
           "request_id": 1, "runner": "vskill.phase2.spectre_runner.SpectreRunner"}
    if a.operation != "simulate.run_unified":
        print(json.dumps(dict(rec, status="NOT_DISPATCHABLE"), ensure_ascii=False))
        return 1
    from vskill.phase2.capabilities import simulate
    from vskill.phase2.spectre_runner import SpectreRunner
    runner = SpectreRunner(req.get("run_root", "runs"), timeout=180, host="eda-220")
    res = simulate(req, runner)
    rec.update({"run_id": req.get("run_id"), "status": res["execution_status"]})
    out = {"trace": rec, "result": res}
    if a.out:
        json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:1500])
    return 0 if res["execution_status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
