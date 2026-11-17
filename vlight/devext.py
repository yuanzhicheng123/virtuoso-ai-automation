"""devext —— 从版图提取器件（MOS），补上 LVS 缺的那一半。

算法（和 KLayout 的 extract_devices 同一个思路）：
    栅      = OD ∩ PO
    源漏    = OD − PO（被栅切成两块）
    沟道宽 W = 栅在 OD 短边方向的长度
    沟道长 L = 栅在电流方向的长度
    网络名   = 落在该区域里的 label（没有就 N$k）

输出标准 `spicein.Netlist`，可以喂给 `lvs.compare` 和 `spice.Circuit`。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import geom2d, polybool
from .spicein import Element, Netlist
from .vdb import Layout


@dataclass
class Mos:
    name: str
    gate_box: tuple
    od_box: tuple
    w: float
    l: float
    nets: tuple[str, str, str, str]      # D G S B
    kind: str = "nmos"


def _labels_in(layout: Layout, box, layers=None) -> str | None:
    x0, y0, x1, y1 = box
    for s in layout.shapes:
        if s.kind not in ("label", "text") or not s.text or not s.points:
            continue
        if layers and s.layer not in layers:
            continue
        x, y = s.points[0]
        if x0 - 1e-12 <= x <= x1 + 1e-12 and y0 - 1e-12 <= y <= y1 + 1e-12:
            return s.text
    return None


def extract_mos(layout: Layout, od: str = "OD", po: str = "PO",
                label_layers=("OD", "M1", "PO", "M2"), prefix: str = "M") -> Netlist:
    od_shapes = [s for s in layout.shapes if s.layer == od and len(s.points) >= 3]
    po_shapes = [s for s in layout.shapes if s.layer == po and len(s.points) >= 3]
    nl = Netlist(path=f"extracted:{layout.cell}")
    nl.globals = ["0"]
    count = 0
    used_nets: dict[str, str] = {}

    def netname(box, hint: str) -> str:
        name = _labels_in(layout, box, label_layers)
        if name:
            used_nets[name] = name
            return name
        key = f"{hint}"
        if key not in used_nets:
            used_nets[key] = f"N${len(used_nets) + 1}"
        return used_nets[key]

    for oi, ods in enumerate(od_shapes):
        ob = geom2d.bbox(ods.points)
        for poi, pos in enumerate(po_shapes):
            gate = polybool.intersection_area(ods.points, pos.points)
            if gate <= 0:
                continue
            gb = geom2d.bbox(pos.points)
            # 栅与 OD 的公共框
            gx0, gy0 = max(ob[0], gb[0]), max(ob[1], gb[1])
            gx1, gy1 = min(ob[2], gb[2]), min(ob[3], gb[3])
            if gx1 <= gx0 or gy1 <= gy0:
                continue
            # 电流方向 = OD 长边；L 取栅在长边方向的厚度，W 取短边
            od_w, od_h = ob[2] - ob[0], ob[3] - ob[1]
            if od_w >= od_h:
                length = gx1 - gx0
                width = gy1 - gy0
                left = (ob[0], ob[1], gx0, ob[3])
                right = (gx1, ob[1], ob[2], ob[3])
            else:
                length = gy1 - gy0
                width = gx1 - gx0
                left = (ob[0], ob[1], ob[2], gy0)
                right = (ob[0], gy1, ob[2], ob[3])
            d_net = netname(right, f"od{oi}_r")
            s_net = netname(left, f"od{oi}_l")
            g_net = netname((gx0, gy0, gx1, gy1), f"po{poi}")
            b_net = _labels_in(layout, ob, ("BULK",)) or "0"
            count += 1
            mos = Mos(f"{prefix}{count}", (gx0, gy0, gx1, gy1), ob,
                      max(width, 1e-9), max(length, 1e-9),
                      (d_net, g_net, s_net, b_net))
            nl.elements.append(Element(
                mos.name, list(mos.nets), "nmos",
                {"w": f"{mos.w:.6g}", "l": f"{mos.l:.6g}"}))
    return nl


def stats(nl: Netlist) -> dict:
    mos = [e for e in nl.elements if e.master == "nmos"]
    nets = {n for e in mos for n in e.nodes}
    return {"devices": len(mos), "nets": len(nets),
            "nets_named": sorted(n for n in nets if not n.startswith("N$"))}


__all__ = ["extract_mos", "Mos", "stats"]
