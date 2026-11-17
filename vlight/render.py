"""render —— 原理图/版图的 SVG 视图（轻量 GUI 的替代：浏览器里看）。

Virtuoso 的图形编辑是重量级的；这里把 cellview 直接渲染成 SVG，
既能嵌报告，也能 `vlight view` 起个静态服务在浏览器里看。
"""
from __future__ import annotations

import html
import math
from pathlib import Path

from .vdb import Layout, Schematic, TechFile

# 一层一套颜色，按层名 hash 稳定分配
_PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
            "#8cd17d", "#86bcb6", "#d37295", "#a0cbe8", "#ffbe7d"]


def layer_color(layer: str) -> str:
    h = 0
    for ch in layer:
        h = (h * 131 + ord(ch)) & 0xFFFF
    return _PALETTE[h % len(_PALETTE)]


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def schematic_svg(sch: Schematic, width: int = 900, box=(70, 44),
                  show_nets: bool = True) -> str:
    """把原理图画成 SVG：实例画方块，网络画连线+名字。"""
    insts = [i for i in sch.insts]
    if insts:
        xs = [i.xy[0] for i in insts]
        ys = [i.xy[1] for i in insts]
        x0, x1 = min(xs) - 60, max(xs) + 60 + box[0]
        y0, y1 = min(ys) - 40, max(ys) + 40 + box[1]
    else:
        x0, y0, x1, y1 = 0, 0, 400, 200
    w = max(x1 - x0, 1)
    h = max(y1 - y0, 1)
    scale = width / w
    height = int(h * scale) + 40

    def sx(x: float) -> float:
        return (x - x0) * scale

    def sy(y: float) -> float:
        return height - 20 - (y - y0) * scale

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" viewBox="0 0 {width} {height}" '
             f'font-family="monospace">',
             f'<rect width="{width}" height="{height}" fill="#fbfbfd"/>']
    pos = {i.name: (sx(i.xy[0]), sy(i.xy[1])) for i in insts}
    bw, bh = box[0] * scale, box[1] * scale

    if show_nets:
        for net in sch.nets:
            pts = [pos[i] for (i, _) in net.terms if i in pos]
            for k, (x, y) in enumerate(pts):
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="#888"/>')
            if len(pts) >= 2:
                d = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}"
                             for k, (x, y) in enumerate(pts))
                dash = "" if len(net.terms) > 1 else ' stroke-dasharray="3 3"'
                parts.append(f'<path d="{d}" fill="none" stroke="#7a8699" '
                             f'stroke-width="1.1"{dash}/>')
                mx = sum(p[0] for p in pts) / len(pts)
                my = sum(p[1] for p in pts) / len(pts)
                parts.append(f'<text x="{mx:.1f}" y="{my - 4:.1f}" font-size="9" '
                             f'fill="#5b6675" text-anchor="middle">{_esc(net.name)}</text>')
            elif pts:
                x, y = pts[0]
                parts.append(f'<text x="{x + 6:.1f}" y="{y - 6:.1f}" font-size="9" '
                             f'fill="#b04">{_esc(net.name)} (dangling)</text>')

    for i in insts:
        x, y = pos[i.name]
        parts.append(f'<rect x="{x - bw / 2:.1f}" y="{y - bh / 2:.1f}" '
                     f'width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="#fff" '
                     f'stroke="#2f3b52" stroke-width="1.1"/>')
        parts.append(f'<text x="{x:.1f}" y="{y - 2:.1f}" font-size="10" '
                     f'text-anchor="middle" fill="#1c2433">{_esc(i.name)}</text>')
        parts.append(f'<text x="{x:.1f}" y="{y + 10:.1f}" font-size="8" '
                     f'text-anchor="middle" fill="#6b7688">{_esc(i.cell)}</text>')

    for p in sch.pins:
        parts.append(f'<text x="{sx(x0) + 4:.1f}" y="{sy(y0) - 6:.1f}" font-size="9" '
                     f'fill="#0a6">PIN {_esc(p.name)} ({_esc(p.direction)})</text>')
        break
    title = f"{sch.lib}/{sch.cell}  insts={len(sch.insts)} nets={len(sch.nets)}"
    parts.append(f'<text x="8" y="14" font-size="11" fill="#334">{_esc(title)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def layout_svg(lay: Layout, tech: TechFile | None = None, width: int = 900,
               legend: bool = True) -> str:
    bb = lay.bbox()
    if bb is None:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40"/>'
    x0, y0, x1, y1 = bb
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    scale = width / w
    height = int(h * scale) + 30

    def sx(x):
        return (x - x0) * scale

    def sy(y):
        return height - 20 - (y - y0) * scale

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" viewBox="0 0 {width} {height}" font-family="monospace">',
             f'<rect width="{width}" height="{height}" fill="#12151c"/>']
    for s in lay.shapes:
        if len(s.points) < 3:
            continue
        pts = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in s.points)
        parts.append(f'<polygon points="{pts}" fill="{layer_color(s.layer)}" '
                     f'fill-opacity="0.55" stroke="{layer_color(s.layer)}" '
                     f'stroke-width="0.6"/>')
    if legend:
        layers = lay.layers()
        for k, name in enumerate(layers[:14]):
            col = layer_color(name)
            parts.append(f'<rect x="{8}" y="{16 + k * 12}" width="9" height="9" '
                         f'fill="{col}" fill-opacity="0.8"/>')
            parts.append(f'<text x="{22}" y="{24 + k * 12}" font-size="9" '
                         f'fill="#cfd6e4">{_esc(name)}</text>')
    parts.append(f'<text x="{width - 8}" y="16" font-size="10" fill="#9aa6bd" '
                 f'text-anchor="end">{_esc(lay.cell)}  {w * 1e6:.1f} x {h * 1e6:.1f} um</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_svg(svg: str, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(svg)
    return p


def write_html(sections: list[tuple[str, str]], path: str | Path, title: str = "vlight") -> Path:
    body = "\n".join(f"<h3>{_esc(t)}</h3>\n{s}" for t, s in sections)
    doc = (f"<!doctype html><meta charset='utf-8'><title>{_esc(title)}</title>"
           f"<style>body{{background:#0e1117;color:#d6dbe6;font-family:monospace;"
           f"margin:24px}}h3{{font-weight:400;color:#8ab4f8}}</style>{body}")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc)
    return p


__all__ = ["schematic_svg", "layout_svg", "write_svg", "write_html", "layer_color"]
