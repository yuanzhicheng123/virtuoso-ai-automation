"""compat.klayout —— 调用本机 KLayout 做参照实现。

两种用法：
  * Python API（klayout.db）：读写 GDS、几何运算
  * 批处理脚本：`klayout -b -r x.rb` 跑 DRC / 层运算，stdout 回传结果
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def have_klayout() -> bool:
    try:
        import klayout.db  # noqa: F401
        return True
    except Exception:                       # noqa: BLE001
        return False


def run_ruby(script: str, workdir: Path, timeout: int = 120,
             ext: str = ".drc") -> str:
    """跑一段 KLayout 脚本（.drc 才会进 DRC 上下文，才有 source/input）。"""
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    p = wd / ("_kl" + ext)
    p.write_text(script)
    proc = subprocess.run(["klayout", "-b", "-r", str(p)],
                          capture_output=True, text=True, timeout=timeout)
    return proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")


def parse_kv(out: str) -> dict:
    """解析 `KL key value...` 行。"""
    data: dict[str, list] = {}
    for line in out.splitlines():
        if not line.startswith("KL "):
            continue
        parts = line[3:].split()
        if not parts:
            continue
        data.setdefault(parts[0], []).append(" ".join(parts[1:]))
    return data


def region_stats(gds: Path, layer: int, datatype: int = 0,
                 workdir: Path = Path("out/_kl")) -> dict:
    script = (
        'source("%s")\n' % Path(gds).resolve()
        + 'r = input(%d, %d)\n' % (layer, datatype)
        + 'puts "KL area #{r.area}"\n'
        + 'puts "KL count #{r.count}"\n'
        + 'puts "KL merged #{r.merged.count}"\n'
        + 'puts "KL bbox #{r.bbox.to_s}"\n'
    )
    out = run_ruby(script, workdir)
    kv = parse_kv(out)

    def get(k, d=None):
        return (kv.get(k) or [d])[0]

    return {"area": float(get("area", 0)), "count": int(float(get("count", 0))),
            "merged": int(float(get("merged", 0))), "bbox": get("bbox", ""),
            "raw": out}


def drc(gds: Path, rules: str, workdir: Path = Path("out/_kl_drc")) -> dict:
    """rules 是 Ruby 片段：对 `r` 做 width/space/enclosure 并 puts 计数。"""
    script = 'source("%s")\n' % Path(gds).resolve() + rules + '\n'
    out = run_ruby(script, workdir)
    kv = parse_kv(out)
    return {"raw": out,
            **{k: int(float(v[0])) for k, v in kv.items()
               if k in ("width", "space", "area", "enclosing", "overlap", "gates",
                        "sd", "shorts")}}


def info(path: Path) -> dict:
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(str(path))
    texts = 0
    for cell in ly.each_cell():
        for li in ly.layer_indexes():
            for sh in cell.shapes(li).each():
                if sh.is_text():
                    texts += 1
    return {"dbu": ly.dbu, "cells": len(list(ly.each_cell())),
            "top": [c.name for c in ly.top_cells()],
            "layers": sorted({(li.layer, li.datatype) for li in ly.layer_infos()}),
            "texts": texts}


def write_gds(path: Path, cells: dict) -> Path:
    """用 KLayout 生成 GDS：cells = {name: {"boxes": [(l,d,x0,y0,x1,y1)], "texts": [...], "refs": [...]}}"""
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.dbu = 0.001
    made = {}
    for name in cells:
        made[name] = ly.create_cell(name)
    for name, spec in cells.items():
        cell = made[name]
        for layer, dt, x0, y0, x1, y1 in spec.get("boxes", []):
            cell.shapes(ly.layer(layer, dt)).insert(
                kdb.Box(int(x0 * 1000), int(y0 * 1000), int(x1 * 1000), int(y1 * 1000)))
        for layer, dt, text, x, y in spec.get("texts", []):
            cell.shapes(ly.layer(layer, dt)).insert(
                kdb.Text(text, int(x * 1000), int(y * 1000)))
        for child, x, y, cols, rows, csp, rsp in spec.get("refs", []):
            if cols > 1 or rows > 1:
                cell.insert(kdb.CellInstArray(
                    made[child].cell_index(),
                    kdb.Trans(int(x * 1000), int(y * 1000)),
                    kdb.Vector(int(csp * 1000), 0), kdb.Vector(0, int(rsp * 1000)),
                    cols, rows))
            else:
                cell.insert(kdb.CellInstArray(made[child].cell_index(),
                                              kdb.Trans(int(x * 1000), int(y * 1000))))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ly.write(str(p))
    return p


def flatten_count(path: Path) -> int:
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(str(path))
    top = ly.top_cell()
    ly.flatten(top.cell_index(), -1, False)
    return sum(top.shapes(li).size() for li in ly.layer_indexes())
