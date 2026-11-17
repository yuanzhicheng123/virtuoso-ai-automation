"""vskill.design —— 用 Python 描述"要画什么"，再生成 SKILL 去执行。

只封装**已确认真机可用**的 db*/sch* 调用，类型按签名来：
    Point(x, y)            → 0:0
    [(x1,y1), (x2,y2)]     → list(0:0 1:0)
    LayerPurpose("M1")     → list("M1" "drawing")

用法：
    d = Design(bridge)
    lib = d.library("MYLIB", "/tmp/my/lib")
    sch = d.schematic("MYLIB", "inv")
    sch.inst("analogLib", "nmos4", "symbol", "M0", Point(0, 0))
    sch.net("IN"); sch.term("IN", "M0", "G")
    print(sch.script())          # 生成的 SKILL
    print(sch.run())             # 真机执行 + 读回
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bridge import Point, Symbol, to_skill


@dataclass
class LayerPurpose:
    layer: str
    purpose: str = "drawing"

    def __str__(self) -> str:
        return '(list "%s" "%s")' % (self.layer, self.purpose)


def _lp(value) -> str:
    if isinstance(value, LayerPurpose):
        return str(value)
    if isinstance(value, str):
        return str(LayerPurpose(value))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(LayerPurpose(value[0], value[1]))
    raise TypeError(f"层/用途不对: {value!r}")


def _pt(value) -> str:
    if isinstance(value, Point):
        return to_skill(value)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return to_skill(Point(value[0], value[1]))
    raise TypeError(f"点不对: {value!r}")


def _pts(values) -> str:
    return "(list " + " ".join(_pt(v) for v in values) + ")"


@dataclass
class Op:
    code: str
    note: str = ""


class Cell:
    """一段 SKILL 脚本的构造器。"""

    def __init__(self, design: "Design", lib: str, cell: str, view: str,
                 view_type: str):
        self.design = design
        self.lib = lib
        self.cell = cell
        self.view = view
        self.view_type = view_type
        self.ops: list[Op] = []
        self.var = "cv"

    # -- 生成 ------------------------------------------------------------- #
    def open(self, mode: str = "w") -> "Cell":
        self.ops.append(Op(
            '(setq cv (dbOpenCellViewByType "%s" "%s" "%s" "%s" "%s"))'
            % (self.lib, self.cell, self.view, self.view_type, mode),
            "open"))
        return self

    def script(self) -> str:
        body = "\n".join("  " + op.code for op in self.ops)
        return "(let (cv)\n%s\n)" % body

    def run(self) -> dict:
        """执行并读回（一次往返）。"""
        probe = self._probe()
        code = "(let (cv)\n%s\n%s\n)" % (
            "\n".join("  " + op.code for op in self.ops), probe)
        return self.design.bridge.eval(code) if hasattr(self.design.bridge, "eval") \
            else {"raw": code}

    def _probe(self) -> str:
        return ('(printf "@@insts=%L@@" length(cv~>instances))\n'
                '  (printf "@@shapes=%L@@" length(cv~>shapes))\n'
                '  (printf "@@nets=%L@@" length(cv~>nets))\n'
                '  (printf "@@ok=%L@@" dbSave(cv))')


class Schematic(Cell):
    def __init__(self, design, lib, cell):
        super().__init__(design, lib, cell, "schematic", "schematic")

    # -- 实例/网络 -------------------------------------------------------- #
    def inst(self, mlib: str, mcell: str, mview: str, name: str,
             origin, orient: str = "R0") -> "Schematic":
        self.ops.append(Op(
            '(dbCreateInstByMasterName cv "%s" "%s" "%s" "%s" %s "%s")'
            % (mlib, mcell, mview, name, _pt(origin), orient), "inst"))
        return self

    def net(self, name: str) -> "Schematic":
        self.ops.append(Op('(dbCreateNet cv "%s")' % name, "net"))
        return self

    def term(self, net: str, inst: str, term: str) -> "Schematic":
        self.ops.append(Op(
            '(dbCreateConnByName (dbGetNet cv "%s") (dbGetInstByName cv "%s") "%s")'
            % (net, inst, term), "conn"))
        return self

    def wire(self, points, net: str = "draw") -> "Schematic":
        self.ops.append(Op(
            '(schCreateWire cv "%s" "full" %s)' % (net, _pts(points)), "wire"))
        return self

    def pin(self, name: str, direction: str, origin,
            orient: str = "R0") -> "Schematic":
        self.ops.append(Op(
            '(schCreatePin cv "%s" "%s" nil %s "%s")'
            % (name, direction, _pt(origin), orient), "pin"))
        return self

    def label(self, origin, text: str, orient: str = "R0") -> "Schematic":
        self.ops.append(Op(
            '(schCreateWireLabel cv %s "%s" "lowerLeft" "%s")'
            % (_pt(origin), text, orient), "label"))
        return self

    def check(self) -> "Schematic":
        self.ops.append(Op("(schCheck cv)", "check"))
        return self


class Layout(Cell):
    def __init__(self, design, lib, cell):
        super().__init__(design, lib, cell, "layout", "maskLayout")

    def rect(self, layer, bbox) -> "Layout":
        (x0, y0), (x1, y1) = bbox
        self.ops.append(Op(
            "(dbCreateRect cv %s (list %s %s))"
            % (_lp(layer), _pt((x0, y0)), _pt((x1, y1))), "rect"))
        return self

    def path(self, layer, points, width: float) -> "Layout":
        self.ops.append(Op(
            "(dbCreatePath cv %s %s %r)" % (_lp(layer), _pts(points), width),
            "path"))
        return self

    def label(self, layer, origin, text: str) -> "Layout":
        self.ops.append(Op(
            '(dbCreateLabel cv %s %s "%s" "lowerLeft" "R0" "roman" 0.2)'
            % (_lp(layer), _pt(origin), text), "label"))
        return self


class Design:
    def __init__(self, bridge=None):
        if bridge is None:
            from .bridge import get_bridge
            bridge = get_bridge()
        self.bridge = bridge
        self.libs: list[tuple[str, str]] = []

    def library(self, name: str, path: str | None = None) -> "Design":
        self.libs.append((name, path or "/tmp/vl_py/%s" % name.lower()))
        return self

    def lib_script(self) -> str:
        lines = []
        for name, path in self.libs:
            lines.append('(unless (ddGetObj "%s") (ddCreateLib "%s" "%s"))'
                         % (name, name, path))
        return "\n".join(lines)

    def schematic(self, lib: str, cell: str) -> Schematic:
        return Schematic(self, lib, cell).open("w")

    def layout(self, lib: str, cell: str) -> Layout:
        return Layout(self, lib, cell).open("w")


__all__ = ["Design", "Schematic", "Layout", "LayerPurpose", "Point"]
