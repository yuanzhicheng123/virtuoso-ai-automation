"""polybool —— 多边形布尔（扫描线 + 梯形分解）。

DRC、寄生提取、连接性提取都需要"两个图形重叠多少/并起来多大"。
这里用最稳的一招：把所有顶点的 y 排好，逐条水平带做区间求交/求并。

* 面积/重叠面积：精确到浮点（对任意简单多边形成立）
* 求并多边形：输出矩形拼成的正交多边形（够 GDS/DRC 用）

复杂度 O(V·E)，V 是顶点总数——版图单元级别的图形完全够用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

Point = tuple[float, float]
Poly = Sequence[Point]


def _edges(poly: Poly) -> list[tuple[Point, Point]]:
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def _crossings(poly: Poly, y: float) -> list[float]:
    """水平线 y 与多边形边的交点 x（从左到右）。"""
    xs = []
    for (x1, y1), (x2, y2) in _edges(poly):
        if y1 == y2:
            continue
        lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
        if lo <= y < hi:
            xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return xs


def _intervals(poly: Poly, y: float) -> list[tuple[float, float]]:
    xs = _crossings(poly, y)
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def _ys(polys: Iterable[Poly]) -> list[float]:
    ys = sorted({p[1] for poly in polys for p in poly})
    return ys


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [list(intervals[0])]
    for a, b in intervals[1:]:
        if a <= out[-1][1] + 1e-18:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def union_intervals(a: list[tuple[float, float]],
                    b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return _merge(list(a) + list(b))


def intersect_intervals(a: list[tuple[float, float]],
                        b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def area(poly: Poly) -> float:
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def union_area(polys: list[Poly]) -> float:
    if not polys:
        return 0.0
    ys = _ys(polys)
    total = 0.0
    for y0, y1 in zip(ys[:-1], ys[1:]):
        y = (y0 + y1) / 2
        ivs: list[tuple[float, float]] = []
        for p in polys:
            ivs = union_intervals(ivs, _intervals(p, y))
        total += sum(b - a for a, b in ivs) * (y1 - y0)
    return total


def intersection_area(a: Poly, b: Poly) -> float:
    ys = sorted({p[1] for p in a} | {p[1] for p in b})
    total = 0.0
    for y0, y1 in zip(ys[:-1], ys[1:]):
        y = (y0 + y1) / 2
        iv = intersect_intervals(_intervals(a, y), _intervals(b, y))
        total += sum(hi - lo for lo, hi in iv) * (y1 - y0)
    return total


def overlap(a: Poly, b: Poly, tol: float = 1e-15) -> bool:
    return intersection_area(a, b) > tol


@dataclass
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def points(self) -> list[Point]:
        return [(self.x0, self.y0), (self.x1, self.y0),
                (self.x1, self.y1), (self.x0, self.y1)]

    def touches(self, other: "Rect", tol: float = 1e-12) -> bool:
        return not (other.x0 > self.x1 + tol or self.x0 > other.x1 + tol or
                    other.y0 > self.y1 + tol or self.y0 > other.y1 + tol)


def to_rects(polys: list[Poly]) -> list[Rect]:
    """把多边形集合并成一堆矩形（垂直分解，同带同区间的合并）。"""
    ys = _ys(polys)
    rects: dict[tuple[float, float, float, float], Rect] = {}
    for y0, y1 in zip(ys[:-1], ys[1:]):
        y = (y0 + y1) / 2
        ivs: list[tuple[float, float]] = []
        for p in polys:
            ivs = union_intervals(ivs, _intervals(p, y))
        for a, b in ivs:
            rects[(round(a, 12), round(y0, 12), round(b, 12), round(y1, 12))] = \
                Rect(a, y0, b, y1)
    # 纵向合并：同 x 区间且上下相接
    merged: list[Rect] = []
    for r in sorted(rects.values(), key=lambda r: (r.x0, r.x1, r.y0)):
        if merged:
            m = merged[-1]
            if (abs(m.x0 - r.x0) < 1e-12 and abs(m.x1 - r.x1) < 1e-12
                    and abs(m.y1 - r.y0) < 1e-12):
                m.y1 = r.y1
                continue
        merged.append(r)
    return merged


def union_polygons(polys: list[Poly], as_rects: bool = True):
    rects = to_rects(polys)
    if as_rects:
        return rects
    return [r.points() for r in rects]


def bbox(poly: Poly) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


__all__ = ["area", "union_area", "intersection_area", "overlap", "to_rects",
           "union_polygons", "union_intervals", "intersect_intervals",
           "Rect", "bbox"]
