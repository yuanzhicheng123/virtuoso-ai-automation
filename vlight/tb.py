"""tb —— 测试台与仿真分析（ADE 的 netlist 视图那一层）。

一个 Testbench = DUT + 激励 + 分析 + 变量 + 模型 include，
自己负责拼出 spectre 网表；仿真执行交给 sim.Simulator。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .netlist import DeviceMap, Netlister, fmt_value
from .vdb import Design, num


# --------------------------------------------------------------------------- #
# 分析
# --------------------------------------------------------------------------- #

@dataclass
class Analysis:
    name: str
    kind: str
    args: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        kv = " ".join(f"{k}={fmt_value(v)}" for k, v in self.args.items() if v is not None)
        return f"{self.name} {self.kind} {kv}".strip()

    def plot_file(self) -> str:
        """psfascii 模式下 spectre 写出的文件名（<analysis>.<type>）。"""
        ext = {"tran": "tran.tran", "sp": "sp", "pss": "pss", "dc": "dc"}.get(self.kind, self.kind)
        return f"{self.name}.{ext}"


def dc(name: str = "dc", *, oppoint: bool = True, **kw) -> Analysis:
    args = dict(kw)
    if oppoint:
        args.setdefault("oppoint", "rawfile")
    return Analysis(name, "dc", args)


def dc_sweep(name: str = "dcsw", *, source: str, param: str = "dc",
             start: float, stop: float, step: float, **kw) -> Analysis:
    return Analysis(name, "dc", {"dev": source, "param": param, "start": start,
                                 "stop": stop, "step": step, **kw})


def ac(name: str = "ac", *, start: float = 1e6, stop: float = 10e9,
       dec: int = 10, **kw) -> Analysis:
    return Analysis(name, "ac", {"start": start, "stop": stop, "dec": dec, **kw})


def tran(name: str = "tran", *, stop: float, step: float | None = None,
         start: float = 0.0, **kw) -> Analysis:
    args = {"start": start, "stop": stop, **kw}
    if step:
        args["step"] = step
    return Analysis(name, "tran", args)


def noise(name: str = "noise", *, start: float = 1e3, stop: float = 1e9,
          dec: int = 10, iprobe: str | None = None, oprobe: str | None = None,
          **kw) -> Analysis:
    args = {"start": start, "stop": stop, "dec": dec, **kw}
    if iprobe:
        args["iprobe"] = iprobe
    if oprobe:
        args["oprobe"] = oprobe
    return Analysis(name, "noise", args)


def sp(name: str = "sp", *, start: float = 1e6, stop: float = 10e9,
       step: float | None = None, dec: int | None = 10, **kw) -> Analysis:
    args = {"start": start, "stop": stop, **kw}
    if step:
        args["step"] = step
    else:
        args["dec"] = dec
    return Analysis(name, "sp", args)


def pss(name: str = "pss", *, fund: float, harms: int = 10, **kw) -> Analysis:
    return Analysis(name, "pss", {"fund": fund, "harms": harms, **kw})


# --------------------------------------------------------------------------- #
# 测试台
# --------------------------------------------------------------------------- #

@dataclass
class Testbench:
    design: Design
    top: str | None = None            # None = 纯网表测试台（没有 DUT 层次）
    view: str = "schematic"
    devmap: DeviceMap = field(default_factory=DeviceMap)
    variables: dict[str, Any] = field(default_factory=dict)
    includes: list[tuple[str, str | None]] = field(default_factory=list)
    elements: list[str] = field(default_factory=list)
    analyses: list[Analysis] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    globals: tuple[str, ...] = ("0",)
    saves: list[str] = field(default_factory=list)
    title: str = "vlight testbench"
    lib_prefix: bool = False
    strict: bool = True
    flatten: bool = False        # DUT 顶层展开到 TB（能看到/偏置内部节点）

    # -- 组装 ------------------------------------------------------------- #
    def include(self, path: str, section: str | None = None) -> "Testbench":
        self.includes.append((path, section))
        return self

    def var(self, **kv) -> "Testbench":
        self.variables.update(kv)
        return self

    def element(self, *lines: str) -> "Testbench":
        self.elements.extend(lines)
        return self

    def analysis(self, *a: Analysis) -> "Testbench":
        self.analyses.extend(a)
        return self

    def save(self, *names: str) -> "Testbench":
        self.saves.extend(names)
        return self

    def option(self, **kv) -> "Testbench":
        self.options.update(kv)
        return self

    # -- 常用激励（省得手写 spectre 语法） --------------------------------- #
    def port(self, name: str, node: str, ref: str = "0", r: float = 50.0,
             num: int = 1, dc: float = 0.0, mag: float = 1.0) -> "Testbench":
        self.elements.append(
            f"{name} ({node} {ref}) port r={fmt_value(r)} num={num} "
            f"dc={fmt_value(dc)} mag={fmt_value(mag)}")
        return self

    def vsource(self, name: str, node: str, ref: str = "0", dc: float = 0.0,
                ac: float | None = None) -> "Testbench":
        mag = "" if ac is None else f" mag={fmt_value(ac)}"
        self.elements.append(f"{name} ({node} {ref}) vsource dc={fmt_value(dc)}{mag}")
        return self

    def load(self, name: str, node: str, ref: str, r: float) -> "Testbench":
        self.elements.append(f"{name} ({node} {ref}) resistor r={fmt_value(r)}")
        return self

    def cap(self, name: str, node: str, ref: str, c: float) -> "Testbench":
        self.elements.append(f"{name} ({node} {ref}) capacitor c={fmt_value(c)}")
        return self

    def ind(self, name: str, node: str, ref: str, l: float) -> "Testbench":
        self.elements.append(f"{name} ({node} {ref}) inductor l={fmt_value(l)}")
        return self

    def isource(self, name: str, node: str, ref: str, dc: float = 0.0) -> "Testbench":
        self.elements.append(f"{name} ({node} {ref}) isource dc={fmt_value(dc)}")
        return self

    # -- 输出 ------------------------------------------------------------- #
    def scs(self) -> str:
        nl = Netlister(self.design, self.devmap, self.globals,
                       lib_prefix=self.lib_prefix, strict=self.strict)
        sch = self.design.schematic(self.top, self.view) if self.top else None
        out = [f"// {self.title}", "simulator lang=spectre", "global " + " ".join(self.globals)]
        for path, section in self.includes:
            inc = f'include "{path}"'
            if section:
                inc += f" section={section}"
            out.append(inc)
        if self.variables:
            kvs = " ".join(f"{k}={fmt_value(v)}" for k, v in self.variables.items())
            out.append(f"parameters {kvs}")
        out.append("")
        if self.top is None:                       # 纯网表：所有 cell 都当 subckt 发
            for lib in self.design.libs.values():
                for cell in lib.cells.values():
                    if "schematic" in cell.schematics:
                        out.append(nl.subckt(f"{lib.name}/{cell.name}"))
                        out.append("")
        elif self.flatten:
            # 只发子层次 subckt；顶层自身展开，内部网络在 TB 里可见（可偏置/可探测）
            subs = [m for m in self.design.topo(self.top) if m != self.top]
            if subs:
                out.append("\n\n".join(nl.subckt(m) for m in subs))
                out.append("")
        else:
            out.append(nl.subckts(self.top))
        out.append("")
        if self.top is None:
            name, nodes = "", ""
        else:
            name = self.top.split("/")[-1]
            nodes = " ".join(p.net or p.name for p in sch.pins)
        out.append("// design under test")
        if self.top is None:
            pass
        elif self.flatten:
            for inst in sch.insts:
                from .netlist import is_graphic
                if is_graphic(inst.master):
                    continue
                out.append("  " + nl.instance_line(inst, sch))
        else:
            out.append(f"{name} ({nodes}) {nl._name(self.top)}")
        if self.elements:
            out.append("// testbench")
            out.extend(self.elements)
        if self.options:
            kvs = " ".join(f"{k}={fmt_value(v)}" for k, v in self.options.items())
            out.append(f"vlightOptions options {kvs}")
        if self.saves:
            out.append("save " + " ".join(self.saves))
        for a in self.analyses:
            out.append(a.line())
        return "\n".join(out) + "\n"

    def netlist_issues(self) -> list[str]:
        return [] if self.top is None else self.design.schematic(self.top, self.view).check()


def from_spec(design: Design, top: str, *, vdd: float = 1.2,
              port_nodes: dict[str, str] | None = None, temp: float = 27.0) -> Testbench:
    """按常见 RF 测试台模板建 TB：电源 + 端口 + 温度。"""
    tb = Testbench(design, top)
    tb.var(vdd=vdd)
    sch = design.schematic(top)
    names = {p.name for p in sch.pins}
    tb.vsource("VDD", "vdd", "0", dc=vdd) if "vdd" in names else None
    for pname, node in (port_nodes or {}).items():
        tb.port(pname, node)
    tb.option(temp=temp)
    return tb


Testbench.__test__ = False          # 别让 pytest 把它当测试类

__all__ = ["Analysis", "Testbench", "dc", "dc_sweep", "ac", "tran", "noise",
           "sp", "pss", "from_spec", "num"]
