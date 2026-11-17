"""place —— 布局算法（力导向 + 行合法化）。

原理图/版图都需要的两件事：
1. 让连得多的单元靠近（力导向：网络当弹簧，节点间库仑斥力）
2. 落到合法位置（行合法化：不重叠、贴网格）

算法：Fruchterman-Reingold 变体 + 模拟退火式步长衰减；
      合法化用"按行贪心 + 最近空位"。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Placement:
    pos: dict[str, tuple[float, float]]
    hpwl: float = 0.0          # 半周长线长估计
    overlap: int = 0

    def __getitem__(self, k):
        return self.pos[k]


def hpwl(pos: dict[str, tuple[float, float]], nets: list[set[str]]) -> float:
    total = 0.0
    for net in nets:
        pts = [pos[n] for n in net if n in pos]
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def force_directed(nodes: list[str], nets: list[set[str]],
                   fixed: dict[str, tuple[float, float]] | None = None,
                   size: float = 100.0, iterations: int = 300,
                   seed: int = 7) -> Placement:
    """力导向布局。fixed 里的节点不动（比如 pad/pin）。"""
    rnd = random.Random(seed)
    fixed = fixed or {}
    pos = {n: fixed.get(n) or (rnd.uniform(-size, size), rnd.uniform(-size, size))
           for n in nodes}
    k = size / max(math.sqrt(len(nodes)), 1)          # 理想边长
    temp = size * 0.1
    for it in range(iterations):
        disp = {n: [0.0, 0.0] for n in nodes}
        # 斥力
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) + 1e-6
                f = k * k / d
                ux, uy = dx / d, dy / d
                disp[a][0] += ux * f
                disp[a][1] += uy * f
                disp[b][0] -= ux * f
                disp[b][1] -= uy * f
        # 引力（网络）
        for net in nets:
            members = [n for n in net if n in pos]
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    dx = pos[a][0] - pos[b][0]
                    dy = pos[a][1] - pos[b][1]
                    d = math.hypot(dx, dy) + 1e-6
                    f = d * d / k
                    ux, uy = dx / d, dy / d
                    disp[a][0] -= ux * f
                    disp[a][1] -= uy * f
                    disp[b][0] += ux * f
                    disp[b][1] += uy * f
        # 位移（限幅）
        for n in nodes:
            if n in fixed:
                continue
            dx, dy = disp[n]
            d = math.hypot(dx, dy) + 1e-9
            step = min(d, temp)
            pos[n] = (pos[n][0] + dx / d * step, pos[n][1] + dy / d * step)
        temp *= 0.97
    return Placement(pos, hpwl(pos, nets))


def legalize(pl: Placement, cell: tuple[float, float],
             rows: int = 6, grid: float = 1.0) -> Placement:
    """行合法化：按 y 排序后放进最近的空槽，保证不重叠。"""
    cw, ch = cell
    ncol = max(int(math.ceil(len(pl.pos) / rows)) + 1, 1)
    slots = [(c * cw, r * ch) for r in range(rows) for c in range(ncol)]
    order = sorted(pl.pos, key=lambda n: (pl.pos[n][1], pl.pos[n][0]))
    used: set[tuple[int, int]] = set()
    newpos: dict[str, tuple[float, float]] = {}
    for n in order:
        x, y = pl.pos[n]
        best, bestd = None, math.inf
        for s in slots:
            if s in used:
                continue
            d = abs(s[1] - y) * 2 + abs(s[0] - x)
            if d < bestd:
                best, bestd = s, d
        if best is None:
            break
        used.add(best)
        newpos[n] = (round(best[0] / grid) * grid, round(best[1] / grid) * grid)
    for n in pl.pos:
        newpos.setdefault(n, pl.pos[n])
    return Placement(newpos)


def overlap_count(pos: dict[str, tuple[float, float]], cell: tuple[float, float]) -> int:
    items = list(pos.items())
    n = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (_, a), (_, b) = items[i], items[j]
            if abs(a[0] - b[0]) < cell[0] - 1e-9 and abs(a[1] - b[1]) < cell[1] - 1e-9:
                n += 1
    return n


__all__ = ["Placement", "force_directed", "legalize", "hpwl", "overlap_count"]
