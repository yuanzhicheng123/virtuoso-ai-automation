"""vdb —— Virtuoso 对象模型的轻量等价物（library / cell / view / inst / net / pin）。

对应关系（逆向自 IC23.1 的 SKILL 对象模型）::

    dd library        ->  Library
    dd cell           ->  Cell
    cellview          ->  CellView   (schematic / symbol / layout)
    inst (CDF 参数)    ->  Instance
    net + instTerm    ->  Net.terms
    terminal (pin)    ->  Pin
    techfile 层表      ->  TechFile

落盘布局刻意对齐 Virtuoso，方便和真实库对照::

    LIB/.oalib                      库标记
    LIB/CELL/schematic/sch.json     视图内容
    LIB/CELL/schematic/master.tag   主文件名（Virtuoso 同名文件）
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator

# --------------------------------------------------------------------------- #
# SKILL 字面量 <-> Python
# --------------------------------------------------------------------------- #

_NUM_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))([eE][+-]?\d+)?([TGMKkmunpfa])?$")
_SUFFIX = {
    "T": 1e12, "G": 1e9, "M": 1e6, "K": 1e3, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18,
}


def num(text: Any) -> float | None:
    """把 CDF 里的工程记数（"30u"/"2.4G"/"350.0n"）转成 float。

    SPICE 约定：M = mega，m = milli；`meg` 也认。
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if text is None:
        return None
    s = str(text).strip().strip('"')
    if not s or s in ("nil", "None"):
        return None
    if s.lower().startswith("meg"):
        try:
            return float(s[3:]) * 1e6
        except ValueError:
            return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    base = float(m.group(1) + (m.group(2) or ""))
    return base * _SUFFIX.get(m.group(3) or "", 1.0)


_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse_skill(text: str) -> Any:
    """读 SKILL 字面量：字符串 / nil / t / 数字 / (a b c)。"""
    tokens = _TOKEN.findall(text.strip())
    pos = 0

    def atom(tok: str) -> Any:
        if tok.startswith('"'):
            return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if tok == "nil":
            return None
        if tok == "t":
            return True
        v = num(tok)
        return v if v is not None and _NUM_RE.match(tok) else tok

    def parse() -> Any:
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            out = []
            while pos < len(tokens) and tokens[pos] != ")":
                out.append(parse())
            pos += 1
            return out
        return atom(tok)

    return parse() if tokens else None


def unparse(value: Any) -> str:
    """Python -> SKILL 字面量（写回 .il 时用）。"""
    if value is None:
        return "nil"
    if value is True:
        return "t"
    if isinstance(value, str):
        return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')
    if isinstance(value, (list, tuple)):
        return "(" + " ".join(unparse(v) for v in value) + ")"
    if isinstance(value, float):
        return repr(value)
    return str(value)


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

ORIENTS = ("R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90")


@dataclass
class Instance:
    name: str
    lib: str = ""
    cell: str = ""
    view: str = "symbol"
    xy: tuple[float, float] = (0.0, 0.0)
    orient: str = "R0"
    params: dict[str, Any] = field(default_factory=dict)
    num_inst: int = 1
    # AREF 阵列参数（GDS 数组引用）
    cols: int = 1
    rows: int = 1
    colspace: float = 0.0
    rowspace: float = 0.0
    # 端名 -> 网络名，由 Schematic 反填
    terms: dict[str, str] = field(default_factory=dict)

    @property
    def master(self) -> str:
        return f"{self.lib}/{self.cell}"

    def p(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def pf(self, key: str, default: float | None = None) -> float | None:
        v = num(self.params.get(key))
        return default if v is None else v


@dataclass
class Pin:
    name: str
    direction: str = "inputOutput"      # input / output / inputOutput
    net: str | None = None


@dataclass
class Net:
    name: str
    sig_type: str = "signal"
    terms: list[tuple[str, str]] = field(default_factory=list)   # (inst, term)
    term_order: list[str] | None = None

    def touch(self, inst: str, term: str) -> None:
        if (inst, term) not in self.terms:
            self.terms.append((inst, term))


@dataclass
class Shape:
    """原理图图形（wire/label 等），渲染用；不影响网表。"""
    kind: str                 # wire / line / label / rect / polygon / path / text
    points: list[tuple[float, float]] = field(default_factory=list)
    layer: str = ""
    purpose: str = "drawing"
    text: str = ""
    width: float = 0.0        # PATH 线宽


@dataclass
class Schematic:
    cell: str
    lib: str = ""
    insts: list[Instance] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    pins: list[Pin] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)

    # -- 查询 ------------------------------------------------------------- #
    def inst(self, name: str) -> Instance:
        for i in self.insts:
            if i.name == name:
                return i
        raise KeyError(f"no instance {name!r} in {self.lib}/{self.cell}")

    def net(self, name: str) -> Net:
        for n in self.nets:
            if n.name == name:
                return n
        raise KeyError(f"no net {name!r} in {self.lib}/{self.cell}")

    def net_of(self, inst: str, term: str) -> str | None:
        for n in self.nets:
            if (inst, term) in n.terms:
                return n.name
        return None

    def pin_names(self) -> list[str]:
        return [p.name for p in self.pins]

    def masters(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for i in self.insts:
            out.setdefault(i.master, []).append(i.name)
        return out

    # -- 编辑 ------------------------------------------------------------- #
    def add_inst(self, inst: Instance) -> Instance:
        if any(i.name == inst.name for i in self.insts):
            raise ValueError(f"duplicate instance {inst.name!r}")
        self.insts.append(inst)
        return inst

    def add_net(self, name: str, sig_type: str = "signal") -> Net:
        for n in self.nets:
            if n.name == name:
                return n
        n = Net(name, sig_type)
        self.nets.append(n)
        return n

    def connect(self, inst: str, term: str, net: str) -> None:
        """等价于 dbCreateConnByName：把 instTerm 连到 net（自动建 net）。"""
        if not any(i.name == inst for i in self.insts):
            raise KeyError(f"no instance {inst!r}")
        for n in self.nets:                       # 先断开旧连接
            if (inst, term) in n.terms:
                n.terms.remove((inst, term))
        self.add_net(net).touch(inst, term)
        self.inst(inst).terms[term] = net

    def add_pin(self, name: str, direction: str = "inputOutput") -> Pin:
        for p in self.pins:
            if p.name == name:
                p.direction = direction
                return p
        p = Pin(name, direction, name)
        self.pins.append(p)
        self.add_net(name)
        return p

    # -- 校验 ------------------------------------------------------------- #
    def check(self) -> list[str]:
        """等价于 design rule check 的电气部分：悬空网络/未连接端子/重名。"""
        issues: list[str] = []
        names = [i.name for i in self.insts]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            issues.append(f"重复实例名: {sorted(dup)}")
        netnames = [n.name for n in self.nets]
        if len(set(netnames)) != len(netnames):
            issues.append("重复网络名")
        connected = {(i, t) for n in self.nets for (i, t) in n.terms}
        for i in self.insts:
            for t in (i.terms or {}):
                if (i.name, t) not in connected:
                    issues.append(f"{i.name}.{t} 引用了不存在于任何 net 的连接")
        for n in self.nets:
            if len(n.terms) == 0:
                issues.append(f"net {n.name} 无任何端子")
            if len(n.terms) == 1 and n.name not in self.pin_names():
                issues.append(f"net {n.name} 只连了 1 个端子（悬空）")
        for p in self.pins:
            if not any(n.name == p.name and n.terms for n in self.nets):
                issues.append(f"pin {p.name} 没有连到任何端子")
        return issues

    def stats(self) -> dict[str, int]:
        return {
            "insts": len(self.insts),
            "nets": len(self.nets),
            "pins": len(self.pins),
            "devices": len({i.master for i in self.insts}),
        }


@dataclass
class Cell:
    name: str
    lib: str = ""
    schematics: dict[str, Schematic] = field(default_factory=dict)   # view -> sch
    layouts: dict[str, "Layout"] = field(default_factory=dict)

    def schematic(self, view: str = "schematic") -> Schematic:
        if view not in self.schematics:
            raise KeyError(f"{self.lib}/{self.name}/{view} 不存在")
        return self.schematics[view]


@dataclass
class Layout:
    """版图视图：层/目的 + 多边形 + 子单元摆放。等价 dbCreateRect/dbCreatePolygon。"""
    cell: str
    lib: str = ""
    shapes: list[Shape] = field(default_factory=list)
    instances: list["Instance"] = field(default_factory=list)

    def place(self, name: str, lib: str, cell: str,
              xy: tuple[float, float] = (0.0, 0.0), orient: str = "R0") -> Instance:
        inst = Instance(name, lib, cell, "layout", xy, orient)
        self.instances.append(inst)
        return inst

    def add(self, layer: str, points: Iterable, purpose: str = "drawing",
            kind: str = "polygon") -> Shape:
        s = Shape(kind=kind, layer=layer, purpose=purpose,
                  points=[(float(x), float(y)) for x, y in points])
        self.shapes.append(s)
        return s

    def bbox(self) -> tuple[float, float, float, float] | None:
        pts = [p for s in self.shapes for p in s.points]
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def layers(self) -> list[str]:
        return sorted({s.layer for s in self.shapes})


@dataclass
class LayerDef:
    name: str
    number: int | None = None
    abbreviation: str = ""
    purposes: dict[str, int] = field(default_factory=dict)


@dataclass
class TechFile:
    """techfile.tf 的轻量投影：层名、层号、purpose 号（GDS 映射）。"""
    layers: dict[str, LayerDef] = field(default_factory=dict)
    gds_map: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)

    def add(self, name: str, number: int | None = None, abbr: str = "") -> LayerDef:
        ld = self.layers.get(name) or LayerDef(name, number, abbr)
        if number is not None:
            ld.number = number
        if abbr:
            ld.abbreviation = abbr
        self.layers[name] = ld
        return ld

    def stream(self, layer: str, purpose: str = "drawing") -> tuple[int, int]:
        """层/目的 -> GDS (layer, datatype)。未显式映射时用层号/purpose 号。"""
        if (layer, purpose) in self.gds_map:
            return self.gds_map[(layer, purpose)]
        ld = self.layers.get(layer)
        if ld is None:
            raise KeyError(f"techfile 里没有层 {layer!r}")
        dt = ld.purposes.get(purpose)
        if dt is None:
            dt = 0 if purpose == "drawing" else abs(hash(purpose)) % 200
        return (ld.number if ld.number is not None else 0), dt


# --------------------------------------------------------------------------- #
# cds.lib / 库目录
# --------------------------------------------------------------------------- #

@dataclass
class LibPath:
    """cds.lib 的解析结果：DEFINE / INCLUDE / SOFTINCLUDE（含 $VAR 展开）。"""
    entries: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, path: str | Path, _seen: set[str] | None = None) -> "LibPath":
        seen = _seen if _seen is not None else set()
        p = Path(path).expanduser()
        out = cls()
        if str(p) in seen or not p.is_file():
            return out
        seen.add(str(p))
        for raw in p.read_text(errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            kw = parts[0].upper()
            if kw in ("DEFINE", "ASSIGN") and len(parts) >= 3:
                out.entries[parts[1]] = os.path.expandvars(parts[2])
            elif kw in ("INCLUDE", "SOFTINCLUDE") and len(parts) >= 2:
                sub = os.path.expandvars(parts[1])
                if not os.path.isabs(sub):
                    sub = str(p.parent / sub)
                out.entries.update(cls.parse(sub, seen).entries)
        return out

    def resolve(self, lib: str, base: str | Path | None = None) -> Path | None:
        if lib in self.entries:
            path = Path(self.entries[lib])
            if not path.is_absolute() and base is not None:
                path = Path(base) / path
            return path
        return None


# --------------------------------------------------------------------------- #
# 磁盘读写
# --------------------------------------------------------------------------- #

_MASTER_TAG = "sch.json"


def _shape_to_json(s: Shape) -> dict:
    d = {"kind": s.kind, "layer": s.layer, "purpose": s.purpose, "text": s.text,
         "width": s.width, "points": [list(p) for p in s.points]}
    return {k: v for k, v in d.items() if v not in ("", [], None)}


def _shape_from_json(d: dict) -> Shape:
    return Shape(kind=d.get("kind", "polygon"),
                 layer=d.get("layer", ""),
                 purpose=d.get("purpose", "drawing"),
                 text=d.get("text", ""),
                 width=float(d.get("width", 0.0)),
                 points=[(float(x), float(y)) for x, y in d.get("points", [])])


def _sch_to_json(s: Schematic) -> dict:
    return {
        "format": "vlight.schematic/1",
        "lib": s.lib, "cell": s.cell,
        "pins": [asdict(p) for p in s.pins],
        "instances": [
            {"name": i.name, "lib": i.lib, "cell": i.cell, "view": i.view,
             "xy": list(i.xy), "orient": i.orient, "n": i.num_inst,
             "params": {k: v for k, v in i.params.items()}}
            for i in s.insts],
        "nets": [{"name": n.name, "sigType": n.sig_type,
                  "terms": [list(t) for t in n.terms]} for n in s.nets],
        "shapes": [_shape_to_json(x) for x in s.shapes],
        "props": s.props,
    }


def _sch_from_json(d: dict) -> Schematic:
    s = Schematic(cell=d["cell"], lib=d.get("lib", ""), props=d.get("props", {}))
    s.pins = [Pin(**p) for p in d.get("pins", [])]
    for i in d.get("instances", []):
        s.insts.append(Instance(
            name=i["name"], lib=i.get("lib", ""), cell=i.get("cell", ""),
            view=i.get("view", "symbol"), xy=tuple(i.get("xy", (0, 0))),
            orient=i.get("orient", "R0"), params=dict(i.get("params", {})),
            num_inst=int(i.get("n", 1))))
    for n in d.get("nets", []):
        net = Net(n["name"], n.get("sigType", "signal"),
                  [tuple(t) for t in n.get("terms", [])])
        s.nets.append(net)
    s.shapes = [_shape_from_json(x) for x in d.get("shapes", [])]
    for i in s.insts:                              # 反填 inst.terms
        for n in s.nets:
            for (iname, term) in n.terms:
                if iname == i.name:
                    i.terms[term] = n.name
    return s


class Library:
    """磁盘上的一个库目录（Virtuoso 布局：LIB/CELL/VIEW/）。"""

    def __init__(self, name: str, path: str | Path):
        self.name = name
        self.path = Path(path)
        self.cells: dict[str, Cell] = {}
        self.readonly = False

    # -- 打开/保存 -------------------------------------------------------- #
    @classmethod
    def open(cls, name: str, path: str | Path, readonly: bool = False) -> "Library":
        lib = cls(name, path)
        lib.readonly = readonly
        if not lib.path.exists():
            return lib
        for cell_dir in sorted(p for p in lib.path.iterdir() if p.is_dir()):
            cell = Cell(cell_dir.name, name)
            for view_dir in sorted(p for p in cell_dir.iterdir() if p.is_dir()):
                tag = view_dir / "master.tag"
                names = ([tag.read_text().strip()] if tag.is_file()
                         else [_MASTER_TAG, "layout.json"])
                master = next((view_dir / n for n in names if (view_dir / n).is_file()), None)
                if master is not None:
                    data = json.loads(master.read_text())
                    if data.get("format", "").startswith("vlight.schematic"):
                        cell.schematics[view_dir.name] = _sch_from_json(data)
                    elif data.get("format", "").startswith("vlight.layout"):
                        lay = Layout(cell_dir.name, name)
                        lay.shapes = [_shape_from_json(x) for x in data.get("shapes", [])]
                        for i in data.get("instances", []):
                            lay.instances.append(Instance(
                                name=i["name"], lib=i.get("lib", ""), cell=i["cell"],
                                view=i.get("view", "layout"), xy=tuple(i.get("xy", (0, 0))),
                                orient=i.get("orient", "R0")))
                        cell.layouts[view_dir.name] = lay
            lib.cells[cell.name] = cell
        return lib

    def save(self) -> None:
        if self.readonly:
            raise PermissionError(f"库 {self.name} 是只读的")
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / ".oalib").write_text(
            json.dumps({"Library": {"DM": "vlightFileSys"}}, indent=2) + "\n")
        for cell in self.cells.values():
            cdir = self.path / cell.name
            (cdir / "schematic").mkdir(parents=True, exist_ok=True)
            (cdir / "schematic" / "master.tag").write_text(_MASTER_TAG + "\n")
            (cdir / "schematic" / _MASTER_TAG).write_text(
                json.dumps(_sch_to_json(cell.schematic()), indent=1) + "\n")
            for view, lay in cell.layouts.items():
                vdir = cdir / view
                vdir.mkdir(parents=True, exist_ok=True)
                (vdir / "master.tag").write_text("layout.json\n")
                (vdir / "layout.json").write_text(json.dumps(
                    {"format": "vlight.layout/1", "cell": cell.name, "lib": self.name,
                     "shapes": [_shape_to_json(s) for s in lay.shapes],
                     "instances": [{"name": i.name, "lib": i.lib, "cell": i.cell,
                                    "xy": list(i.xy), "orient": i.orient}
                                   for i in lay.instances]},
                    indent=1) + "\n")

    # -- 访问 ------------------------------------------------------------- #
    def cell(self, name: str, create: bool = False) -> Cell:
        if name not in self.cells:
            if not create:
                raise KeyError(f"库 {self.name} 里没有 cell {name!r}")
            self.cells[name] = Cell(name, self.name,
                                    {"schematic": Schematic(name, self.name)})
        return self.cells[name]

    def schematic(self, cell: str, view: str = "schematic") -> Schematic:
        return self.cell(cell).schematic(view)

    def new_cell(self, cell: str, pins: Iterable[tuple[str, str]] = ()) -> Schematic:
        s = Schematic(cell, self.name)
        for name, direction in pins:
            s.add_pin(name, direction)
        self.cells[cell] = Cell(cell, self.name, {"schematic": s})
        return s

    def __iter__(self) -> Iterator[Cell]:
        return iter(self.cells.values())


class Design:
    """多个库的集合（cds.lib 的等价物），网表/仿真都从这里取 master。"""

    def __init__(self, libs: dict[str, Library] | None = None):
        self.libs: dict[str, Library] = libs or {}

    def add(self, lib: Library) -> Library:
        self.libs[lib.name] = lib
        return lib

    def open_cds_lib(self, path: str | Path, readonly: bool = True) -> "Design":
        lp = LibPath.parse(path)
        for name, p in lp.entries.items():
            self.add(Library.open(name, p, readonly=readonly))
        return self

    def lib(self, name: str) -> Library:
        if name not in self.libs:
            raise KeyError(f"未加载库 {name!r}（cds.lib 里没有或没 open）")
        return self.libs[name]

    def schematic(self, master: str, view: str = "schematic") -> Schematic:
        lib, _, cell = master.partition("/")
        return self.lib(lib).schematic(cell, view)

    def find(self, lib: str, cell: str, view: str = "schematic") -> Schematic | None:
        try:
            return self.lib(lib).schematic(cell, view)
        except (KeyError, FileNotFoundError):
            return None

    def topo(self, master: str, seen: set[str] | None = None) -> list[str]:
        """自底向上的层次序（叶子在前），用于 subckt 定义顺序。"""
        seen = seen if seen is not None else set()
        out: list[str] = []
        if master in seen:
            return out
        seen.add(master)
        sch = self.schematic(master)
        for i in sch.insts:
            sub = f"{i.lib}/{i.cell}"
            if self.find(i.lib, i.cell, "schematic") is not None:
                for m in self.topo(sub, seen):
                    if m not in out:
                        out.append(m)
        out.append(master)
        return out


# --------------------------------------------------------------------------- #
# 变换（实例放置，版图/渲染共用）
# --------------------------------------------------------------------------- #

def orient_matrix(orient: str) -> tuple[int, int, int, int]:
    return {
        "R0": (1, 0, 0, 1), "R90": (0, -1, 1, 0),
        "R180": (-1, 0, 0, -1), "R270": (0, 1, -1, 0),
        "MX": (1, 0, 0, -1), "MY": (-1, 0, 0, 1),
        "MXR90": (0, 1, 1, 0), "MYR90": (0, -1, -1, 0),
    }.get(orient.upper(), (1, 0, 0, 1))


def transform(points: Iterable[tuple[float, float]],
              xy: tuple[float, float] = (0.0, 0.0),
              orient: str = "R0") -> list[tuple[float, float]]:
    a, b, c, d = orient_matrix(orient)
    ox, oy = xy
    return [(a * x + b * y + ox, c * x + d * y + oy) for x, y in points]


def snap(v: float, grid: float = 0.001) -> float:
    return round(v / grid) * grid


__all__ = [
    "Instance", "Pin", "Net", "Shape", "Schematic", "Cell", "Layout", "LayerDef",
    "TechFile", "LibPath", "Library", "Design", "num", "parse_skill", "unparse",
    "transform", "orient_matrix", "ORIENTS", "snap",
]
