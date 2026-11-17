"""通用原理图构建/连接/校验（声明式 spec 驱动，不针对具体 cell 硬编码）。

spec 结构（JSON）：
{
  "lib": "...", "cell": "...", "view": "schematic",
  "instances": [{"name":"M0","lib":"tsmcN65","cell":"nch","view":"symbol",
                 "x":0,"y":0,"params":{"wf":"500n","w":"500n"}}],
  "nets": ["G","D","S","B","gnd"],
  "conns": [["G","M0","G"], ...],
  "wires": [["M0","G","VG","PLUS"], ...]
}
"""
import json


class SpecError(Exception):
    pass


def _prop(obj, name):
    return None


def terminal_point(sm, inst, tname):
    """任意 master/terminal 的引脚中心（master 坐标 -> 父 cellview 坐标）。"""
    master = sm.call("getq", inst, "master")
    term = sm.call("dbFindTermByName", master, tname)
    if term is None:
        raise SpecError("terminal %r 不存在于 %s" % (tname, master))
    pins = sm.call("getq", term, "pins") or []
    if not isinstance(pins, (list, tuple)):
        pins = [pins]
    if not pins:
        raise SpecError("terminal %r 无 pin 图形" % tname)
    fig = sm.call("getq", pins[0], "fig")
    bb = sm.call("getq", fig, "bBox")
    (x0, y0), (x1, y1) = bb[0], bb[1]
    mpt = [(x0 + x1) / 2.0, (y0 + y1) / 2.0]
    return sm.call("dbTransformPoint", mpt, sm.call("getq", inst, "transform"))


def check_spec(spec):
    for k in ("lib", "cell"):
        if not spec.get(k):
            raise SpecError("缺少 %s" % k)
    names = set()
    for i in spec.get("instances", []):
        for k in ("name", "lib", "cell", "view"):
            if not i.get(k):
                raise SpecError("instance 缺字段 %s: %r" % (k, i))
        if i["name"] in names:
            raise SpecError("instance 重名: %s" % i["name"])
        names.add(i["name"])
    for net, inst, term in spec.get("conns", []):
        if inst not in names:
            raise SpecError("conns 引用未知实例 %s" % inst)
    for a, ta, b, tb in spec.get("wires", []):
        for inst, term in ((a, ta), (b, tb)):
            if inst not in names:
                raise SpecError("wires 引用未知实例 %s" % inst)
    return spec


def build(sm, spec, mode="w"):
    """建实例 → 建网络 → 绑定端子 → 画导线。返回 cellview 句柄。"""
    check_spec(spec)
    if spec.get("create_lib"):
        path = spec.get("lib_path") or ("/tmp/%s" % spec["lib"])
        try:
            sm.call("ddCreateLib", spec["lib"], path)
        except Exception:
            pass                      # 已存在
    cv = sm.call("dbOpenCellViewByType", spec["lib"], spec["cell"], spec["view"],
                 spec["view"], mode)
    if cv is None:
        raise SpecError("无法打开 %s/%s/%s" % (spec["lib"], spec["cell"], spec["view"]))
    insts = {}
    for i in spec.get("instances", []):
        inst = sm.call("dbCreateInstByMasterName", cv, i["lib"], i["cell"],
                       i["view"], i["name"], [i.get("x", 0), i.get("y", 0)], "R0")
        insts[i["name"]] = inst
        for k, v in (i.get("params") or {}).items():
            sm.call("dbReplaceProp", inst, k, "string", v)
    nets = {}
    for n in spec.get("nets", []):
        nets[n] = sm.call("dbCreateNet", cv, n)
    for net, inst, term in spec.get("conns", []):
        sm.call("dbCreateConnByName", nets[net], insts[inst], term)
    wires = []
    for a, ta, b, tb in spec.get("wires", []):
        pa = terminal_point(sm, insts[a], ta)
        pb = terminal_point(sm, insts[b], tb)
        wires.append(sm.call("schCreateWire", cv, "draw", "full", [pa, pb], 0.0, 0.0, 0.125))
    return cv, insts, nets, wires


def verify(sm, cv, expect_wires=None):
    """验证：schCheck 计数、markers、每实例端子到网络名、导线数。"""
    res = sm.call("schCheck", cv)
    markers = []
    try:
        for m in (sm.call("getq", cv, "markers") or []):
            markers.append(str(sm.call("getq", m, "msg")).strip())
    except Exception:
        pass
    conns = []
    for inst in (sm.call("getq", cv, "instances") or []):
        iname = str(sm.call("getq", inst, "name"))
        for it in (sm.call("getq", inst, "instTerms") or []):
            net = sm.call("getq", it, "net")
            conns.append({"instance": iname,
                          "terminal": str(sm.call("getq", it, "name")),
                          "net": str(sm.call("getq", net, "name")) if net else None})
    try:
        n_wires = int(sm.call("length", sm.call("getq", cv, "wires")))
    except Exception:
        n_wires = None
    return {"check": str(res), "markers": markers,
            "connections": sorted(conns, key=lambda c: (c["instance"], c["terminal"])),
            "wires": n_wires,
            "wires_ok": (n_wires == expect_wires) if expect_wires is not None else None}
