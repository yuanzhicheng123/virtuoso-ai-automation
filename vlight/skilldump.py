"""skilldump —— 把 headless SKILL 导出的设计文本读回 vdb。

导出格式由 `vlight.bridge.dump_script()` 生成，行协议：

    LIB  <lib> ncells=<n>
      CELL <cell> views=("schematic")
    SCH  <lib>/<cell> insts=.. nets=.. terms=.. shapes=..
      I  <name> <lib>/<cell>/<view> xy=(x y) orient="R0" n=1
        P  <key> <cdfType> = <skill literal>       ; CDF 有效值
        R  <key> = <skill literal>                 ; 用户改过的属性
      N  <name> sig="signal" termOrder=nil
        T  <inst>.<term>
      PIN <name> dir="input" net="rf_in"

这一层就是「复用 Virtuoso 自己的对象模型」的接口：不解析 OA 二进制，
用工具自己的 API 把语义取出来。
"""
from __future__ import annotations

import re
from pathlib import Path

from .vdb import (Cell, Design, Instance, Library, Net, Pin, Schematic, Shape,
                  TechFile, num, parse_skill)

_LIB = re.compile(r"^LIB\s+(\S+)\s+ncells=(\d+)")
_CELL = re.compile(r"^  CELL\s+(\S+)\s+views=(.+)$")
_SCH = re.compile(r"^SCH\s+(\S+)/(\S+)\s+insts=(\d+)\s+nets=(\d+)\s+terms=(\d+)\s+shapes=(\d+)")
_INST = re.compile(r'^  I\s+(\S+)\s+(\S+?)/(\S+?)/(\S+?)\s+xy=\((.*?)\)\s+orient="(\w+)"\s+n=(\d+)')
_PROP = re.compile(r"^    P\s+(\S+)\s+(\S+)\s+=\s*(.*)$")
_USERPROP = re.compile(r"^    R\s+(\S+)\s+=\s*(.*)$")
_NET = re.compile(r'^  N\s+(\S+)\s+sig="(\w+)"')
_TERM = re.compile(r"^    T\s+(\S+)\.(\S+)$")
_PIN = re.compile(r'^  PIN\s+(\S+)\s+dir="(\w+)"\s+net="([^"]*)"')
_TECH = re.compile(r"^TECH\s+layers=(\d+)\s+purposes=(\d+)")
_LAYER = re.compile(r"^  L\s+(\S+)\s+num=(\S+)\s+abbr=(\S+)")

MAX_VALUE = 512          # lxComponentType 这类几 KB 的字符串不进库


def _val(text: str):
    text = text.strip()
    if len(text) > MAX_VALUE:
        return "<truncated:%d bytes>" % len(text)
    return parse_skill(text)


def import_dump(text: str, lib_name: str | None = None) -> Design:
    """把 dump 文本解析成 Design。只取第一个 LIB 段（或指定 lib_name）。"""
    design = Design()
    lib: Library | None = None
    sch: Schematic | None = None
    cur_net: Net | None = None
    cell_views: dict[str, list[str]] = {}

    for raw in text.splitlines():
        if not raw.strip() or raw.startswith(("2026", "Loading", "COPYRIGHT", "#", "//")):
            continue
        m = _LIB.match(raw)
        if m:
            name = m.group(1)
            lib = Library(name, f"<dump>/{name}")
            if lib_name is None or name == lib_name:
                design.add(lib)
            sch = None
            continue
        m = _CELL.match(raw)
        if m and lib is not None:
            cell_views[m.group(1)] = re.findall(r'"([^"]+)"', m.group(2))
            continue
        m = _SCH.match(raw)
        if m:
            lname, cname = m.group(1), m.group(2)
            if lname not in design.libs:
                sch = None
                continue
            lib = design.libs[lname]
            cell = lib.cell(cname, create=True)
            sch = Schematic(cname, lname)
            cell.schematics["schematic"] = sch
            continue
        if sch is None:
            continue
        m = _INST.match(raw)
        if m:
            name, mlib, mcell, mview, xy, orient, n = m.groups()
            xs = xy.split()
            pos = (float(xs[0]), float(xs[1])) if len(xs) == 2 else (0.0, 0.0)
            sch.add_inst(Instance(name, mlib, mcell, mview, pos, orient, {}, int(n)))
            continue
        m = _PROP.match(raw)
        if m and sch.insts:
            key = m.group(1)
            if key == "lxComponentType":
                continue
            sch.insts[-1].params[key] = _val(m.group(3))
            continue
        m = _USERPROP.match(raw)
        if m and sch.insts:
            key = m.group(1)
            sch.insts[-1].params.setdefault(key, _val(m.group(2)))
            continue
        m = _NET.match(raw)
        if m:
            cur_net = sch.add_net(m.group(1), m.group(2) if m.group(2) != "nil" else "signal")
            continue
        m = _TERM.match(raw)
        if m and cur_net is not None:
            inst, term = m.groups()
            sch.connect(inst, term, cur_net.name)
            continue
        m = _PIN.match(raw)
        if m:
            name, direction, net = m.groups()
            p = sch.add_pin(name, direction)
            p.net = net
            continue

    return design


def read_dump(path: str | Path, lib_name: str | None = None) -> Design:
    return import_dump(Path(path).read_text(errors="replace"), lib_name)


def import_tech(path: str | Path, tech: TechFile | None = None) -> TechFile:
    """读 Virtuoso stream 层映射表（tsmcN65.layermap 同族格式）。

    列：LayerName  LayerPurpose  StreamLayer  StreamDatatype
    """
    tech = tech or TechFile()
    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        name, purpose, layer, dtype = parts[:4]
        try:
            ln, dt = int(layer), int(dtype)
        except ValueError:
            continue
        tech.add(name)
        tech.layers[name].purposes[purpose] = dt
        tech.gds_map[(name, purpose)] = (ln, dt)
    return tech


__all__ = ["import_dump", "read_dump", "import_tech"]
