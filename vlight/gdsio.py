"""gdsio —— GDSII 读写（对齐 KLayout 的语义）。

支持的元素（读写双向）：BOUNDARY / PATH / TEXT / SREF / AREF。
写出的库能被 KLayout 原样读回（层号、图形数、面积、文字、线宽、阵列一致）；
KLayout 写出的 GDS 也能读回来——`compat/` 里有逐项对照。
"""
from __future__ import annotations

import struct
from pathlib import Path

from .vdb import Instance, Layout, Library, Shape, TechFile

HEADER, BGNLIB, LIBNAME, UNITS, ENDLIB = 0x00, 0x01, 0x02, 0x03, 0x04
BGNSTR, STRNAME, ENDSTR = 0x05, 0x06, 0x07
BOUNDARY, PATH, SREF, AREF, TEXT = 0x08, 0x09, 0x0A, 0x0B, 0x0C
LAYER, DATATYPE, WIDTH, XY, ENDEL = 0x0D, 0x0E, 0x0F, 0x10, 0x11
SNAME, COLROW, TEXTTYPE, PRESENTATION, STRING = 0x12, 0x13, 0x16, 0x17, 0x19
STRANS, PATHTYPE = 0x1A, 0x21

_NODATA, _BITARRAY, _INT2, _INT4, _REAL8, _ASCII = 0, 1, 2, 3, 5, 6


def _rec(rt: int, dt: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HBB", 4 + len(payload), rt, dt) + payload


def _i2(v):
    return struct.pack(">h", int(v))


def _i4(v):
    return struct.pack(">i", int(v))


def _r8(v: float) -> bytes:
    if v == 0:
        return b"\x00" * 8
    sign = 0x80 if v < 0 else 0x00
    v = abs(float(v))
    exp = 0
    while v >= 1.0:
        v /= 16.0
        exp += 1
    while v < 1.0 / 16.0:
        v *= 16.0
        exp -= 1
    mant = int(round(v * (1 << 56)))
    if mant >= (1 << 56):
        mant >>= 4
        exp += 1
    return bytes([sign | ((exp + 64) & 0x7F)]) + mant.to_bytes(7, "big")


def _r8_read(b: bytes) -> float:
    if not b or b == b"\x00" * 8:
        return 0.0
    sign = -1.0 if b[0] & 0x80 else 1.0
    exp = (b[0] & 0x7F) - 64
    mant = int.from_bytes(b[1:8], "big") / float(1 << 56)
    return sign * mant * (16.0 ** exp)


def _ascii(s) -> bytes:
    b = str(s).encode()
    return b + (b"\x00" if len(b) % 2 else b"")


def _ascii_read(b: bytes) -> str:
    return b.rstrip(b"\x00").decode(errors="replace")


def _xy(pts) -> bytes:
    return b"".join(_i4(int(round(x))) + _i4(int(round(y))) for x, y in pts)


def _stamp() -> bytes:
    return b"".join(_i2(v) for v in (2026, 1, 1, 0, 0, 0, 2026, 1, 1, 0, 0, 0))


def _layer_of(tech, layer, purpose, auto):
    try:
        return tech.stream(layer, purpose)
    except KeyError:
        if layer not in auto:
            auto[layer] = len(auto) + 1
        return auto[layer], 0


def _hierarchy(lib, top, view):
    order, seen = [], set()

    def walk(cell):
        if cell in seen or cell not in lib.cells:
            return
        seen.add(cell)
        lay = lib.cell(cell).layouts.get(view)
        if lay is not None:
            for inst in lay.instances:
                walk(inst.cell)
        order.append(cell)

    walk(top)
    return order


def write_gds(path, lib: Library, top: str, tech: TechFile,
              precision: float = 1e-9, view: str = "layout",
              arrays: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scale = 1.0 / precision
    auto = {}
    out = bytearray()
    out += _rec(HEADER, _INT2, _i2(600))
    out += _rec(BGNLIB, _INT2, _stamp())
    out += _rec(LIBNAME, _ASCII, _ascii(lib.name or "vlight"))
    out += _rec(UNITS, _REAL8, _r8(precision / 1e-6) + _r8(precision))
    top_cell = top.split("/")[-1]
    for name in _hierarchy(lib, top_cell, view):
        lay = lib.cell(name).layouts.get(view) or Layout(name, lib.name)
        out += _rec(BGNSTR, _INT2, _stamp())
        out += _rec(STRNAME, _ASCII, _ascii(name))
        for sh in lay.shapes:
            layer, dtype = _layer_of(tech, sh.layer, sh.purpose, auto)
            if sh.kind in ("text", "label") and sh.text:
                out += _rec(TEXT, _NODATA)
                out += _rec(LAYER, _INT2, _i2(layer))
                out += _rec(TEXTTYPE, _INT2, _i2(dtype))
                out += _rec(PRESENTATION, _BITARRAY, struct.pack(">H", 0x0005))
                out += _rec(XY, _INT4, _xy([(sh.points[0][0] * scale,
                                             sh.points[0][1] * scale)]))
                out += _rec(STRING, _ASCII, _ascii(sh.text))
                out += _rec(ENDEL, _NODATA)
                continue
            if sh.kind == "path" and len(sh.points) >= 2:
                out += _rec(PATH, _NODATA)
                out += _rec(LAYER, _INT2, _i2(layer))
                out += _rec(DATATYPE, _INT2, _i2(dtype))
                out += _rec(PATHTYPE, _INT2, _i2(0))
                if sh.width:
                    out += _rec(WIDTH, _INT4, _i4(int(round(sh.width * scale))))
                out += _rec(XY, _INT4, _xy([(x * scale, y * scale)
                                            for x, y in sh.points]))
                out += _rec(ENDEL, _NODATA)
                continue
            if len(sh.points) < 3:
                continue
            out += _rec(BOUNDARY, _NODATA)
            out += _rec(LAYER, _INT2, _i2(layer))
            out += _rec(DATATYPE, _INT2, _i2(dtype))
            pts = [(x * scale, y * scale) for x, y in sh.points]
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            out += _rec(XY, _INT4, _xy(pts))
            out += _rec(ENDEL, _NODATA)
        for inst in lay.instances:
            flag = 0x8000 if inst.orient.upper().startswith("M") else 0
            if arrays and (inst.cols > 1 or inst.rows > 1):
                out += _rec(AREF, _NODATA)
                out += _rec(SNAME, _ASCII, _ascii(inst.cell))
                out += _rec(COLROW, _INT2, _i2(inst.cols) + _i2(inst.rows))
                if flag:
                    out += _rec(STRANS, _BITARRAY, struct.pack(">H", flag))
                out += _rec(XY, _INT4, _xy([
                    (inst.xy[0] * scale, inst.xy[1] * scale),
                    ((inst.xy[0] + inst.cols * inst.colspace) * scale,
                     inst.xy[1] * scale),
                    (inst.xy[0] * scale,
                     (inst.xy[1] + inst.rows * inst.rowspace) * scale)]))
            else:
                out += _rec(SREF, _NODATA)
                out += _rec(SNAME, _ASCII, _ascii(inst.cell))
                if flag:
                    out += _rec(STRANS, _BITARRAY, struct.pack(">H", flag))
                out += _rec(XY, _INT4, _xy([(inst.xy[0] * scale,
                                             inst.xy[1] * scale)]))
            out += _rec(ENDEL, _NODATA)
        out += _rec(ENDSTR, _NODATA)
    out += _rec(ENDLIB, _NODATA)
    path.write_bytes(bytes(out))
    return path


def read_gds(path) -> dict:
    data = Path(path).read_bytes()
    pos, cells, cur, shape, inst = 0, {}, None, None, None
    units, libname = 1e-9, ""
    while pos + 4 <= len(data):
        length, rt, dt = struct.unpack(">HBB", data[pos:pos + 4])
        payload = data[pos + 4:pos + length]
        pos += length
        if rt == LIBNAME:
            libname = _ascii_read(payload)
        elif rt == UNITS:
            units = _r8_read(payload[8:16]) or 1e-9
        elif rt == STRNAME:
            cur = {"shapes": [], "instances": []}
            cells[_ascii_read(payload)] = cur
        elif rt == BOUNDARY:
            shape = {"kind": "polygon", "layer": 0, "datatype": 0, "points": []}
        elif rt == PATH:
            shape = {"kind": "path", "layer": 0, "datatype": 0, "points": [],
                     "width": 0.0}
        elif rt == TEXT:
            shape = {"kind": "text", "layer": 0, "datatype": 0, "points": [],
                     "text": ""}
        elif rt in (SREF, AREF):
            inst = {"cell": "", "xy": [], "orient": "R0", "cols": 1, "rows": 1,
                    "colspace": 0.0, "rowspace": 0.0}
        elif rt == LAYER and shape is not None:
            shape["layer"] = struct.unpack(">h", payload[:2])[0]
        elif rt in (DATATYPE, TEXTTYPE) and shape is not None:
            shape["datatype"] = struct.unpack(">h", payload[:2])[0]
        elif rt == WIDTH and shape is not None:
            shape["width"] = struct.unpack(">i", payload[:4])[0] * units
        elif rt == STRING and shape is not None:
            shape["text"] = _ascii_read(payload)
        elif rt == SNAME and inst is not None:
            inst["cell"] = _ascii_read(payload)
        elif rt == COLROW and inst is not None:
            inst["cols"], inst["rows"] = struct.unpack(">hh", payload[:4])
        elif rt == STRANS and inst is not None:
            if struct.unpack(">H", payload[:2])[0] & 0x8000:
                inst["orient"] = "MX"
        elif rt == XY:
            ints = struct.unpack(">%di" % (len(payload) // 4), payload)
            pts = [(ints[i] * units, ints[i + 1] * units)
                   for i in range(0, len(ints), 2)]
            if shape is not None:
                shape["points"] = pts
            elif inst is not None:
                inst["xy"] = pts
        elif rt == ENDEL:
            if shape is not None and cur is not None:
                cur["shapes"].append(shape)
            elif inst is not None and cur is not None:
                p = inst["xy"]
                if len(p) >= 3:
                    inst["colspace"] = (p[1][0] - p[0][0]) / max(inst["cols"], 1)
                    inst["rowspace"] = (p[2][1] - p[0][1]) / max(inst["rows"], 1)
                inst["xy"] = p[:1] or [(0.0, 0.0)]
                cur["instances"].append(inst)
            shape, inst = None, None
    return {"lib": libname, "units": units, "cells": cells}


def load_gds_into(lib: Library, path, view: str = "layout", tech=None) -> dict:
    info = read_gds(path)
    rev = {}
    if tech is not None:
        for (name, _purpose), (num, dt) in tech.gds_map.items():
            rev[(num, dt)] = name
    for cell_name, cell in info["cells"].items():
        lay = Layout(cell_name, lib.name)
        for sh in cell["shapes"]:
            name = rev.get((sh["layer"], sh["datatype"]), f"L{sh['layer']}")
            if sh["kind"] == "text":
                lay.shapes.append(Shape(kind="text", layer=name, text=sh["text"],
                                        points=sh["points"]))
            elif sh["kind"] == "path":
                lay.shapes.append(Shape(kind="path", layer=name, width=sh["width"],
                                        points=sh["points"]))
            else:
                lay.shapes.append(Shape(kind="polygon", layer=name,
                                        points=sh["points"]))
        for k, inst in enumerate(cell["instances"]):
            lay.instances.append(Instance(
                name=f"I{k}", lib="", cell=inst["cell"], view=view,
                xy=inst["xy"][0], orient=inst["orient"], cols=inst["cols"],
                rows=inst["rows"], colspace=inst["colspace"],
                rowspace=inst["rowspace"]))
        lib.cell(cell_name, create=True).layouts[view] = lay
    return {"lib": info["lib"], "units": info["units"],
            "cells": {k: {"shapes": len(v["shapes"]),
                          "instances": len(v["instances"])}
                      for k, v in info["cells"].items()}}


def verify(path, tech=None) -> dict:
    cells = read_gds(path)
    info = {
        "file": str(path),
        "bytes": Path(path).stat().st_size,
        "cells": {k: len(v["shapes"]) for k, v in cells["cells"].items()},
        "instances": {k: len(v["instances"]) for k, v in cells["cells"].items()},
        "layers": sorted({(s["layer"], s["datatype"])
                          for v in cells["cells"].values() for s in v["shapes"]}),
    }
    try:
        import klayout.db as kdb
        ly = kdb.Layout()
        ly.read(str(path))
        n = sum(cell.shapes(li).size() for cell in ly.each_cell()
                for li in ly.layer_indexes())
        info["klayout"] = {
            "dbu": ly.dbu,
            "cells": len(list(ly.each_cell())),
            "top": [c.name for c in ly.top_cells()],
            "shapes": n,
            "layers": sorted({(li.layer, li.datatype) for li in ly.layer_infos()}),
        }
    except Exception as exc:
        info["klayout"] = f"unavailable: {type(exc).__name__}: {exc}"
    return info


# --------------------------------------------------------------------------- #
# OASIS（通过 KLayout 桥接；原生编解码未实现，见 docs/KLAYOUT_GAP.md）
# --------------------------------------------------------------------------- #

def write_oasis(path, lib: Library, top: str, tech: TechFile,
                precision: float = 1e-9, view: str = "layout") -> Path:
    """写 OASIS：先出 GDS，再让 KLayout 转。没有 KLayout 就报错。"""
    import klayout.db as kdb
    path = Path(path)
    tmp = path.with_suffix(".tmp.gds")
    write_gds(tmp, lib, top, tech, precision=precision, view=view)
    ly = kdb.Layout()
    ly.read(str(tmp))
    path.parent.mkdir(parents=True, exist_ok=True)
    ly.write(str(path))
    tmp.unlink(missing_ok=True)
    return path


def read_oasis(path, lib: Library, view: str = "layout", tech=None) -> dict:
    """读 OASIS：同样借 KLayout 转成内存里的 GDS 再进 vdb。"""
    import klayout.db as kdb
    tmp = Path(str(path) + ".tmp.gds")
    ly = kdb.Layout()
    ly.read(str(path))
    ly.write(str(tmp))
    try:
        return load_gds_into(lib, tmp, view=view, tech=tech)
    finally:
        tmp.unlink(missing_ok=True)


__all__ = ["write_gds", "read_gds", "load_gds_into", "verify",
           "write_oasis", "read_oasis"]
