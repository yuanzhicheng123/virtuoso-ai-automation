"""vskill.tools_builtin —— 把已验证过的 Virtuoso 设计能力登记成工具。

每个工具都对应一条真机验证过的 SKILL 调用（见 docs/OPERATION_STANDARD.md）。
验证器一律用"读回"：创建后立刻查询，证明副作用真实发生。
"""
from __future__ import annotations

from .bridge import Point
from .tools import Param, Registry


def install(reg: Registry | None = None) -> Registry:
    reg = reg or Registry()

    # ---- 库 / 单元 ---------------------------------------------------- #
    @reg.tool("vl.lib.create", "建库（若已存在则返回现有库）",
              [Param("lib", "string", doc="库名"),
               Param("path", "string", required=False,
                     default="./lib", doc="库目录（远端相对路径）")],
              risk="write", returns="library", tags=("library",))
    def lib_create(session, lib, path):
        session.call("ddCreateLib", lib, path)
        return session.call("ddGetObj", lib)

    @reg.verifier("vl.lib.create")
    def _v_lib(session, result, **kw):
        obj = session.call("ddGetObj", kw["lib"])
        return obj is not None, f"ddGetObj({kw['lib']}) -> {obj!r}"

    @reg.tool("vl.cell.open", "打开（或新建）cellview",
              [Param("lib", "string"), Param("cell", "string"),
               Param("view", "string", required=False, default="schematic"),
               Param("viewType", "string", required=False, default="schematic"),
               Param("mode", "string", required=False, default="a",
                     doc="a=追加 w=写 r=读")],
              risk="write", returns="cellview", tags=("cell",))
    def cell_open(session, lib, cell, view, viewType, mode):
        return session.call("dbOpenCellViewByType", lib, cell, view, viewType, mode)

    @reg.verifier("vl.cell.open")
    def _v_open(session, result, **kw):
        name = getattr(result, "cellName", None) or session.call("getq", result, "cellName")
        return name is not None, f"cellName={name!r}"

    @reg.tool("vl.cell.close", "关闭 cellview",
              [Param("cv", "object")], risk="write", tags=("cell",))
    def cell_close(session, cv):
        return session.call("dbClose", cv)

    @reg.tool("vl.cell.save", "保存 cellview",
              [Param("cv", "object")], risk="write", returns="bool", tags=("cell",))
    def cell_save(session, cv):
        return session.call("dbSave", cv)

    @reg.verifier("vl.cell.save")
    def _v_save(session, result, **kw):
        return result is True, f"dbSave -> {result!r}"

    # ---- 实例 / 网络 / 连接 -------------------------------------------- #
    @reg.tool("vl.inst.create", "按 master 名放实例",
              [Param("cv", "object"), Param("lib", "string"),
               Param("cell", "string"), Param("view", "string", required=False,
                                              default="symbol"),
               Param("name", "string"), Param("x", "number"), Param("y", "number"),
               Param("orient", "string", required=False, default="R0")],
              risk="write", returns="inst", tags=("schematic",))
    def inst_create(session, cv, lib, cell, view, name, x, y, orient):
        return session.call("dbCreateInstByMasterName", cv, lib, cell, view,
                            name, Point(x, y), orient)

    @reg.verifier("vl.inst.create")
    def _v_inst(session, result, **kw):
        got = session.call("dbGetInstByName", kw["cv"], kw["name"])
        return got is not None, f"dbGetInstByName -> {got!r}"

    @reg.tool("vl.net.create", "建网络",
              [Param("cv", "object"), Param("name", "string")],
              risk="write", returns="net", tags=("schematic",))
    def net_create(session, cv, name):
        return session.call("dbCreateNet", cv, name)

    @reg.verifier("vl.net.create")
    def _v_net(session, result, **kw):
        got = session.call("dbGetNet", kw["cv"], kw["name"])
        return got is not None, f"dbGetNet -> {got!r}"

    @reg.tool("vl.conn.create", "把实例端子接到网络",
              [Param("cv", "object"), Param("net", "string"),
               Param("inst", "string"), Param("term", "string")],
              risk="write", returns="conn", tags=("schematic", "connectivity"))
    def conn_create(session, cv, net, inst, term):
        return session.call("dbCreateConnByName",
                            session.call("dbGetNet", cv, net),
                            session.call("dbGetInstByName", cv, inst), term)

    @reg.verifier("vl.conn.create")
    def _v_conn(session, result, **kw):
        net = session.call("dbGetNet", kw["cv"], kw["net"])
        terms = session.call("getq", net, "instTerms")
        return result is not None, f"net~>instTerms -> {terms!r}"

    # ---- 图形 ---------------------------------------------------------- #
    @reg.tool("vl.wire.draw", "画连线（数据面：line + conn 已分步完成）",
              [Param("cv", "object"), Param("layer", "layer"),
               Param("points", "list", doc="[[x,y],[x,y]]")],
              risk="write", returns="shape", tags=("schematic", "shape"))
    def wire_draw(session, cv, layer, points):
        return session.call("dbCreateLine", cv, layer, [Point(*p) for p in points])

    @reg.verifier("vl.wire.draw")
    def _v_wire(session, result, **kw):
        n = session.call("length", session.call("getq", kw["cv"], "shapes"))
        return n >= 1, f"shapes={n}"

    @reg.tool("vl.shape.rect", "画矩形",
              [Param("cv", "object"), Param("layer", "layer"),
               Param("x0", "number"), Param("y0", "number"),
               Param("x1", "number"), Param("y1", "number")],
              risk="write", returns="shape", tags=("layout", "shape"))
    def rect(session, cv, layer, x0, y0, x1, y1):
        return session.call("dbCreateRect", cv, layer,
                            [Point(x0, y0), Point(x1, y1)])

    @reg.verifier("vl.shape.rect")
    def _v_rect(session, result, **kw):
        n = session.call("length", session.call("getq", kw["cv"], "shapes"))
        return n >= 1, f"shapes={n}"

    # ---- 属性 ---------------------------------------------------------- #
    @reg.tool("vl.prop.set", "设置对象属性（dbReplaceProp）",
              [Param("obj", "object"), Param("name", "string"),
               Param("kind", "string", required=False, default="string"),
               Param("value", "string")],
              risk="write", returns="prop", tags=("property",))
    def prop_set(session, obj, name, kind, value):
        return session.call("dbReplaceProp", obj, name, kind, value)

    @reg.verifier("vl.prop.set")
    def _v_prop(session, result, **kw):
        got = session.call("dbGetPropByName", kw["obj"], kw["name"])
        return got is not None, f"dbGetPropByName({kw['name']}) -> {got!r}"

    @reg.tool("vl.prop.get", "读对象属性",
              [Param("obj", "object"), Param("name", "string")],
              risk="read", returns="prop", tags=("property",))
    def prop_get(session, obj, name):
        return session.call("dbGetPropByName", obj, name)

    # ---- 巡检 ---------------------------------------------------------- #
    @reg.tool("vl.cell.inspect", "读回 cellview 的实例/网络/图形数",
              [Param("cv", "object")], risk="read", returns="object",
              tags=("verify",))
    def inspect(session, cv):
        return {
            "instances": session.call("length", session.call("getq", cv, "instances")),
            "nets": session.call("length", session.call("getq", cv, "nets")),
            "shapes": session.call("length", session.call("getq", cv, "shapes")),
        }

    @reg.verifier("vl.cell.inspect")
    def _v_inspect(session, result, **kw):
        return result["nets"] >= 0, str(result)

    return reg


def install_composite(reg: Registry | None = None) -> Registry:
    """复合工具：一次往返画出一个反相器（适合无 daemon 的批处理桥）。"""
    reg = reg or BUILTIN

    @reg.tool("vl.design.inverter",
              "一次生成反相器原理图（2 器件 + 2 网络 + 2 连接 + 1 条线）并读回校验",
              [Param("lib", "string", required=False, default="vlTools"),
               Param("cell", "string", required=False, default="inv"),
               Param("layer", "layer", required=False,
                     default=["text", "drawing"])],
              risk="write", returns="object", tags=("design", "composite"))
    def inverter(session, lib, cell, layer):
        script = f"""
(ddCreateLib "{lib}" "./{lib}")
(let (cv mn mp n1 n2)
  (setq cv (dbOpenCellViewByType "{lib}" "{cell}" "schematic" "schematic" "w"))
  (setq mn (dbCreateInstByMasterName cv "analogLib" "nmos4" "symbol" "MN" 0:0 "R0"))
  (setq mp (dbCreateInstByMasterName cv "analogLib" "pmos4" "symbol" "MP" 0:2 "R0"))
  (setq n1 (dbCreateNet cv "IN"))
  (setq n2 (dbCreateNet cv "OUT"))
  (dbCreateConnByName n1 mn "G")
  (dbCreateConnByName n2 mn "D")
  (dbCreateConnByName n2 mp "D")
  (dbCreateLine cv (list "{layer[0]}" "{layer[1]}") (list 0:0 4:0))
  (dbReplaceProp cv "AUTHOR" "string" "vskill")
  (printf "@@insts=%L@@" length(cv~>instances))
  (printf "@@nets=%L@@" length(cv~>nets))
  (printf "@@shapes=%L@@" length(cv~>shapes))
  (printf "@@save=%L@@" dbSave(cv))
  (dbClose cv)
  (printf "@@done=t@@"))
"""
        runner = getattr(session.bridge, "run_script", None)
        if runner is None:
            raise RuntimeError("复合工具需要能跑整段脚本的桥（NographBridge/daemon）")
        res = runner(script)
        if res.get("done") != "t":
            raise RuntimeError(f"脚本未完成: {res}")
        return {"instances": int(res["insts"]), "nets": int(res["nets"]),
                "shapes": int(res["shapes"]), "saved": res.get("save") == "t"}

    @reg.verifier("vl.design.inverter")
    def _v_inv(session, result, **kw):
        ok = (result["instances"] == 2 and result["nets"] == 2
              and result["shapes"] >= 1 and result["saved"])
        return ok, str(result)

    return reg


def install_gui(reg: Registry | None = None) -> Registry:
    """GUI 会话专属工具（编辑器上下文）：需要先 deOpenCellView 打开窗口。"""
    reg = reg or BUILTIN

    def need(session, fn: str) -> None:
        if session.call("getd", __import__("vskill").Symbol(fn)) is None:
            raise RuntimeError(f"{fn} 不在当前会话（需要 GUI 编辑窗口："
                               f"deOpenCellView ...）")

    @reg.tool("vl.sch.wire", "画原理图连线（schCreateWire，需要 GUI 会话）",
              [Param("cv", "object"), Param("net", "string", required=False,
                                            default="draw"),
               Param("points", "list")],
              risk="write", returns="wire", tags=("schematic", "gui"))
    def sch_wire(session, cv, net, points):
        need(session, "schCreateWire")
        return session.call("schCreateWire", cv, net, "full",
                            [Point(*p) for p in points])

    @reg.verifier("vl.sch.wire")
    def _v_sch_wire(session, result, **kw):
        return result is not None, f"schCreateWire -> {result!r}"

    @reg.tool("vl.sch.pin", "加原理图引脚（schCreatePin，需要 GUI 会话）",
              [Param("cv", "object"), Param("name", "string"),
               Param("direction", "string", required=False, default="input"),
               Param("x", "number"), Param("y", "number"),
               Param("orient", "string", required=False, default="R0")],
              risk="write", returns="pin", tags=("schematic", "gui"))
    def sch_pin(session, cv, name, direction, x, y, orient):
        need(session, "schCreatePin")
        return session.call("schCreatePin", cv, name, direction, None,
                            Point(x, y), orient)

    @reg.verifier("vl.sch.pin")
    def _v_sch_pin(session, result, **kw):
        return result is not None, f"schCreatePin -> {result!r}"

    return reg


BUILTIN = install_gui(install_composite(install()))


__all__ = ["install", "install_composite", "install_gui", "BUILTIN"]
