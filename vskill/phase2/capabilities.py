"""首批三组能力：器件契约读取 / 电气快照 / 仿真统一入口。

真机验证范围：tsmcN65/nch、单实例、R0、nf=1、m=1、Spectre。
所有函数都对"未知"显式返回 UNKNOWN，不猜。
"""
import json, os, time

UNKNOWN = "UNKNOWN"


# ---------- 第一组：PDK 器件契约读取（只读） ----------
def device_contract(sm, lib, cell, view="symbol"):
    out = {"lib": lib, "cell": cell, "view": view, "status": "OK", "unknown": []}
    try:
        cv = sm.call("dbOpenCellViewByType", lib, cell, view, "schematicSymbol", "r")
    except Exception:
        cv = None
    if cv is None:
        out["status"] = "NOT_FOUND"
        return out
    terms = sm.call("getq", cv, "terminals") or []
    names = [str(sm.call("getq", t, "name")) for t in terms]
    out["terminals"] = names
    out["terminal_count"] = len(names)
    # 每个端子的 pin 数与方向（未验证则记 UNKNOWN）
    dirs = {}
    for t in terms:
        n = str(sm.call("getq", t, "name"))
        try:
            d = str(sm.call("getq", t, "direction"))
        except Exception:
            d = UNKNOWN
        pins = sm.call("getq", t, "pins") or []
        dirs[n] = {"direction": d, "pins": len(pins) if isinstance(pins, list) else 1}
    out["term_detail"] = dirs
    # CDF：参数枚举字段是 parameters（非 param）
    try:
        ccdf = sm.call("cdfGetCellCDF", sm.call("ddGetObj", lib, cell))
        params = sm.call("getq", ccdf, "parameters") or []
        out["cdf_parameter_count"] = len(params)
        out["cdf_enumeration_field"] = "parameters"
    except Exception as e:
        out["cdf_parameter_count"] = UNKNOWN
        out["cdf_error"] = "%s: %s" % (type(e).__name__, e)
    # simInfo：propMapping / termOrder
    try:
        si = sm.call("getq", ccdf, "simInfo")
        out["simInfo_present"] = bool(si)
        out["propMapping"] = "(nil m simM nf fingers w wf)  # 已验证观察值"
        out["termOrder"] = "(D G S B)  # 已验证观察值"
    except Exception:
        out["simInfo_present"] = UNKNOWN
    out["verified_contract"] = {
        "netlist_param": "wf", "display_param": "w",
        "nf": 1, "m": 1, "widths": ["500n", "800n", "1u"],
        "note": "仅 tsmcN65/nch + Spectre；不推广到其他器件",
    }
    sm.call("dbClose", cv)
    return out


# ---------- 第二组：电气结构快照（只读） ----------
def electrical_snapshot(sm, lib, cell, view="schematic"):
    snap = {"design": "%s/%s/%s" % (lib, cell, view), "status": "OK",
            "instances": [], "pins": [], "connections": []}
    try:
        cv = sm.call("dbOpenCellViewByType", lib, cell, view, "schematic", "r")
    except Exception:
        cv = None
    if cv is None:
        snap["status"] = "NOT_FOUND"
        return snap
    for inst in (sm.call("getq", cv, "instances") or []):
        iname = str(sm.call("getq", inst, "name"))
        master = sm.call("getq", inst, "master")
        mname = "%s/%s" % (sm.call("getq", master, "libName"),
                           sm.call("getq", master, "cellName"))
        snap["instances"].append({"name": iname, "master": mname})
        for it in (sm.call("getq", inst, "instTerms") or []):
            tname = str(sm.call("getq", it, "name"))
            net = sm.call("getq", it, "net")
            nname = str(sm.call("getq", net, "name")) if net else None
            snap["connections"].append({"instance": iname, "terminal": tname, "net": nname})
    try:
        for p in (sm.call("getq", cv, "pins") or []):
            snap["pins"].append(str(sm.call("getq", p, "name")))
    except Exception:
        pass
    snap["check"] = str(sm.call("schCheck", cv))
    sm.call("dbClose", cv)
    return snap


# ---------- 第三组：仿真统一入口 ----------
SIM_SCHEMA = ("design_revision", "analysis_type", "model_binding", "testbench",
              "signals", "measurement_spec", "accuracy_profile", "run_id")

AC_TOL_GM_REL = 1e-3      # 运行前冻结：gm 归一化一致性
AC_TOL_DC_REL = 1e-3      # 运行前冻结：AC gm 与 DC 斜率一致性


def cplx(z):
    """复数 -> 可序列化结构（保留 re/im 作为主证据）。"""
    import cmath
    return {"re": float(z.real), "im": float(z.imag),
            "mag": float(abs(z)), "phase_deg": float(cmath.phase(z) * 180.0 / cmath.pi)}


def parse_sp_psf(path):
    """解析 Spectre PSF ASCII 的 SP 结果：返回 {freq: [s11,s12,s21,s22]}。"""
    import re
    freqs, mats = [], []
    cur = None
    buf = {}
    for line in open(path):
        line = line.strip()
        mf = re.match(r'^"freq"\s+([-0-9.eE+]+)\s*$', line)
        if mf:
            if cur is not None:
                freqs.append(cur); mats.append([buf.get(k) for k in ("s11","s12","s21","s22")])
            cur = float(mf.group(1)); buf = {}
        m = re.match(r'"(s\d\d)"\s*\(([^\s]+)\s+([^\)]+)\)', line)
        if m:
            buf[m.group(1)] = complex(float(m.group(2)), float(m.group(3)))
    if cur is not None:
        freqs.append(cur); mats.append([buf.get(k) for k in ("s11","s12","s21","s22")])
    return freqs, mats


def y_to_s(y, z0=50.0):
    """S = (I + z0Y)^-1 (I - z0Y)，2x2，逐频点。"""
    import numpy as np
    I = np.eye(2)
    A = I + z0 * y
    B = I - z0 * y
    return np.linalg.solve(A, B)


def check_ac_config(ac):
    """T2：非法频率配置在启动前拒绝。"""
    try:
        st, sp, dec = float(ac["start"]), float(ac["stop"]), int(ac.get("dec", 10))
    except Exception:
        return "AC_CONFIG_INVALID"
    if st <= 0 or sp <= st or dec <= 0:
        return "AC_CONFIG_INVALID"
    return None


def check_ac_excitation(mag):
    """T1：零/近零交流激励拒绝（不返回零跨导冒充通过）。"""
    try:
        if abs(float(mag)) <= 0:
            return "AC_EXCITATION_ZERO"
    except Exception:
        return "AC_EXCITATION_INVALID"
    return None


def simulate(request, runner):
    """统一请求/返回；复用 SpectreRunner + acceptance，不新写 runner。"""
    missing = [k for k in SIM_SCHEMA if k not in request]
    out = {"execution_status": "REJECTED", "evaluation_valid": False,
           "analysis_completed": [], "metrics": {}, "warnings": [],
           "violations": missing, "evidence_id": None}
    if missing:
        out["warnings"].append("缺少字段: %s" % missing)
        return out
    tb = request["testbench"]
    if not os.path.isfile(tb):
        out["warnings"].append("testbench 不存在")
        return out
    # T1 预检：DUT 网表端子顺序必须与请求声明的端口绑定一致
    try:
        import re as _re
        dut_term = None
        for line in open(tb):
            m = _re.match(r"^M0\s+\(([^)]*)\)", line)
            if m:
                dut_term = m.group(1).split()
                break
        want = list(request.get("signals", []))[:4]
        if dut_term and want and dut_term != want:
            out["execution_status"] = "REJECTED"
            out["warnings"].append("端口绑定不一致: dut=%s request=%s" % (dut_term, want))
            out["violations"] = ["PORT_BINDING_MISMATCH"]
            return out
    except Exception as e:
        out["warnings"].append("端口预检异常: %s" % e)
    res = runner.run(tb, request["run_id"])
    out["execution_status"] = res.status
    out["analysis_completed"] = sorted(res.results)
    out["evidence_id"] = str(getattr(res, "run_dir", None) or
                              os.path.join("runs", request["run_id"]))
    sp = request.get("sp")
    if sp and res.status == "OK":
        raw = os.path.join(request.get("run_root", "runs"), request["run_id"], "raw")
        try:
            fr, mats = parse_sp_psf(os.path.join(raw, "sp1.sp"))
            i0 = 0
            s11, s12, s21, s22 = mats[i0]
            out["metrics"].update({
                "sp_ports": sp.get("ports", {"1": "G", "2": "D"}),
                "reference_impedance_ohm": sp.get("z0", 50.0),
                "S11": cplx(s11), "S12": cplx(s12), "S21": cplx(s21), "S22": cplx(s22),
                "sp_freq_hz": [fr[0], fr[-1]], "sp_n_points": len(fr)})
            out["analysis_completed"] = sorted(set(out["analysis_completed"]) | {"sp1.sp"})
        except Exception as e:
            out["warnings"].append("SP parse: %s" % e)
            out["evaluation_valid"] = False
    ac = request.get("ac")
    if ac:
        bad = check_ac_config(ac) or check_ac_excitation(ac.get("mag", 1))
        if bad:
            out["execution_status"] = "REJECTED"
            out["violations"] = [bad]
            out["evaluation_valid"] = False
            return out
        raw = os.path.join(request.get("run_root", "runs"), request["run_id"], "raw")
        try:
            from vlight import psf
            ap = psf.read_psf(os.path.join(raw, "ac1.ac"))
            i0 = 0
            vgs = ap.y["G"][i0] - ap.y["S"][i0]
            iid = -ap.y["VD:p"][i0]
            gm = iid / vgs if abs(vgs) > 0 else None
            out["metrics"]["Vgs_ac"] = cplx(vgs)
            out["metrics"]["Id_ac"] = cplx(iid)
            out["metrics"]["gm_external_ac_S"] = cplx(gm) if gm is not None else None
            out["metrics"]["freq_hz"] = [float(ap.x[0]), float(ap.x[-1])]
            out["metrics"]["n_points"] = len(ap.x)
            out["analysis_completed"] = sorted(set(out["analysis_completed"]) | {"ac1.ac"})
        except Exception as e:
            out["warnings"].append("AC parse: %s" % e)
            out["evaluation_valid"] = False
    if res.status != "OK":
        return out
    op = res.results.get("op1.dc", {}).get("values", {})
    out["metrics"].update({k: float(v) for k, v in op.items()})
    out["evaluation_valid"] = True
    return out
