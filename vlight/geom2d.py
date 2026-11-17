"""geom2d —— 版图检查用的二维几何（自实现，不依赖 klayout）。

只做 DRC/连接性提取真正需要的那几件事：点、线段距离、多边形相交/包含、面积。
多边形默认是「顶点序列 + 隐式闭合」，坐标单位是用户单位（米）。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]
Poly = Sequence[Point]


def bbox(poly: Poly) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_overlap(a, b, tol: float = 0.0) -> bool:
    return not (a[2] < b[0] - tol or b[2] < a[0] - tol or
                a[3] < b[1] - tol or b[3] < a[1] - tol)


def area(poly: Poly) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def edges(poly: Poly) -> list[tuple[Point, Point]]:
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def seg_point_dist(a: Point, b: Point, p: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def seg_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1 = _cross(c, d, a)
    d2 = _cross(c, d, b)
    d3 = _cross(a, b, c)
    d4 = _cross(a, b, d)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return False


def seg_seg_dist(a: Point, b: Point, c: Point, d: Point) -> float:
    if seg_intersect(a, b, c, d):
        return 0.0
    return min(seg_point_dist(a, b, c), seg_point_dist(a, b, d),
               seg_point_dist(c, d, a), seg_point_dist(c, d, b))


def point_in_poly(p: Point, poly: Poly) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-300) + x1
            if xin > x:
                inside = not inside
    return inside


def polys_touch(p: Poly, q: Poly, tol: float = 0.0) -> bool:
    """两个多边形是否接触/重叠（含边贴边、顶点落边）。"""
    if not bbox_overlap(bbox(p), bbox(q), tol):
        return False
    if any(point_in_poly(pt, q) for pt in p) or any(point_in_poly(pt, p) for pt in q):
        return True
    for a, b in edges(p):
        for c, d in edges(q):
            if seg_intersect(a, b, c, d):
                return True
            if tol > 0 and seg_seg_dist(a, b, c, d) <= tol:
                return True
    return False


def poly_poly_dist(p: Poly, q: Poly) -> float:
    """两个多边形之间的最小边距（接触=0）。"""
    if polys_touch(p, q):
        return 0.0
    best = math.inf
    for a, b in edges(p):
        for c, d in edges(q):
            best = min(best, seg_seg_dist(a, b, c, d))
    return best


def min_width(poly: Poly) -> float:
    """多边形的最小「宽度」：非相邻对边之间的最小距离。

    对矩形/八边形这类凸多边形就是短边长度；凹多边形会偏保守。
    """
    es = edges(poly)
    n = len(es)
    best = math.inf
    for i in range(n):
        a, b = es[i]
        for j in range(i + 1, n):
            if abs(i - j) <= 1 or (i == 0 and j == n - 1):
                continue                                   # 相邻边不算
            c, d = es[j]
            best = min(best, seg_seg_dist(a, b, c, d))
    return best


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def grow(poly: Poly, d: float) -> list[Point]:
    """粗糙外扩：沿质心方向按 d 拉开（只用于可视化，不是精确 offset）。"""
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    out = []
    for x, y in poly:
        vx, vy = x - cx, y - cy
        L = math.hypot(vx, vy) or 1.0
        out.append((x + vx / L * d, y + vy / L * d))
    return out


__all__ = ["Point", "Poly", "bbox", "bbox_overlap", "area", "edges",
           "seg_point_dist", "seg_seg_dist", "seg_intersect", "point_in_poly",
           "polys_touch", "poly_poly_dist", "min_width", "dist", "grow"]
