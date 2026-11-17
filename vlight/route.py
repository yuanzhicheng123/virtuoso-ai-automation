"""route —— 迷宫布线（A* on grid），支持多层与过孔。

版图/原理图自动连线的核心算法：
    网格化 -> 障碍标记 -> A*（曼哈顿启发）-> 回溯成折线 -> 插过孔
代价：走线长度 + 换层次数×过孔代价 + 拐弯惩罚（越少拐弯越好打）。

用法：
    r = Router(bbox, pitch=0.1e-6, layers=["M1","M2"])
    r.add_obstacle("M1", poly)
    path = r.route(("M1", 0, 0), ("M2", 30, 0))
    -> Path(segments=[(layer, [pts...]), ...], vias=[(x, y, "VIA1"), ...])
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass
class Path:
    segments: list[tuple[str, list[Point]]] = field(default_factory=list)
    vias: list[tuple[float, float, str]] = field(default_factory=list)
    cost: float = math.inf
    length: float = 0.0

    def ok(self) -> bool:
        return self.cost < math.inf

    def points(self) -> list[Point]:
        out = []
        for _l, pts in self.segments:
            out.extend(pts)
        return out

    def to_shapes(self, width_layer: dict[str, float] | None = None):
        """转成 vdb.Shape 列表（走线用中线 + 宽度画成矩形）。"""
        from .pcell import path_polygons
        from .vdb import Shape
        shapes = []
        for layer, pts in self.segments:
            w = (width_layer or {}).get(layer, 0.1e-6)
            for poly in path_polygons(pts, w):
                shapes.append(Shape(kind="polygon", layer=layer, points=poly))
        return shapes


class Router:
    """正交网格迷宫布线器。"""

    def __init__(self, bbox: tuple[float, float, float, float], pitch: float,
                 layers: list[str] | None = None, via_cost: float = 8.0,
                 bend_cost: float = 0.5):
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.pitch = pitch
        self.layers = layers or ["M1"]
        self.nx = max(int((self.x1 - self.x0) / pitch) + 1, 2)
        self.ny = max(int((self.y1 - self.y0) / pitch) + 1, 2)
        self.via_cost = via_cost
        self.bend_cost = bend_cost
        self.blocked = {l: bytearray(self.nx * self.ny) for l in self.layers}

    # -- 网格 ------------------------------------------------------------- #
    def idx(self, x: float, y: float) -> tuple[int, int]:
        return (int(round((x - self.x0) / self.pitch)),
                int(round((y - self.y0) / self.pitch)))

    def xy(self, i: int, j: int) -> Point:
        return (self.x0 + i * self.pitch, self.y0 + j * self.pitch)

    def _inside(self, i: int, j: int) -> bool:
        return 0 <= i < self.nx and 0 <= j < self.ny

    def add_obstacle(self, layer: str, points, inflate: float = 0.0) -> None:
        """把多边形占的网格标成障碍；inflate 给间距（通常设成走线宽/2）。"""
        from . import geom2d
        if layer not in self.blocked:
            return
        x0, y0, x1, y1 = geom2d.bbox(points)
        i0, j0 = self.idx(x0 - inflate, y0 - inflate)
        i1, j1 = self.idx(x1 + inflate, y1 + inflate)
        grid = self.blocked[layer]
        es = geom2d.edges(points)
        for j in range(max(j0, 0), min(j1, self.ny - 1) + 1):
            for i in range(max(i0, 0), min(i1, self.nx - 1) + 1):
                pt = self.xy(i, j)
                hit = geom2d.point_in_poly(pt, points)
                if not hit and inflate > 0:
                    hit = min(geom2d.seg_point_dist(a, b, pt) for a, b in es) <= inflate
                if hit:
                    grid[j * self.nx + i] = 1

    def free(self, layer: str, i: int, j: int) -> bool:
        return (self._inside(i, j) and
                not self.blocked[layer][j * self.nx + i])

    # -- A* --------------------------------------------------------------- #
    def route(self, src: tuple[str, int, int], dst: tuple[str, int, int],
              max_expand: int = 400_000) -> Path:
        (sl, si, sj), (dl, di, dj) = src, dst
        if sl not in self.layers or dl not in self.layers:
            return Path()
        start = (sl, si, sj, -1)          # 最后一位：来的方向(0..3)，-1 为起点
        goal = (dl, di, dj)

        def h(i, j):
            return abs(i - di) + abs(j - dj)

        openq = [(h(si, sj), 0.0, start)]
        best = {start: 0.0}
        prev: dict = {}
        expanded = 0
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        while openq and expanded < max_expand:
            f, g, node = heapq.heappop(openq)
            layer, i, j, d = node
            if (layer, i, j) == goal:
                return self._build(prev, node)
            if g > best.get(node, math.inf):
                continue
            expanded += 1
            for nd, (dx, dy) in enumerate(dirs):
                ni, nj = i + dx, j + dy
                if not self.free(layer, ni, nj):
                    continue
                extra = 1.0 + (self.bend_cost if d not in (-1, nd) else 0.0)
                nn = (layer, ni, nj, nd)
                ng = g + extra
                if ng < best.get(nn, math.inf):
                    best[nn] = ng
                    prev[nn] = node
                    heapq.heappush(openq, (ng + h(ni, nj), ng, nn))
            if len(self.layers) > 1:                  # 换层（过孔有代价）
                for other in self.layers:
                    if other == layer or not self.free(other, i, j):
                        continue
                    nn = (other, i, j, d)
                    ng = g + self.via_cost
                    if ng < best.get(nn, math.inf):
                        best[nn] = ng
                        prev[nn] = node
                        heapq.heappush(openq, (ng + h(i, j), ng, nn))
        return Path()

    def _build(self, prev: dict, node) -> Path:
        chain = [node]
        while chain[-1] in prev:
            chain.append(prev[chain[-1]])
        chain.reverse()
        path = Path(cost=0.0)
        cur_layer = chain[0][0]
        pts = [self.xy(chain[0][1], chain[0][2])]
        for layer, i, j, _d in chain[1:]:
            p = self.xy(i, j)
            if layer != cur_layer:
                path.vias.append((p[0], p[1], f"VIA{max(1, len(path.segments) + 1)}"))
                path.segments.append((cur_layer, pts))
                pts = [p]
                cur_layer = layer
                continue
            if p != pts[-1]:
                pts.append(p)
        path.segments.append((cur_layer, pts))
        path.length = sum(math.dist(a, b) for _l, ps in path.segments
                          for a, b in zip(ps[:-1], ps[1:]))
        path.cost = path.length / self.pitch + self.via_cost * len(path.vias)
        return path


# --------------------------------------------------------------------------- #
# 原理图级别的正交连线（简单通道分配，不用网格）
# --------------------------------------------------------------------------- #

def orth_connect(a: Point, b: Point, style: str = "L") -> list[Point]:
    """两点正交连线：L（先横后竖）/ Z（中间折）/ U。"""
    (x0, y0), (x1, y1) = a, b
    if style == "Z":
        xm = (x0 + x1) / 2
        return [(x0, y0), (xm, y0), (xm, y1), (x1, y1)]
    if style == "U":
        ym = max(y0, y1) + abs(x1 - x0) * 0.3
        return [(x0, y0), (x0, ym), (x1, ym), (x1, y1)]
    return [(x0, y0), (x1, y0), (x1, y1)]


__all__ = ["Router", "Path", "orth_connect"]
