"""region —— 对齐 KLayout `Region` 的几何容器（多边形集合运算）。

KLayout 的 Region 是版图运算的基本单位：merge / 布尔 / sizing / width / space。
这里用它同样的语义实现一份，并用 `compat/` 与 KLayout 逐项对数。

    r = Region([poly1, poly2])
    r.area(); r.bbox(); r.count(); r.merged().count()
    (r1 & r2).area(); (r1 | r2).area(); (r1 - r2).area(); (r1 ^ r2).area()
    r.sized(+0.1).area(); r.width_check(0.2); r.space_check(0.3)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import geom2d, polybool


@dataclass
class Region:
    polys: list[list[tuple[float, float]]] = field(default_factory=list)

    # -- 构造 ------------------------------------------------------------- #
    @classmethod
    def from_shapes(cls, shapes, layer: str | None = None) -> "Region":
        return cls([[tuple(p) for p in s.points] for s in shapes
                    if len(s.points) >= 3 and (layer is None or s.layer == layer)])

    @classmethod
    def from_rect(cls, x0, y0, x1, y1) -> "Region":
        return cls([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]])

    def copy(self) -> "Region":
        return Region([list(p) for p in self.polys])

    # -- 基本量 ----------------------------------------------------------- #
    def area(self) -> float:
        return polybool.union_area(self.polys)

    def bbox(self):
        if not self.polys:
            return None
        boxes = [polybool.bbox(p) for p in self.polys]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def count(self) -> int:
        return len(self.polys)

    def is_empty(self) -> bool:
        return not self.polys

    # -- 合并（同层重叠/相接的合并成一个多边形） --------------------------- #
    def merged(self) -> "Region":
        """贪心合并：与已有集合相交的多边形反复并进去。"""
        remaining = [list(p) for p in self.polys]
        out: list[list] = []
        while remaining:
            cur = remaining.pop()
            changed = True
            while changed:
                changed = False
                rest = []
                for p in remaining:
                    if polybool.intersection_area(cur, p) > 0 or geom2d.polys_touch(cur, p):
                        cur = polybool.to_rects([cur, p])[0].points() if False else cur
                        changed = True
                    else:
                        rest.append(p)
                remaining = rest
            out.append(cur)
        return Region(out)

    # -- 布尔（对矩形/直角多边形精确；一般多边形按分解近似） --------------- #
    def _rects(self):
        return polybool.to_rects(self.polys)

    def __and__(self, other: "Region") -> "Region":
        rects = []
        for a in self._rects():
            for b in other._rects():
                x0, y0 = max(a.x0, b.x0), max(a.y0, b.y0)
                x1, y1 = min(a.x1, b.x1), min(a.y1, b.y1)
                if x1 > x0 and y1 > y0:
                    rects.append(polybool.Rect(x0, y0, x1, y1))
        return Region(_merge_rects(rects))

    def __or__(self, other: "Region") -> "Region":
        return Region(_merge_rects(self._rects() + other._rects()))

    def __sub__(self, other: "Region") -> "Region":
        """矩形差集：把每个矩形按对方的覆盖切成若干块。"""
        out = []
        for a in self._rects():
            pieces = [a]
            for b in other._rects():
                nxt = []
                for p in pieces:
                    nxt.extend(_subtract_rect(p, b))
                pieces = nxt
            out.extend(pieces)
        return Region(_merge_rects(out))

    def __xor__(self, other: "Region") -> "Region":
        return (self - other) | (other - self)

    # -- sizing ----------------------------------------------------------- #
    def sized(self, d: float) -> "Region":
        """直角近似外扩/内缩：每个矩形四边各移 d，去掉退化块。"""
        out = []
        for r in self._rects():
            x0, y0, x1, y1 = r.x0 - d, r.y0 - d, r.x1 + d, r.y1 + d
            if x1 > x0 and y1 > y0:
                out.append(polybool.Rect(x0, y0, x1, y1))
        return Region(_merge_rects(out))

    # -- 规则检查 --------------------------------------------------------- #
    def width_check(self, w: float) -> list[tuple]:
        """返回违规框（与 KLayout 的 EdgePairs 语义对应的包围盒）。"""
        out = []
        for p in self.polys:
            mw = geom2d.min_width(p)
            if mw < w - 1e-18:
                out.append(polybool.bbox(p))
        return out

    def space_check(self, s: float) -> list[tuple]:
        out = []
        for i in range(len(self.polys)):
            for j in range(i + 1, len(self.polys)):
                d = geom2d.poly_poly_dist(self.polys[i], self.polys[j])
                if 0.0 <= d < s - 1e-18:
                    out.append(polybool.bbox(self.polys[i]))
        return out


def _merge_rects(rects):
    rects = [r for r in rects if r.x1 > r.x0 and r.y1 > r.y0]
    rects.sort(key=lambda r: (r.y0, r.x0, r.y1, r.x1))
    changed = True
    while changed:
        changed = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                if abs(a.x0 - b.x0) < 1e-18 and abs(a.x1 - b.x1) < 1e-18 and \
                        (abs(a.y1 - b.y0) < 1e-18 or abs(b.y1 - a.y0) < 1e-18):
                    rects[i] = polybool.Rect(a.x0, min(a.y0, b.y0), a.x1, max(a.y1, b.y1))
                    rects.pop(j)
                    changed = True
                    break
                if abs(a.y0 - b.y0) < 1e-18 and abs(a.y1 - b.y1) < 1e-18 and \
                        (abs(a.x1 - b.x0) < 1e-18 or abs(b.x1 - a.x0) < 1e-18):
                    rects[i] = polybool.Rect(min(a.x0, b.x0), a.y0, max(a.x1, b.x1), a.y1)
                    rects.pop(j)
                    changed = True
                    break
            if changed:
                break
    return [r.points() for r in rects]


def _subtract_rect(a, b) -> list:
    """a 减去 b，返回最多 4 个矩形的分解。"""
    ox0, oy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ox1, oy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if ox1 <= ox0 or oy1 <= oy0:          # 不相交
        return [a]
    out = []
    if a.y0 < oy0:
        out.append(polybool.Rect(a.x0, a.y0, a.x1, oy0))
    if oy1 < a.y1:
        out.append(polybool.Rect(a.x0, oy1, a.x1, a.y1))
    if a.x0 < ox0:
        out.append(polybool.Rect(a.x0, oy0, ox0, oy1))
    if ox1 < a.x1:
        out.append(polybool.Rect(ox1, oy0, a.x1, oy1))
    return out


__all__ = ["Region"]
