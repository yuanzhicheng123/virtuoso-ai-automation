"""netlist —— 原理图 -> SPICE/Spectre 网表。

器件映射表是「逆向出来的核心知识」：Virtuoso 的 CDF 参数名 -> PDK 网表参数名。
证据：tmscN65 `models/spectre/crn65gplus_2d5_lk_v1d0.scs` 里的 subckt 签名

    inline subckt nmos_rf_18 ( d g s b )
      parameters lr=0.28e-6 wr=1.0e-6 nr=4 mismatchflag=0 sigma=1 ...
    subckt rppolywo ( n1 n2 )     parameters l=1u w=1u factor=1 ... mf=1 ...
    subckt mimcap_sin ( top bottom )  parameters lt=10u wt=10u mf=1 ...
    subckt spiral_std_mu_z (top bottom gnode) parameters rad=.. w=.. nr=.. lay=.. gdis=.. spacing=..

这些签名和 headless SKILL 导出的 CDF 参数名一一对得上，所以可以机械翻译。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .vdb import Instance, Schematic, num

# --------------------------------------------------------------------------- #
# 参数格式化
# --------------------------------------------------------------------------- #

_SUFFIXES = [(1e9, "G"), (1e6, "M"), (1e3, "K"), (1e-3, "m"),
             (1e-6, "u"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]


def fmt_value(v: Any) -> str:
    """python 值 -> spectre 认的字面量（"10u"/"350n"/"1.5"/"1e-3"）。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"[+-]?[\d.]+[eE][+-]?\d+", s) or num(s) is None:
            return s
        return _eng(num(s), s)
    if isinstance(v, (int, float)):
        return _eng(float(v), None)
    if v is None:
        return "0"
    return str(v)


def _eng(x: float, orig: str | None) -> str:
    if orig is not None and re.fullmatch(r"[+-]?[\d.]+[TGMKkmunpfa]", orig.strip()):
        return orig.strip()                      # 保留用户写的工程记数
    if orig is not None and re.fullmatch(r"[+-]?\d+(\.\d+)?([eE][+-]?\d+)?", orig.strip()):
        return orig.strip()                      # 纯数字原样吐回
    if x == 0 or not math.isfinite(x):
        return repr(x)
    ax = abs(x)
    if ax >= 1e3:                                  # G / M / K
        for scale, suf in ((1e9, "G"), (1e6, "M"), (1e3, "K")):
            if ax >= scale:
                return f"{x / scale:.6g}{suf}"
    if ax >= 1:                                    # 1e0 ~ 1e3 直接写
        return f"{x:.6g}"
    for scale, suf in ((1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
                       (1e-12, "p"), (1e-15, "f"), (1e-18, "a")):
        if ax >= scale * 0.9999995:
            return f"{x / scale:.6g}{suf}"
    return f"{x:.6g}"


# --------------------------------------------------------------------------- #
# 器件映射
# --------------------------------------------------------------------------- #

@dataclass
class DeviceSpec:
    """一个 master 的网表形态。

    nodes: 网表里的节点顺序 = master 的端子名顺序（对应 subckt 的位置参数）
    model: 网表里用的模型名；"{model}" 表示取实例的 model 参数
    params: spectre 参数名 -> 取值模板；"{}" 里的名字取实例 CDF 参数
    """
    nodes: list[str]
    model: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    kind: str = "subckt"          # mos / res / cap / ind / source / port / subckt
    always: dict[str, str] = field(default_factory=dict)
    drop_zero: bool = True

    def resolve(self, inst: Instance, params: dict | None = None
                ) -> tuple[str | None, dict[str, str]]:
        src = params if params is not None else inst.params
        out: dict[str, str] = {}
        for k, tmpl in self.params.items():
            val = _expand(tmpl, src)
            if val is None:
                continue
            if self.drop_zero and val in ("0", "nil"):
                continue
            out[k] = val
        out.update({k: _expand(v, src) or v for k, v in self.always.items()})
        model = None
        if self.model:
            model = _expand(self.model, src) or self.model
        return model, out


def _expand(tmpl: str, inst) -> str | None:
    """模板展开："{wr}"、"iPar(...)" 这类取值。"""
    if "{" not in tmpl:
        return tmpl
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        key = m.group(1)
        v = inst.get(key) if isinstance(inst, dict) else inst.params.get(key)
        if v is None or v == "" or (isinstance(v, str) and v.startswith("iPar(")):
            missing = True
            return ""
        return fmt_value(v)

    out = re.sub(r"\{(\w+)\}", sub, tmpl)
    return None if missing else out


# tsmcN65 主器件（逆向自 PDK subckt 签名 + 工程里实际用到的 master）
TSMC = {
    "nmos_rf_25ud18": DeviceSpec(["D", "G", "S", "B"], "{model}",
                                 {"lr": "{lr}", "wr": "{wr}", "nr": "{nr}"}, "mos"),
    "pmos_rf_25ud18": DeviceSpec(["D", "G", "S", "B"], "{model}",
                                 {"lr": "{lr}", "wr": "{wr}", "nr": "{nr}"}, "mos"),
    "nch_25": DeviceSpec(["D", "G", "S", "B"], "{model}",
                         {"l": "{l}", "w": "{w}", "nf": "{nf}"}, "mos"),
    "pch_25": DeviceSpec(["D", "G", "S", "B"], "{model}",
                         {"l": "{l}", "w": "{w}", "nf": "{nf}"}, "mos"),
    "rppolywo": DeviceSpec(["PLUS", "MINUS"], "rppolywo",
                           {"l": "{l}", "w": "{w}", "mf": "{m}"}, "res"),
    "rnpo": DeviceSpec(["PLUS", "MINUS"], "rnpo",
                       {"l": "{l}", "w": "{w}", "mf": "{m}"}, "res"),
    "mimcap": DeviceSpec(["PLUS", "MINUS"], "mimcap_sin",
                         {"lt": "{lt}", "wt": "{wt}", "mf": "{m}", "mimflag": "{mimflag}"}, "cap"),
    "mimcap_1p5": DeviceSpec(["PLUS", "MINUS"], "mimcap_sin",
                             {"lt": "{lt}", "wt": "{wt}", "mf": "{m}"}, "cap"),
    "spiral_std_mu_z": DeviceSpec(["PLUS", "MINUS", "BULK"], "spiral_std_mu_z",
                                  {"rad": "{rad}", "w": "{w}", "nr": "{nr}",
                                   "spacing": "{spacing}", "lay": "{lay}",
                                   "gdis": "{gdis}"}, "ind"),
    "spiral_sym_mu_z": DeviceSpec(["PLUS", "MINUS", "BULK"], "spiral_sym_mu_z",
                                  {"rad": "{rad}", "w": "{w}", "nr": "{nr}",
                                   "spacing": "{spacing}", "lay": "{lay}"}, "ind"),
}

# 自带的最小器件库（不依赖 PDK，跑单元测试/教学用）
VLIGHTLIB = {
    "res": DeviceSpec(["PLUS", "MINUS"], "resistor", {"r": "{r}"}, "res"),
    "cap": DeviceSpec(["PLUS", "MINUS"], "capacitor", {"c": "{c}"}, "cap"),
    "ind": DeviceSpec(["PLUS", "MINUS"], "inductor", {"l": "{l}"}, "ind"),
    "vsource": DeviceSpec(["PLUS", "MINUS"], "vsource",
                          {"dc": "{dc}", "mag": "{mag}", "type": "{type}"}, "source"),
    "isource": DeviceSpec(["PLUS", "MINUS"], "isource",
                          {"dc": "{dc}", "mag": "{mag}"}, "source"),
    "port": DeviceSpec(["PLUS", "MINUS"], "port",
                       {"r": "{r}", "num": "{num}"}, "port"),
    "vcvs": DeviceSpec(["PLUS", "MINUS", "CTRLP", "CTRLN"], "vcvs", {"gain": "{gain}"}, "subckt"),
    "nmos4": DeviceSpec(["D", "G", "S", "B"], "nmos4",
                        {"w": "{w}", "l": "{l}", "nf": "{nf}"}, "mos"),
    "pmos4": DeviceSpec(["D", "G", "S", "B"], "pmos4",
                        {"w": "{w}", "l": "{l}", "nf": "{nf}"}, "mos"),
}


class DeviceMap:
    """master 名 -> DeviceSpec。没登记的 master 按层次化 subckt 处理。"""

    def __init__(self, table: dict[str, DeviceSpec] | None = None):
        self.table: dict[str, DeviceSpec] = dict(table or {})
        self.table.update(TSMC)
        self.table.update(VLIGHTLIB)

    def add(self, master: str, spec: DeviceSpec) -> None:
        self.table[master] = spec

    def get(self, lib: str, cell: str) -> DeviceSpec | None:
        for key in (f"{lib}/{cell}", cell):
            if key in self.table:
                return self.table[key]
        return None

    @classmethod
    def from_json(cls, path) -> "DeviceMap":
        import json
        from pathlib import Path
        raw = json.loads(Path(path).read_text())
        dm = cls()
        for master, d in raw.items():
            dm.add(master, DeviceSpec(nodes=d["nodes"], model=d.get("model"),
                                      params=d.get("params", {}),
                                      kind=d.get("kind", "subckt"),
                                      always=d.get("always", {})))
        return dm


# --------------------------------------------------------------------------- #
# 网表生成
# --------------------------------------------------------------------------- #

_GLOBALS = {"0", "gnd!", "vss", "vdd"}          # 不作为 subckt 端口

# 纯符号实例（引脚/地/未连接标记），netlist 时跳过（Virtuoso 自己也是这么做的）
IGNORE_MASTERS = {
    "basic/ipin", "basic/opin", "basic/iopin", "basic/pin",
    "basic/noConn", "basic/gnd", "basic/gnd!", "basic/vdd",
    "analogLib/gnd", "analogLib/vdd", "analogLib/ipin", "analogLib/opin",
}


def is_graphic(master: str) -> bool:
    return master in IGNORE_MASTERS or master.split("/")[-1] in ("ipin", "opin", "iopin", "noConn")


class NetlistError(RuntimeError):
    pass


@dataclass
class Netlister:
    design: Any                                   # vdb.Design
    devmap: DeviceMap = field(default_factory=DeviceMap)
    globals: tuple[str, ...] = ("0",)
    lib_prefix: bool = False
    strict: bool = True
    allow_unknown: bool = False      # 未知 master 原样透传（导入网表时用）
    cdf: Any = None                  # cdf.CDFdb：有它就用 CDF 有效值填默认参数

    # -- 单个 cell 的 subckt ------------------------------------------------- #
    def subckt(self, master: str, view: str = "schematic") -> str:
        sch = self.design.schematic(master, view)
        name = self._name(master)
        pins = [p.name for p in sch.pins]
        lines = [f"subckt {name} {' '.join(pins)}".rstrip()]
        for inst in sch.insts:
            if is_graphic(inst.master):
                continue
            lines.append("  " + self.instance_line(inst, sch))
        lines.append(f"ends {name}")
        return "\n".join(lines)

    def instance_line(self, inst: Instance, parent: Schematic) -> str:
        spec = self.devmap.get(inst.lib, inst.cell)
        subckt = self.design.find(inst.lib, inst.cell, "schematic")
        if spec is None:
            if subckt is None:
                if self.allow_unknown:
                    nodes_u = list(inst.terms.values())
                    kv = " ".join(f"{k}={fmt_value(v)}" for k, v in inst.params.items())
                    return f"{inst.name} ({' '.join(nodes_u)}) {inst.cell}" + \
                        (f" {kv}" if kv else "")
                if self.strict:
                    raise NetlistError(
                        f"{parent.cell}: 实例 {inst.name} 的 master "
                        f"{inst.master} 既没有映射表也没有原理图")
                return f"* UNMAPPED {inst.name} {inst.master}"
            spec = DeviceSpec(nodes=[p.name for p in subckt.pins], model=self._name(inst.master),
                              kind="subckt")
        nodes = []
        for t in spec.nodes:
            n = inst.terms.get(t)
            nodes.append(n if n is not None else self._missing(inst, t, parent))
        eff = self.cdf.effective(inst) if self.cdf is not None else None
        model, params = spec.resolve(inst, eff)
        if subckt is not None and spec.kind == "subckt" and spec.model is None:
            model = self._name(inst.master)
        pfx = {"mos": "M", "res": "R", "cap": "C", "subckt": "X"}.get(spec.kind, "X")
        if spec.kind == "ind":
            pfx = "L"
        if self.cdf is not None:                      # CDF 声明了 netlistProcedure 就交给它
            cdf = self.cdf.get(inst.lib, inst.cell)
            if cdf is not None and cdf.netlist_procedure:
                from .cdf import run_procedure
                line = run_procedure(cdf.netlist_procedure,
                                     {"name": inst.name, "nodes": nodes, "master": inst.master,
                                      "params": eff or {}, "cdf": cdf, "inst": inst})
                if line:
                    return line
        if "m" not in params and "mf" not in params:
            mult = num(inst.params.get("m", inst.num_inst))
            if mult and mult > 1:
                params["m"] = fmt_value(inst.params.get("m", inst.num_inst))
        parts = [inst.name, "(" + " ".join(nodes) + ")", str(model)]
        parts += [f"{k}={v}" for k, v in params.items()]
        return " ".join(parts)

    def _missing(self, inst: Instance, term: str, parent: Schematic) -> str:
        if self.strict:
            raise NetlistError(f"{parent.cell}: {inst.name}.{term} 没有连接")
        return f"nc_{inst.name}_{term}"

    def _name(self, master: str) -> str:
        if self.lib_prefix:
            return master.replace("/", "_")
        return master.split("/")[-1]

    # -- 整个层次 ----------------------------------------------------------- #
    def subckts(self, top: str) -> str:
        out = []
        for master in self.design.topo(top):
            out.append(self.subckt(master))
        return "\n\n".join(out)

    # -- CDL（LVS 交接） ---------------------------------------------------- #
    def cdl(self, top: str) -> str:
        sch = self.design.schematic(top)
        body = [f".SUBCKT {top.split('/')[-1]} {' '.join(p.name for p in sch.pins)}"]
        for inst in sch.insts:
            spec = self.devmap.get(inst.lib, inst.cell)
            if spec is None:
                continue
            nodes = [inst.terms.get(t, "0") for t in spec.nodes]
            model, params = spec.resolve(inst)
            parts = [inst.name] + nodes + [str(model or inst.cell)]
            parts += [f"{k}={v}" for k, v in params.items()]
            body.append(" ".join(parts))
        body.append(".ENDS")
        return "\n".join(body)
