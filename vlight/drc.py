"""drc —— 设计规则检查（lite）。

Virtuoso 里的 DRC 是 Assura/Calibre/PVS 的活；这里实现最常用的一档规则，
够在出 GDS 之前挡住明显错误：

    min_width     同层多边形最小宽度（非相邻对边最小距离）
    min_space     同层两个多边形最小间距
    min_area      多边形最小面积
    grid          坐标是否落在网格上
    enclosure     via 必须被指定金属包住 margin

规则用 JSON 给（`Deck.from_json`），也可以从 techfile/layermap 生成默认值。
几何全在 `geom2d` 里自实现，不依赖 klayout。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import geom2d
from .vdb import Layout, Shape


@dataclass
class Rule:
    min_width: float = 0.0
    min_space: float = 0.0
    min_area: float = 0.0

    def merge(self, other: "Rule") -> "Rule":
        return Rule(max(self.min_width, other.min_width),
                    max(self.min_space, other.min_space),
                    max(self.min_area, other.min_area))


@dataclass
class Violation:
    kind: str
    layer: str
    value: float
    limit: float
    at: tuple[float, float, float, float] = (0, 0, 0, 0)
    message: str = ""

    def __str__(self) -> str:
        where = f"@({self.at[0]*1e6:.3f},{self.at[1]*1e6:.3f})um"
        return (f"[{self.kind}] {self.layer}: {self.message or ''} "
                f"value={self.value*1e6:.4f}um limit={self.limit*1e6:.4f}um {where}")


@dataclass
class Deck:
    rules: dict[str, Rule] = field(default_factory=dict)
    grid: float = 1e-9                       # 1 nm，与 stream out 精度一致
    enclosure: dict[str, tuple[str, float]] = field(default_factory=dict)
    default: Rule | None = None

    # -- 构造 ------------------------------------------------------------- #
    def rule(self, layer: str) -> Rule | None:
        if layer in self.rules:
            return self.rules[layer]
        return self.default

    def add(self, layer: str, min_width: float = 0.0, min_space: float = 0.0,
            min_area: float = 0.0) -> Rule:
        r = self.rules.get(layer, Rule())
        r.min_width = max(r.min_width, min_width)
        r.min_space = max(r.min_space, min_space)
        r.min_area = max(r.min_area, min_area)
        self.rules[layer] = r
        return r

    @classmethod
    def from_json(cls, path: str | Path) -> "Deck":
        raw = json.loads(Path(path).read_text())
        deck = cls(grid=raw.get("grid", 1e-9))
        for layer, d in raw.get("layers", {}).items():
            deck.add(layer, **{k: v for k, v in d.items()
                               if k in ("min_width", "min_space", "min_area")})
        for via, d in raw.get("enclosure", {}).items():
            deck.enclosure[via] = (d["metal"], d["margin"])
        if "default" in raw:
            deck.default = Rule(**raw["default"])
        return deck

    def to_json(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps({
            "grid": self.grid,
            "default": vars(self.default) if self.default else None,
            "layers": {k: vars(v) for k, v in self.rules.items()},
            "enclosure": {k: {"metal": m, "margin": v}
                          for k, (m, v) in self.enclosure.items()},
        }, indent=1) + "\n")
        return p

    @classmethod
    def from_techfile(cls, tech, width: float = 0.1e-6, space: float = 0.1e-6,
                      area: float = 0.05e-12) -> "Deck":
        deck = cls()
        for name in tech.layers:
            if name in ("ref", "text", "annotate", "grid", "background"):
                continue
            deck.add(name, width, space, area)
        return deck


def _bbox(poly) -> tuple[float, float, float, float]:
    return geom2d.bbox(poly)


def check(layout: Layout, deck: Deck, max_report: int = 500,
          layers: Iterable[str] | None = None) -> list[Violation]:
    """跑一遍规则，返回违规列表（超过 max_report 就截断）。"""
    keep = set(layers) if layers else None
    shapes = [s for s in layout.shapes if len(s.points) >= 3
              and (keep is None or s.layer in keep)]
    out: list[Violation] = []

    def add(v: Violation) -> None:
        if len(out) < max_report:
            out.append(v)

    # 1) 面积 / 宽度 / 网格
    for s in shapes:
        r = deck.rule(s.layer)
        if r is None:
            continue
        a = geom2d.area(s.points)
        if r.min_area and a < r.min_area:
            add(Violation("min_area", s.layer, a, r.min_area, _bbox(s.points),
                          "多边形面积过小"))
        if r.min_width:
            w = geom2d.min_width(s.points)
            if w < r.min_width - 1e-15:
                add(Violation("min_width", s.layer, w, r.min_width, _bbox(s.points),
                              "线宽不足"))
        if deck.grid:
            for x, y in s.points:
                if (abs(x / deck.grid - round(x / deck.grid)) > 1e-6 or
                        abs(y / deck.grid - round(y / deck.grid)) > 1e-6):
                    add(Violation("grid", s.layer, deck.grid, deck.grid,
                                  _bbox(s.points), "坐标不在网格上"))
                    break

    # 2) 间距（同层两两）
    by_layer: dict[str, list[Shape]] = {}
    for s in shapes:
        by_layer.setdefault(s.layer, []).append(s)
    for layer, group in by_layer.items():
        r = deck.rule(layer)
        if r is None or not r.min_space:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                p, q = group[i].points, group[j].points
                if not geom2d.bbox_overlap(_bbox(p), _bbox(q), r.min_space):
                    continue
                d = geom2d.poly_poly_dist(p, q)
                if 0.0 < d < r.min_space - 1e-15:
                    add(Violation("min_space", layer, d, r.min_space, _bbox(p),
                                  "间距不足"))

    # 3) via 被金属包住
    for via, (metal, margin) in deck.enclosure.items():
        vias = by_layer.get(via, [])
        metals = by_layer.get(metal, [])
        for v in vias:
            vb = _bbox(v.points)
            ok = False
            for m in metals:
                mb = _bbox(m.points)
                if (mb[0] <= vb[0] - margin + 1e-15 and mb[1] <= vb[1] - margin + 1e-15
                        and mb[2] >= vb[2] + margin - 1e-15
                        and mb[3] >= vb[3] + margin - 1e-15):
                    ok = True
                    break
            if not ok:
                add(Violation("enclosure", via, 0.0, margin, vb,
                              f"{via} 未被 {metal} 包住 {margin*1e6:.3f}um"))
    return out


def report(violations: list[Violation]) -> str:
    if not violations:
        return "DRC PASS（0 违规）"
    lines = [f"DRC FAIL：{len(violations)} 违规"]
    for v in violations[:40]:
        lines.append("  " + str(v))
    if len(violations) > 40:
        lines.append(f"  ... 还有 {len(violations) - 40} 条")
    return "\n".join(lines)


__all__ = ["Rule", "Violation", "Deck", "check", "report"]
