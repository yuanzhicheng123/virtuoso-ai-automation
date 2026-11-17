"""V0.1：单 PDK / 单器件 / 单入口 的原理图→网表→Spectre→验收 任务入口。

设计约束（本轮冻结）：
  PDK=tsmcN65, device=tsmcN65/nch, simulator=spectre, nf=1, m=1,
  width ∈ {500n, 800n, 1u}, 隔离库名必须以 vlV01_ 开头。

入口：python3 tools/v01_run.py --config cfg.json --workdir /tmp/v01_run
"""
import json, os, re, sys, time, hashlib, subprocess

WIDTHS = ("500n", "800n", "1u")
ISM = {"nf": 1, "m": 1, "fingers": 1, "simM": 1}
FORBIDDEN = ("fingers", "simM", "nf", "m")
DEVICE = "tsmcN65/nch"
SIM = "spectre"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class Reject(Exception):
    pass


def validate_config(cfg):
    lib = cfg.get("lib", "")
    if not lib.startswith("vlV01_"):
        raise Reject("非隔离库名（必须以 vlV01_ 开头）: %r" % lib)
    for k in ("lib", "cell"):
        if not NAME.match(cfg.get(k, "")):
            raise Reject("非法名称 %s=%r" % (k, cfg.get(k)))
    if cfg.get("device", DEVICE) != DEVICE:
        raise Reject("器件不在契约内: %r" % cfg.get("device"))
    if cfg.get("simulator", SIM) != SIM:
        raise Reject("仿真器不在契约内: %r" % cfg.get("simulator"))
    w = str(cfg.get("width", ""))
    if w not in WIDTHS:
        raise Reject("宽度不在已验证集合 %s: %r" % (list(WIDTHS), w))
    for k in FORBIDDEN:
        if cfg.get(k) not in (None, ISM[k]):
            raise Reject("受保护参数 %s=%r 未验证（仅允许 %r）" % (k, cfg.get(k), ISM[k]))
    extra = set(cfg.get("params", {}))
    if extra - {"l", "w", "wf"}:
        raise Reject("参数不在契约内: %s" % sorted(extra - {"l", "w", "wf"}))
    return cfg


def guarded_write(sm, cv, inst, width):
    """契约 → 写 wf/w → 回读 → 一致性判定。"""
    validate_config({"lib": "vlV01_x", "cell": "x", "width": width,
                     "device": DEVICE, "simulator": SIM})
    sm.call("dbReplaceProp", inst, "wf", "string", width)
    sm.call("dbReplaceProp", inst, "w", "string", width)
    got = {"wf": str(sm.call("getq", inst, "wf")), "w": str(sm.call("getq", inst, "w"))}
    if got["wf"] != width or got["w"] != width:
        raise Reject("写入回读不一致: %r != %r" % (got, width))
    return got


def evidence_check(sm, cv):
    """会话内取证：计数 + markers 的消息与对象。"""
    res = sm.call("schCheck", cv)
    markers = []
    try:
        raw = sm.call("getq", cv, "markers")
        for m in (raw or []):
            item = {"msg": str(sm.call("getq", m, "msg"))}
            objs = []
            try:
                for o in (sm.call("getq", m, "objects") or []):
                    objs.append(str(sm.call("getq", o, "name")))
            except Exception:
                pass
            item["objects"] = objs
            markers.append(item)
    except Exception as e:
        markers = [{"error": "%s: %s" % (type(e).__name__, e)}]
    return {"counts": str(res), "markers": markers}


def netlist(cfg, workdir, cds_dir="/tmp/sb9"):
    ocn = os.path.join(workdir, "netlist.ocn")
    with open(ocn, "w") as f:
        f.write("simulator('spectre)\ndesign(\"%s\" \"%s\" \"schematic\")\n"
                "createNetlist(?recreateAll t)\nexit\n" % (cfg["lib"], cfg["cell"]))
    env = dict(os.environ, HOME=workdir)
    env.pop("DISPLAY", None)
    src = os.path.join(cds_dir, "cds.lib")
    if os.path.isfile(src):
        open(os.path.join(workdir, "cds.lib"), "w").write(open(src).read())
    subprocess.call("cd %s && cp %s/netlist.ocn . && %s -nograph < netlist.ocn > %s/netlist.log 2>&1" %
                    (cds_dir, workdir, "/eda/cds/IC2310/tools.lnx86/dfII/bin/ocean", workdir),
                    shell=True, env=env)
    p = os.path.join(workdir, "simulation", cfg["cell"], "spectre",
                     "schematic", "netlist", "input.scs")
    return p if os.path.isfile(p) else None


def device_line(path):
    for line in open(path):
        if line.startswith("M"):
            return line.strip()
    return ""


def main(argv=None):
    argv = argv or sys.argv[1:]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args(argv)
    cfg = json.load(open(a.config))
    validate_config(cfg)                      # A：入口即校验
    sys.path.insert(0, "/tmp")
    from skillbridge import Workspace
    from session_manager import SessionManager
    sm = SessionManager(lambda: Workspace.open("default"), timeout=10,
                        log_path=os.path.join(a.workdir, "requests.log"))
    os.makedirs(a.workdir, exist_ok=True)
    ev = {"config": cfg, "run_id": time.strftime("%Y%m%dT%H%M%S"), "steps": []}

    def step(name, fn):
        try:
            v = fn()
            ev["steps"].append({"step": name, "ok": True, "value": str(v)[:90]})
            return v
        except Exception as e:
            ev["steps"].append({"step": name, "ok": False,
                                "error": "%s: %s" % (type(e).__name__, e)})
            raise

    try:
        step("lib", lambda: sm.call("ddCreateLib", cfg["lib"], "./" + cfg["lib"]))
        cv = step("open", lambda: sm.call("dbOpenCellViewByType", cfg["lib"],
                                          cfg["cell"], "schematic", "schematic", "w"))
        inst = step("inst", lambda: sm.call("dbCreateInstByMasterName", cv,
                                            "tsmcN65", "nch", "symbol", "M0",
                                            [0, 0], "R0"))
        nets = {}
        for net in ("D", "G", "S", "B"):
            nets[net] = step("net_" + net, lambda n=net: sm.call("dbCreateNet", cv, n))
        for term in ("D", "G", "S", "B"):
            step("conn_" + term, lambda t=term: sm.call(
                "dbCreateConnByName", nets[t], inst, t))
        step("width", lambda: guarded_write(sm, cv, inst, cfg["width"]))
        chk = step("schCheck", lambda: evidence_check(sm, cv))
        ev["schCheck"] = chk
        step("save", lambda: sm.call("dbSave", cv))
        step("close", lambda: sm.call("dbClose", cv))
        cv2 = step("reopen", lambda: sm.call("dbOpenCellViewByType", cfg["lib"],
                                             cfg["cell"], "schematic", "schematic", "r"))
        ev["readback"] = {"wf": str(sm.call("getq", cv2, "wf")) if False else None}
        ev["readback"] = {"nets": str(sm.call("length", sm.call("getq", cv2, "nets"))),
                          "insts": str(sm.call("length", sm.call("getq", cv2, "instances")))}
        step("close2", lambda: sm.call("dbClose", cv2))
        nl = step("netlist", lambda: netlist(cfg, a.workdir))
        ev["netlist"] = nl
        ev["device_line"] = device_line(nl) if nl else None
        ok = bool(nl) and ("w=" + cfg["width"]) in (ev["device_line"] or "")
        ev["netlist_w_ok"] = ok
        ev["status"] = "OK" if ok else "FAILED"
    except Exception as e:
        ev["status"] = "FAILED"
        ev["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            sm.close()
        except Exception:
            pass
        path = os.path.join(a.workdir, "evidence.json")
        with open(path, "w") as f:
            json.dump(ev, f, ensure_ascii=False, indent=1)
        print(json.dumps({"status": ev.get("status"), "evidence": path,
                          "device_line": ev.get("device_line"),
                          "schCheck": ev.get("schCheck", {}).get("counts")},
                         ensure_ascii=False))
    return 0 if ev.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
