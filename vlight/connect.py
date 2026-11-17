"""connect —— 版图连接性提取（Layout XL / 提取器的核心那一步）。

输入：`vdb.Layout`（各层多边形 + label/pin）
输出：`Connectivity`：每个电气节点由哪些图形构成、叫什么名字

规则：
* 同层多边形接触/重叠 -> 同一节点
* via 层图形与上下层金属重叠 -> 把两层并起来
* label/pin 落在某图形上 -> 给该节点命名

`lvs(schematic, connectivity)` 再把提取结果和原理图对一遍（LVS-lite：
按名字对 net、抓短路/开路），不做器件识别。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import geom2d
from .vdb import Layout, Schematic, Shape

# 默认的 via 栈（层名约定 VIA<n> 连接 M<n-1> 与 M<n>），可覆盖
DEFAULT_STACK = [("VIA1", "M1", "M2"), ("VIA2", "M2", "M3"), ("VIA3", "M3", "M4"),
                 ("VIA4", "M4", "M5"), ("VIA5", "M5", "M6"), ("VIA6", "M6", "M7"),
                 ("VIA7", "M7", "M8"), ("VIA8", "M8", "M9")]


class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.p)):
            out.setdefault(self.find(i), []).append(i)
        return out


@dataclass
class Connectivity:
    nets: dict[str, list[int]] = field(default_factory=dict)     # net -> shape idx
    shape_net: dict[int, str] = field(default_factory=dict)
    shapes: list[Shape] = field(default_factory=list)
    labels: list[tuple[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def net_of_shape(self, idx: int) -> str | None:
        return self.shape_net.get(idx)

    def net_count(self) -> int:
        return len(self.nets)

    def summary(self) -> dict:
        return {"nets": len(self.nets), "shapes": len(self.shapes),
                "labels": len(self.labels),
                "named": sorted(n for n in self.nets if not n.startswith("N$"))}


def _stack(via_layers: dict[str, tuple[str, str]] | None) -> dict[str, tuple[str, str]]:
    if via_layers:
        return via_layers
    return {via: (lo, hi) for via, lo, hi in DEFAULT_STACK}


def extract(layout: Layout, via_layers: dict[str, tuple[str, str]] | None = None,
            tol: float = 0.0) -> Connectivity:
    """把版图拆成电气节点。"""
    stack = _stack(via_layers)
    shapes = [s for s in layout.shapes if len(s.points) >= 3]
    conn = Connectivity(shapes=shapes)
    dsu = _DSU(len(shapes))

    by_layer: dict[str, list[int]] = {}
    for i, s in enumerate(shapes):
        by_layer.setdefault(s.layer, []).append(i)

    # 1) 同层接触
    for layer, ids in by_layer.items():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, j = ids[a], ids[b]
                if geom2d.polys_touch(shapes[i].points, shapes[j].points, tol):
                    dsu.union(i, j)

    # 2) via 连接上下层
    for via, (lo, hi) in stack.items():
        for vi in by_layer.get(via, []):
            vs = shapes[vi].points
            for layer in (lo, hi):
                for oi in by_layer.get(layer, []):
                    if geom2d.polys_touch(vs, shapes[oi].points, tol):
                        dsu.union(vi, oi)

    # 3) label 命名
    groups = dsu.groups()
    names: dict[int, str] = {}
    for li, s in enumerate(layout.shapes):
        if s.kind not in ("label", "text") or not s.text:
            continue
        pt = s.points[0] if s.points else (0.0, 0.0)
        hit = None
        for i, sh in enumerate(shapes):
            if s.layer and sh.layer == s.layer and geom2d.point_in_poly(pt, sh.points):
                hit = i
                break
        if hit is None:                        # 层没对上就任意层找
            for i, sh in enumerate(shapes):
                if geom2d.point_in_poly(pt, sh.points):
                    hit = i
                    break
        if hit is None:
            conn.warnings.append(f"label {s.text!r} 没落在任何图形上")
            continue
        root = dsu.find(hit)
        if root in names and names[root] != s.text:
            conn.warnings.append(
                f"短路: {names[root]} 与 {s.text} 落在同一个节点上")
            continue
        names[root] = s.text

    # 4) 汇总
    for root, members in groups.items():
        name = names.get(root) or f"N${len(conn.nets) + 1}"
        while name in conn.nets:
            name += "_"
        conn.nets[name] = members
        for m in members:
            conn.shape_net[m] = name
    for root, nm in names.items():
        conn.labels.append((nm, root))
    return conn


# --------------------------------------------------------------------------- #
# LVS-lite：原理图 vs 版图连接性
# --------------------------------------------------------------------------- #

@dataclass
class LVSReport:
    ok: bool = True
    matched: list[str] = field(default_factory=list)
    missing_in_layout: list[str] = field(default_factory=list)
    extra_in_layout: list[str] = field(default_factory=list)
    shorts: list[str] = field(default_factory=list)
    opens: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"ok": self.ok, "matched": len(self.matched),
                "missing": self.missing_in_layout, "extra": self.extra_in_layout,
                "shorts": self.shorts, "opens": self.opens}

    def text(self) -> str:
        lines = ["LVS " + ("PASS" if self.ok else "FAIL")]
        if self.matched:
            lines.append(f"  matched nets: {len(self.matched)}")
        for label, items in (("missing in layout", self.missing_in_layout),
                             ("extra in layout", self.extra_in_layout),
                             ("shorts", self.shorts), ("opens", self.opens),
                             ("warnings", self.warnings)):
            for it in items:
                lines.append(f"  {label}: {it}")
        return "\n".join(lines)


def lvs(sch: Schematic, conn: Connectivity,
        ignore: tuple[str, ...] = ("0", "gnd", "vdd", "vss")) -> LVSReport:
    """按 net 名字对账。原理图侧取 pin + 有端子的 net，版图侧取 label。"""
    rep = LVSReport()
    sch_nets = {p.name for p in sch.pins}
    sch_nets |= {n.name for n in sch.nets if n.terms}
    sch_nets = {n for n in sch_nets if n not in ignore}
    lay_nets = {n for n in conn.nets if not n.startswith("N$")} - set(ignore)

    rep.matched = sorted(sch_nets & lay_nets)
    rep.missing_in_layout = sorted(sch_nets - lay_nets)
    rep.extra_in_layout = sorted(lay_nets - sch_nets)

    # 短路：label 数量 < 节点数说明两个名字落在了同一个 net 上（extract 时已记录）
    for w in conn.warnings:
        if w.startswith("短路"):
            rep.shorts.append(w.split(": ", 1)[-1])
    # 开路：原理图里有端子但版图没提取到任何图形
    for n in sch_nets & lay_nets:
        if not conn.nets.get(n):
            rep.opens.append(n)

    rep.warnings = [w for w in conn.warnings if not w.startswith("短路")]
    rep.ok = not (rep.missing_in_layout or rep.extra_in_layout or rep.shorts or rep.opens)
    return rep


__all__ = ["extract", "Connectivity", "lvs", "LVSReport", "DEFAULT_STACK"]
