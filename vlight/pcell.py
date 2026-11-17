"""pcell —— 参数化单元（Virtuoso pcells 的轻量等价物）。

Virtuoso 的 pcell 是 SKILL 里的 `pcDefinePCell`，参数一改就重算版图。
这里用 Python 类做同样的事：参数 -> 几何，注册到表里，可以按名字生成。

已实现（够 RFIC 常用）:
    spiral_ind   八边形螺旋电感（可选对称/抽头留给以后）
    mim_cap      MIM 电容（上下板 + via 阵列）
    poly_res     多晶电阻（蛇形 segments）
    mos4         四端 MOS 的粗版图（OD/PO/contact）
    via_array    通用 via 阵列

几何都用「中线 + 线宽」生成多边形，重叠多边形在 GDS 里是合法的，
真正要求合并/DRC 时再接 klayout。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .vdb import Layout, Library, Shape

Point = tuple[float, float]


# --------------------------------------------------------------------------- #
# 基础几何
# --------------------------------------------------------------------------- #

def rect(x0: float, y0: float, x1: float, y1: float) -> list[Point]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def path_polygons(points: list[Point], width: float) -> list[list[Point]]:
    """折线 -> 一组矩形/四边形（不做布尔合并，允许叠）。"""
    hw = width / 2.0
    polys: list[list[Point]] = []
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln < 1e-12:
            continue
        ux, uy = dx / ln, dy / ln
        nx, ny = -uy * hw, ux * hw
        polys.append([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                      (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])
    for x, y in points[1:-1]:                       # 拐角补方
        polys.append(rect(x - hw, y - hw, x + hw, y + hw))
    return polys


def octagon(radius: float, clip: float = 0.4142, stretch: float = 1.0) -> list[Point]:
    """八边形（Virtuoso pcell 里 octagon 的通用参数化）。"""
    r = radius
    c = clip * r
    s = stretch
    return [(c, r * s), (r, c * s), (r, -c * s), (c, -r * s),
            (-c, -r * s), (-r, -c * s), (-r, c * s), (-c, r * s)]


def spiral_centerline(nr: float, radius: float, width: float, spacing: float,
                      start_deg: float = 0.0, direction: int = 1,
                      seg_per_turn: int = 8) -> list[Point]:
    """八边形螺旋的中线。nr 可以是 0.5 的倍数（半圈起步）。"""
    r_out = radius
    r_in = max(radius - nr * (width + spacing), width)
    steps = max(int(nr * seg_per_turn), 2)
    pts: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        r = r_out + (r_in - r_out) * t
        ang = math.radians(start_deg) + direction * 2 * math.pi * nr * t
        # 八边形：角度量化到 45° 的边上
        a = (ang % (2 * math.pi)) / (2 * math.pi) * 8
        k = int(a)
        frac = a - k
        p0 = _oct_vertex(k)
        p1 = _oct_vertex(k + 1)
        x = (p0[0] + (p1[0] - p0[0]) * frac) * r
        y = (p0[1] + (p1[1] - p0[1]) * frac) * r
        pts.append((x, y))
    return pts


def _oct_vertex(k: int) -> Point:
    ang = math.pi / 8 + k * math.pi / 4
    return (math.cos(ang), math.sin(ang))


# --------------------------------------------------------------------------- #
# 电感
# --------------------------------------------------------------------------- #

def wheeler_inductance(n: float, d_out: float, d_in: float, mu0: float = 1.25663706212e-6) -> float:
    """修正 Wheeler 公式（方形螺旋近似，八边形略偏小，量级够用）。"""
    d_avg = (d_out + d_in) / 2
    rho = (d_out - d_in) / (d_out + d_in) if (d_out + d_in) else 0.0
    return 2.34 * mu0 * n * n * d_avg / (1 + 2.75 * rho)


@dataclass
class SpiralInd:
    nr: float = 2.0
    rad: float = 60e-6
    w: float = 10e-6
    spacing: float = 3e-6
    metal: str = "M9"
    under: str = "M8"
    via: str = "VIA8"
    pin_layer: str = "M9"
    via_size: float = 0.26e-6
    via_pitch: float = 0.4e-6
    port: str = ""

    def layout(self, cell: str = "IND", lib: str = "") -> Layout:
        lay = Layout(cell, lib)
        center = spiral_centerline(self.nr, self.rad, self.w, self.spacing,
                                   start_deg=90, direction=-1)
        for poly in path_polygons(center, self.w):
            lay.add(self.metal, poly)
        # 内圈 -> 下层引出
        inner = center[0]
        out = (inner[0], -self.rad * 1.25)
        for poly in path_polygons([inner, (inner[0], out[1])], self.w):
            lay.add(self.under, poly)
        for vx, vy in via_grid(inner[0], out[1] + self.w, self.w,
                               self.via_size, self.via_pitch):
            lay.add(self.via, rect(vx, vy, vx + self.via_size, vy + self.via_size))
        # 外圈端口
        lay.add(self.pin_layer, [(center[-1][0] - self.w / 2, center[-1][1] - self.w / 2),
                          (center[-1][0] + self.w / 2, center[-1][1] - self.w / 2),
                          (center[-1][0] + self.w / 2, center[-1][1] + self.w / 2),
                          (center[-1][0] - self.w / 2, center[-1][1] + self.w / 2)])
        return lay

    def metrics(self) -> dict[str, float]:
        d_out = 2 * self.rad
        d_in = 2 * max(self.rad - self.nr * (self.w + self.spacing), 0.0)
        L = wheeler_inductance(self.nr, d_out, d_in)
        return {"L_H": L, "D_out": d_out, "D_in": d_in,
                "turns": self.nr, "width": self.w}


def via_grid(x: float, y: float, span: float, size: float, pitch: float):
    n = max(int(span / pitch), 1)
    for i in range(n):
        yield x - span / 2 + i * pitch + (pitch - size) / 2, y + size


# --------------------------------------------------------------------------- #
# MIM 电容
# --------------------------------------------------------------------------- #

@dataclass
class MimCap:
    lt: float = 10e-6
    wt: float = 10e-6
    m: int = 1
    top: str = "CTM"
    bottom: str = "CBM"
    via: str = "VIA8"
    c_density: float = 1.5e-15            # F/um^2，tsmcN65 MIM 量级

    def layout(self, cell: str = "MIM", lib: str = "") -> Layout:
        lay = Layout(cell, lib)
        pitch = self.lt + 1.2e-6
        cols = max(int(math.ceil(math.sqrt(self.m))), 1)
        for i in range(self.m):
            cx = (i % cols) * pitch
            cy = (i // cols) * pitch
            lay.add(self.bottom, rect(cx, cy, cx + self.lt, cy + self.wt))
            lay.add(self.top, rect(cx + 0.2e-6, cy + 0.2e-6,
                                   cx + self.lt - 0.2e-6, cy + self.wt - 0.2e-6))
            for vx, vy in via_grid(cx + self.lt / 2, cy + self.wt - 0.4e-6,
                                   self.wt * 0.4, 0.26e-6, 0.4e-6):
                lay.add(self.via, rect(vx, vy, vx + 0.26e-6, vy + 0.26e-6))
        return lay

    def capacitance(self) -> float:
        return self.c_density * self.lt * self.wt * 1e12 * self.m    # 单位: F*1e12? 见下

    def farads(self) -> float:
        return self.c_density * self.lt * self.wt * self.m


# --------------------------------------------------------------------------- #
# 多晶电阻
# --------------------------------------------------------------------------- #

@dataclass
class PolyRes:
    l: float = 10e-6
    w: float = 1e-6
    segments: int = 1
    layer: str = "PO"
    contact: str = "CO"
    rsh: float = 250.0                    # ohm/sq

    def layout(self, cell: str = "RES", lib: str = "") -> Layout:
        lay = Layout(cell, lib)
        gap = max(self.w * 2, 1.4e-6)
        for i in range(self.segments):
            y = i * (self.w + gap)
            lay.add(self.layer, rect(0, y, self.l, y + self.w))
            for x in (0.0, self.l - self.w):
                lay.add(self.contact, rect(x, y, x + self.w, y + self.w))
            if i + 1 < self.segments:           # 蛇形连接
                xc = self.l - self.w / 2
                lay.add(self.layer, rect(xc, y, xc + self.w, y + self.w + gap))
        return lay

    def resistance(self) -> float:
        return self.rsh * self.l / self.w * self.segments


# --------------------------------------------------------------------------- #
# MOS（粗版图，用于示意/面积估算）
# --------------------------------------------------------------------------- #

@dataclass
class Mos4:
    w: float = 10e-6
    l: float = 0.35e-6
    nf: int = 4
    od: str = "OD"
    po: str = "PO"
    contact: str = "CO"
    m1: str = "M1"

    def layout(self, cell: str = "MOS", lib: str = "") -> Layout:
        lay = Layout(cell, lib)
        pitch = self.w + 0.6e-6
        total = self.nf * pitch
        lay.add(self.od, rect(0, -1.2e-6, total, 0.35e-6))
        for i in range(self.nf):
            x = i * pitch + 0.3e-6
            lay.add(self.po, rect(x, -1.6e-6, x + self.l, 0.75e-6))
            for dx in (0.0, self.l / 2):
                lay.add(self.contact, rect(x + dx, -1.1e-6, x + dx + 0.2e-6, -0.9e-6))
                lay.add(self.contact, rect(x + dx, 0.1e-6, x + dx + 0.2e-6, 0.3e-6))
        lay.add(self.m1, rect(-0.5e-6, -1.9e-6, 0.0, 0.9e-6))
        return lay

    def area(self) -> float:
        return self.w * self.l * self.nf * 1e12        # um^2


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

PCELS: dict[str, tuple[type, Callable[[object], dict]]] = {
    "spiral_ind": (SpiralInd, lambda o: o.metrics()),
    "mim_cap": (MimCap, lambda o: {"C_F": o.farads()}),
    "poly_res": (PolyRes, lambda o: {"R_ohm": o.resistance()}),
    "mos4": (Mos4, lambda o: {"area_um2": o.area()}),
}


def make(kind: str, **params) -> tuple[Layout, dict]:
    if kind not in PCELS:
        raise KeyError(f"没有 pcell {kind!r}；有 {list(PCELS)}")
    cls, metrics = PCELS[kind]
    obj = cls(**{k: v for k, v in params.items() if k in cls.__dataclass_fields__})
    return obj.layout(params.pop("cell", kind), params.pop("lib", "")), metrics(obj)


def add_to_library(lib: Library, cell: str, kind: str, view: str = "layout", **params):
    """等价 pcDefinePCell + 实例化：在库里生成一个版图 cell。"""
    lay, met = make(kind, cell=cell, lib=lib.name, **params)
    lib.cell(cell, create=True).layouts[view] = lay
    return lay, met


__all__ = ["SpiralInd", "MimCap", "PolyRes", "Mos4", "PCELS", "make",
           "add_to_library", "spiral_centerline", "path_polygons", "octagon",
           "rect", "wheeler_inductance", "via_grid"]
