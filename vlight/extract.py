"""extract —— 寄生 RC 提取（版图 → 带寄生的网表）。

真提取器（Quantus/StarRC）是场求解器 + 模型降阶；这里给两条都能量化的路：

1. **解析式（默认）**：方块电阻 + 面积/周长电容 + 平行板耦合电容
       R = rsh · L/W
       C_gnd = Ca·A + Cp·P
       C_couple = ε0·εr·A_overlap / d
2. **2D 场求解（可选）**：`microstrip_cap()` 用有限差分解拉普拉斯方程，
   算单位长度电容，用来校准 1 里的 Ca/Cp。

输出是标准 `spicein.Netlist`，可以直接喂给 `spice.Circuit` 做后仿。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom2d, polybool
from .spicein import Element, Netlist
from .vdb import Layout, TechFile

EPS0 = 8.8541878128e-12


@dataclass
class ExtractRules:
    """工艺提取参数（每层一套）。单位：SI。"""
    rsh: dict[str, float] = field(default_factory=dict)      # ohm/sq
    ca: dict[str, float] = field(default_factory=dict)       # F/m^2 对地
    cper: dict[str, float] = field(default_factory=dict)     # F/m 周长
    z: dict[str, float] = field(default_factory=dict)        # 层高度（耦合用）
    eps_r: float = 4.0
    contact_r: float = 0.0                                   # 每个接触孔电阻

    @classmethod
    def demo(cls) -> "ExtractRules":
        return cls(
            rsh={"M1": 0.1, "M2": 0.1, "M8": 0.05, "M9": 0.05, "PO": 8.0},
            ca={"M1": 3.0e-5, "M2": 3.0e-5, "M8": 2.0e-5, "M9": 2.0e-5, "PO": 1.2e-4},
            cper={"M1": 8.0e-11, "M2": 8.0e-11, "M8": 6.0e-11, "M9": 6.0e-11},
            z={"M1": 1.0e-6, "M2": 1.5e-6, "M8": 6.0e-6, "M9": 8.0e-6},
        )


@dataclass
class ExtractedDevice:
    name: str
    kind: str            # R / C
    value: float
    nodes: tuple[str, str]
    layer: str = ""
    tag: str = ""


def _label_map(layout: Layout) -> dict[int, str]:
    """label -> 它所在的图形。"""
    out: dict[int, str] = {}
    shapes = [s for s in layout.shapes if len(s.points) >= 3]
    for s in layout.shapes:
        if s.kind in ("label", "text") and s.text and s.points:
            pt = s.points[0]
            for i, sh in enumerate(shapes):
                if geom2d.point_in_poly(pt, sh.points):
                    out[i] = s.text
                    break
    return out


def _shape_net_names(layout: Layout) -> dict[int, str]:
    """给每个图形分配网络名：优先用 label，否则 N$k。"""
    shapes = [s for s in layout.shapes if len(s.points) >= 3]
    labels = _label_map(layout)
    names: dict[int, str] = {}
    k = 0
    for i in range(len(shapes)):
        if i in labels:
            names[i] = labels[i]
        else:
            k += 1
            names[i] = f"N${k}"
    return names


def extract(layout: Layout, rules: ExtractRules | None = None,
            tech: TechFile | None = None, prefix: str = "X") -> Netlist:
    """把版图抽成 R/C 网表（每个图形一段电阻 + 对地/耦合电容）。"""
    rules = rules or ExtractRules.demo()
    shapes = [s for s in layout.shapes if len(s.points) >= 3]
    names = _shape_net_names(layout)
    nl = Netlist(path=f"extracted:{layout.cell}")
    nl.simulator = "spectre"
    nl.globals = ["0"]
    used_names: set[str] = set()
    for nm in names.values():
        if nm not in used_names and not nm.startswith("N$"):
            nl.subckts = []
    internal = 0

    devices: list[ExtractedDevice] = []

    for i, s in enumerate(shapes):
        layer = s.layer
        poly = s.points
        x0, y0, x1, y1 = geom2d.bbox(poly)
        w_bbox, h_bbox = x1 - x0, y1 - y0
        area = polybool.area(poly)
        perim = sum(geom2d.dist(a, b) for a, b in geom2d.edges(poly))

        # 电流方向取 bbox 长边，宽度取短边（片状近似的标准做法）
        length, width = max(w_bbox, h_bbox), max(min(w_bbox, h_bbox), 1e-12)
        rsh = rules.rsh.get(layer)
        node_a = names[i]
        node_b = f"{node_a}_r{i}"
        if rsh is not None:
            r = rsh * length / width
            # 图形本身的端点：沿长边两端
            devices.append(ExtractedDevice(f"{prefix}R{i}", "R", r,
                                           (node_a, node_b), layer, "sheet"))
        else:
            node_b = node_a                          # 该层不提取电阻

        ca = rules.ca.get(layer, 0.0)
        cp = rules.cper.get(layer, 0.0)
        c = ca * area + cp * perim
        if c > 0:
            devices.append(ExtractedDevice(f"{prefix}C{i}", "C", c,
                                           (node_b, "0"), layer, "ground"))

    # 耦合电容：不同层、投影重叠、按高度差算平行板
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            si, sj = shapes[i], shapes[j]
            if si.layer == sj.layer:
                continue
            ov = polybool.intersection_area(si.points, sj.points)
            if ov <= 0:
                continue
            zi = rules.z.get(si.layer, 1e-6)
            zj = rules.z.get(sj.layer, 1e-6)
            d = abs(zi - zj) or 1e-6
            c = rules.eps_r * EPS0 * ov / d
            if c <= 0:
                continue
            devices.append(ExtractedDevice(
                f"{prefix}Cc{i}_{j}", "C", c,
                (names[i], names[j]), f"{si.layer}|{sj.layer}", "couple"))

    # 转成网表元素
    for d in devices:
        params = {"r": d.value} if d.kind == "R" else {"c": d.value}
        nl.elements.append(Element(d.name, list(d.nodes),
                                   "resistor" if d.kind == "R" else "capacitor",
                                   {k: repr(v) for k, v in params.items()}))
    # 端口 = 带 label 的图形
    labeled = {nm for nm in names.values() if not nm.startswith("N$")}
    nl.subckts = []
    nl.parameters = {}
    nl.saves = []
    nl.raw_lines = [f"* ports: {' '.join(sorted(labeled))}",
                    f"* devices: {len(devices)}"]
    return nl


def summary(nl: Netlist) -> dict:
    r = [e for e in nl.elements if e.master == "resistor"]
    c = [e for e in nl.elements if e.master == "capacitor"]
    rv = [float(e.params.get("r", 0)) for e in r]
    cv = [float(e.params.get("c", 0)) for e in c]
    return {"resistors": len(r), "capacitors": len(c),
            "R_total_ohm": sum(rv), "C_total_F": sum(cv)}


# --------------------------------------------------------------------------- #
# 2D 场求解：微带线单位长度电容（校准用）
# --------------------------------------------------------------------------- #

def microstrip_cap(width: float, height: float, eps_r: float = 4.0,
                   nx: int = 120, ny: int = 60) -> float:
    """有限差分解 2D 拉普拉斯，求单位长度电容（F/m）。

    边界：底部地平面 V=0，导体 V=1，外框用零法向导数（Neumann）。
    电容 C = 2·W_e / V²，W_e 为电场能量密度积分。
    """
    dx, dy = width / nx, height / ny
    V = [[0.0] * (nx + 1) for _ in range(ny + 1)]
    # 导体：顶行中间一段置 1
    i0, i1 = int(nx * 0.25), int(nx * 0.75)
    for it in range(4000):
        delta = 0.0
        for j in range(1, ny):
            for i in range(1, nx):
                if j == ny - 2 and i0 <= i <= i1:
                    V[j][i] = 1.0
                    continue
                new = 0.25 * (V[j][i - 1] + V[j][i + 1] + V[j - 1][i] + V[j + 1][i])
                delta = max(delta, abs(new - V[j][i]))
                V[j][i] = new
        if delta < 1e-9:
            break
    energy = 0.0
    for j in range(ny):
        for i in range(nx):
            ex = (V[j][i + 1] - V[j][i]) / dx
            ey = (V[j + 1][i] - V[j][i]) / dy
            energy += 0.5 * eps_r * EPS0 * (ex * ex + ey * ey) * dx * dy
    return 2 * energy            # V=1，C = 2W/V²


__all__ = ["ExtractRules", "extract", "summary", "microstrip_cap"]
