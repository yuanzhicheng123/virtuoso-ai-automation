"""compat.check —— 与 KLayout 的逐项对照（"绝对兼容"的度量）。

每个 check 都拿同一份输入跑两边，比较数值/结构。输出 JSON + Markdown。

    python3 -m compat.check
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compat import klayout as kl                                # noqa: E402
from vlight import drc, devext, gdsio, polybool                 # noqa: E402
from vlight.region import Region                                # noqa: E402
from vlight.skilldump import import_tech                        # noqa: E402
from vlight.vdb import Instance, Layout, Library, Shape, TechFile  # noqa: E402

OUT = ROOT / "out" / "compat"
OUT.mkdir(parents=True, exist_ok=True)


@dataclass
class Check:
    id: str
    axis: str
    desc: str
    fn: object


def _tech_for(layers) -> TechFile:
    tech = TechFile()
    for i, name in enumerate(layers, start=1):
        tech.add(name, i)
        tech.gds_map[(name, "drawing")] = (i, 0)
    return tech


# --------------------------------------------------------------------------- #
# GDS 双向
# --------------------------------------------------------------------------- #

def _demo_library() -> tuple[Library, TechFile]:
    lib = Library("cmp", OUT / "_lib")
    lay = Layout("TOP", "cmp")
    lay.add("M1", [(0, 0), (10e-6, 0), (10e-6, 5e-6), (0, 5e-6)])
    lay.add("M1", [(20e-6, 0), (30e-6, 0), (30e-6, 5e-6), (20e-6, 5e-6)])
    lay.add("M2", [(0, 10e-6), (30e-6, 10e-6), (30e-6, 12e-6), (0, 12e-6)])
    lay.shapes.append(Shape(kind="text", layer="M1", text="VOUT",
                            points=[(5e-6, 2.5e-6)]))
    lay.shapes.append(Shape(kind="path", layer="M2", width=0.4e-6,
                            points=[(2e-6, 11e-6), (20e-6, 11e-6)]))
    sub = Layout("SUB", "cmp")
    sub.add("M1", [(0, 0), (1e-6, 0), (1e-6, 1e-6), (0, 1e-6)])
    lay.instances.append(Instance("A1", "cmp", "SUB", "layout", (40e-6, 0),
                                  cols=3, rows=2, colspace=2e-6, rowspace=2e-6))
    lib.cell("TOP", create=True).layouts["layout"] = lay
    lib.cell("SUB", create=True).layouts["layout"] = sub
    return lib, _tech_for(["M1", "M2"])


def c_gds_write_read():
    lib, tech = _demo_library()
    p = gdsio.write_gds(OUT / "cmp.gds", lib, "TOP", tech)
    info = kl.info(p)
    our = gdsio.read_gds(p)
    n_shapes = sum(len(c["shapes"]) for c in our["cells"].values())
    ok = (info["cells"] == 2 and info["layers"] == [(1, 0), (2, 0)]
          and info["texts"] == 1)
    return ok, (f"KLayout: {info['cells']} cells, 层 {info['layers']}, "
                f"文字 {info['texts']}；本实现读回 {n_shapes} 个元素")


def c_gds_text_path():
    lib, tech = _demo_library()
    p = gdsio.write_gds(OUT / "cmp.gds", lib, "TOP", tech)
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(str(p))
    texts, paths = [], []
    for cell in ly.each_cell():
        for li in ly.layer_indexes():
            for sh in cell.shapes(li).each():
                if sh.is_text():
                    texts.append(sh.text.string)
                if sh.is_path():
                    paths.append(sh.path.width * ly.dbu)
    ok = texts == ["VOUT"] and paths and abs(paths[0] - 0.4) < 1e-9
    return ok, f"文字 {texts}，线宽 {paths} um"


def c_gds_aref():
    lib, tech = _demo_library()
    p = gdsio.write_gds(OUT / "cmp.gds", lib, "TOP", tech)
    flat = kl.flatten_count(p)
    # TOP 本级 3 个多边形 + 1 text + 1 path，被扁平化后 + SUB 6 个多边形
    ok = flat == 3 + 1 + 1 + 6
    return ok, f"KLayout 扁平化后 {flat} 个元素（期望 11）"


def c_klayout_write_ours_read():
    p = kl.write_gds(OUT / "from_kl.gds", {
        "TOP": {"boxes": [(1, 0, 0, 0, 10, 5), (2, 0, 0, 10, 30, 12)],
                "texts": [(1, 0, "IN", 1, 1)],
                "refs": [("SUB", 40, 0, 2, 2, 2, 2)]},
        "SUB": {"boxes": [(1, 0, 0, 0, 1, 1)]},
    })
    lib = Library("from_kl", OUT / "_from_kl")
    got = gdsio.load_gds_into(lib, p)
    top = lib.cell("TOP").layouts["layout"]
    ok = (len(top.shapes) == 3 and len(top.instances) == 1
          and top.instances[0].cols == 2 and top.instances[0].rows == 2
          and abs(top.instances[0].colspace - 2e-6) < 1e-12)
    return ok, f"读回 {len(top.shapes)} 图形 / {len(top.instances)} 实例（阵列 {top.instances[0].cols}x{top.instances[0].rows}）"


# --------------------------------------------------------------------------- #
# 几何：Region 运算
# --------------------------------------------------------------------------- #

def _poly_set():
    a = [(0, 0), (10, 0), (10, 10), (0, 10)]
    b = [(5, 0), (15, 0), (15, 10), (5, 10)]
    c = [(0, 12), (4, 12), (4, 16), (0, 16)]
    return [a, b, c]


def c_region_bool():
    polys = _poly_set()
    r1, r2 = Region(polys[:2]), Region(polys[2:])
    import klayout.db as kdb
    def kr(ps):
        r = kdb.Region()
        for p in ps:
            r.insert(kdb.DPolygon([kdb.DPoint(x, y) for x, y in p]))
        return r
    ka, kb = kr(polys[:2]), kr(polys[2:])
    ours = {"or": (r1 | r2).area(), "and": (r1 & r2).area(),
            "sub": (r1 - r2).area(), "xor": (r1 ^ r2).area()}
    ref = {"or": (ka + kb).area(), "and": (ka & kb).area(),
           "sub": (ka - kb).area(), "xor": (ka ^ kb).area()}
    ok = all(abs(ours[k] - ref[k]) < 1e-6 for k in ref)
    return ok, f"本实现 {ours} vs KLayout {ref}"


def c_region_sizing():
    poly = [(0, 0), (10, 0), (10, 5), (0, 5)]
    import klayout.db as kdb
    r = kdb.Region(kdb.DPolygon([kdb.DPoint(x, y) for x, y in poly]))
    ours = Region([poly]).sized(1.0).area()
    ref = r.sized(1.0).area()
    ok = abs(ours - ref) < 1e-6
    return ok, f"外扩 1um：本实现 {ours:.3f} vs KLayout {ref:.3f}（解析 84）"


def _um(pts):
    return [(x * 1e-6, y * 1e-6) for x, y in pts]


def c_width_space():
    polys = [_um([(0, 0), (0.05, 0), (0.05, 10), (0, 10)]),
             _um([(10, 0), (20, 0), (20, 10), (10, 10)]),
             _um([(20.1, 0), (30, 0), (30, 10), (20.1, 10)])]
    lib = Library("ws", OUT / "_ws")
    lay = Layout("T", "ws")
    for p in polys:
        lay.add("M1", p)
    lib.cell("T", create=True).layouts["layout"] = lay
    tech = _tech_for(["M1"])
    p = gdsio.write_gds(OUT / "ws.gds", lib, "T", tech)
    ref = kl.drc(p, 'r = input(1,0)\n'
                    'puts "KL width #{r.width(0.1).count}"\n'
                    'puts "KL space #{r.space(0.2).count}"\n')
    reg = Region(polys)
    ours_w = len(reg.width_check(0.1e-6))
    ours_s = len(reg.space_check(0.2e-6))
    ok = ours_w == ref.get("width", -1) and ours_s == ref.get("space", -1)
    return ok, f"本实现 width {ours_w} / space {ours_s}；KLayout {ref.get('width')}/{ref.get('space')}"


# --------------------------------------------------------------------------- #
# DRC 脚本对照
# --------------------------------------------------------------------------- #

def c_drc_script():
    polys = [_um([(0, 0), (0.05, 0), (0.05, 10), (0, 10)]),
             _um([(10, 0), (20, 0), (20, 10), (10, 10)]),
             _um([(20.1, 0), (30, 0), (30, 10), (20.1, 10)])]
    lib = Library("drc", OUT / "_drc")
    lay = Layout("T", "drc")
    for p in polys:
        lay.add("M1", p)
    lib.cell("T", create=True).layouts["layout"] = lay
    tech = _tech_for(["M1"])
    gds = gdsio.write_gds(OUT / "drc.gds", lib, "T", tech)
    ref = kl.drc(gds, 'r = input(1,0)\n'
                      'puts "KL width #{r.width(0.1).count}"\n'
                      'puts "KL space #{r.space(0.2).count}"\n')
    deck = drc.Deck()
    deck.add("M1", min_width=0.1e-6, min_space=0.2e-6, min_area=0.0)
    viol = drc.check(lay, deck)
    ours = {"width": sum(1 for v in viol if v.kind == "min_width"),
            "space": sum(1 for v in viol if v.kind == "min_space")}
    ok = ours == {"width": ref.get("width"), "space": ref.get("space")}
    return ok, f"本实现 {ours}；KLayout {ref.get('width')}/{ref.get('space')}"


# --------------------------------------------------------------------------- #
# 器件提取 / LVS
# --------------------------------------------------------------------------- #

def _mos_layout() -> Layout:
    L = Layout("MOS")
    L.add("OD", _um([(0, 0), (6, 0), (6, 1), (0, 1)]))
    L.add("PO", _um([(2, -0.2), (2.1, -0.2), (2.1, 1.2), (2, 1.2)]))
    L.add("OD", _um([(10, 0), (16, 0), (16, 1), (10, 1)]))
    L.add("PO", _um([(12, -0.2), (12.1, -0.2), (12.1, 1.2), (12, 1.2)]))
    return L


def c_mos_extract():
    L = _mos_layout()
    lib = Library("mos", OUT / "_mos")
    lib.cell("MOS", create=True).layouts["layout"] = L
    tech = _tech_for(["OD", "PO"])
    gds = gdsio.write_gds(OUT / "mos.gds", lib, "MOS", tech)
    ref = kl.drc(gds, 'od = input(1,0)\npo = input(2,0)\n'
                      'g = od & po\nputs "KL gates #{g.count}"\n')
    nl = devext.extract_mos(L)
    ok = devext.stats(nl)["devices"] == ref.get("gates")
    return ok, f"本实现 {devext.stats(nl)['devices']} 个 MOS；KLayout 栅区 {ref.get('gates')}"


# --------------------------------------------------------------------------- #
# 层映射
# --------------------------------------------------------------------------- #

def c_layer_map():
    tech = import_tech(ROOT / "recon" / "tsmcN65.layermap")
    pairs = {k: v for k, v in tech.gds_map.items()}
    ok = pairs.get(("M1", "drawing")) == (31, 0) and pairs.get(("M9", "drawing")) == (39, 60)
    return ok, f"M1={pairs.get(('M1','drawing'))} M9={pairs.get(('M9','drawing'))}（共 {len(pairs)} 条）"


def c_hierarchy():
    lib, tech = _demo_library()
    mid = Layout("MID", "cmp")
    mid.instances.append(Instance("S1", "cmp", "SUB", "layout", (0, 0)))
    lib.cell("MID", create=True).layouts["layout"] = mid
    top = lib.cell("TOP").layouts["layout"]
    top.instances = [i for i in top.instances if i.cell != "SUB"]
    top.instances.append(Instance("M1x", "cmp", "MID", "layout", (40e-6, 0)))
    p = gdsio.write_gds(OUT / "hier.gds", lib, "TOP", tech)
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(str(p))
    kc = sorted(c.name for c in ly.each_cell())
    flat = kl.flatten_count(p)
    # 本实现递归展开后的图形数
    def count(cell):
        lay = lib.cell(cell).layouts["layout"]
        n = len(lay.shapes)
        return n + sum(count(i.cell) for i in lay.instances if i.cell in lib.cells)
    ours = count("TOP")
    ok = kc == ["MID", "SUB", "TOP"] and flat == ours
    return ok, f"KLayout cells {kc}，扁平化 {flat}；本实现递归 {ours}"


def c_lvs():
    L = _mos_layout()
    labels = [("OUT1", 0.5, 0.5), ("VDD", 5.0, 0.5),
              ("OUT2", 10.5, 0.5), ("GND", 15.0, 0.5),
              ("IN1", 2.05, 1.1), ("IN2", 12.05, 1.1)]
    for text, x, y in labels:
        L.shapes.append(Shape(kind="label", layer="OD", text=text,
                              points=[(x * 1e-6, y * 1e-6)]))
    from vlight import lvs as lvsm
    from vlight.spicein import Netlist, Element
    nl = devext.extract_mos(L)
    ref = Netlist(path="schematic")
    ref.elements.append(Element("M1", ["VDD", "IN1", "OUT1", "0"], "nmos", {}))
    ref.elements.append(Element("M2", ["GND", "IN2", "OUT2", "0"], "nmos", {}))
    rep = lvsm.compare(lvsm.Graph.from_netlist(ref), lvsm.Graph.from_netlist(nl))
    return rep.ok, f"提取 {devext.stats(nl)['devices']} 个器件；LVS {'MATCH' if rep.ok else rep.text()[:60]}"


def c_oasis():
    lib, tech = _demo_library()
    p = gdsio.write_oasis(OUT / "cmp.oas", lib, "TOP", tech)
    info = kl.info(p)
    g = gdsio.write_gds(OUT / "cmp2.gds", lib, "TOP", tech)
    gref = kl.info(g)
    ok = (info["cells"] == gref["cells"] and info["layers"] == gref["layers"]
          and info["texts"] == gref["texts"])
    return ok, f"OASIS {Path(p).stat().st_size}B：cells {info['cells']}，层 {info['layers']}，文字 {info['texts']}"


CHECKS = [
    Check("data.oasis", "data", "OASIS 输出（经 KLayout 桥）", c_oasis),
    Check("data.hierarchy", "data", "多层 SREF 层次", c_hierarchy),
    Check("verify.lvs", "verification", "版图提取 → 网表 LVS", c_lvs),
    Check("data.gds_write", "data", "本实现写 GDS → KLayout 读", c_gds_write_read),
    Check("data.text_path", "data", "TEXT/PATH 语义", c_gds_text_path),
    Check("data.aref", "data", "AREF 阵列展开", c_gds_aref),
    Check("data.kl_write", "data", "KLayout 写 → 本实现读", c_klayout_write_ours_read),
    Check("geom.bool", "geometry", "布尔运算面积（并/交/差/异或）", c_region_bool),
    Check("geom.sizing", "geometry", "sizing 外扩", c_region_sizing),
    Check("geom.width_space", "geometry", "width/space 检查计数", c_width_space),
    Check("verify.drc", "verification", "DRC 规则计数", c_drc_script),
    Check("verify.mos", "verification", "MOS 器件提取", c_mos_extract),
    Check("tech.layermap", "data", "layermap 层号映射", c_layer_map),
]


def run() -> dict:
    rows = []
    for c in CHECKS:
        try:
            ok, detail = c.fn()
        except Exception as exc:                       # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        rows.append({"id": c.id, "axis": c.axis, "desc": c.desc,
                     "ok": bool(ok), "detail": detail})
    axes: dict[str, dict] = {}
    for r in rows:
        a = axes.setdefault(r["axis"], {"pass": 0, "total": 0})
        a["total"] += 1
        a["pass"] += 1 if r["ok"] else 0
    for a in axes.values():
        a["score"] = round(a["pass"] / a["total"], 3)
    passed = sum(a["pass"] for a in axes.values())
    total = sum(a["total"] for a in axes.values())
    return {"checks": rows, "axes": axes,
            "overall": {"pass": passed, "total": total,
                        "score": round(passed / total, 3) if total else None},
            "klayout": {"binary": "0.29.8", "python": "0.30.5"}}


def to_markdown(rep: dict) -> str:
    lines = ["# 与 KLayout 的兼容性报告", "",
             f"- 总体：{rep['overall']['pass']}/{rep['overall']['total']} = "
             f"{rep['overall']['score']:.0%}", "",
             "| 轴 | 通过/总数 | 得分 |", "|---|---|---|"]
    for axis, a in rep["axes"].items():
        lines.append(f"| {axis} | {a['pass']}/{a['total']} | {a['score']:.0%} |")
    lines += ["", "| 检查 | 说明 | 结果 | 细节 |", "|---|---|---|---|"]
    for r in rep["checks"]:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"| {r['id']} | {r['desc']} | {icon} | {r['detail']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    rep = run()
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    md = to_markdown(rep)
    (OUT / "report.md").write_text(md)
    print(md)
    return 0 if rep["overall"]["score"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
