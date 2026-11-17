"""spicein —— 反向解析 Spectre/SPICE 网表（Virtuoso 的 "spiceIn" 对应物）。

现实里大量设计只以网表形式存在（别人给的、脚本生成的、仿真目录里的），
先把它们读进 vdb，才能编辑/重跑/换工艺。支持：

    simulator lang=spectre      global                   include "... " section=tt
    parameters a=1 b=2          subckt / ends            element: NAME (n1 n2) master k=v
    analysis: name dc|ac|tran|noise|sp|pss|pnoise|xf ...  save / options / ic / nodeset
    注释: //  *  续行: \\   数值表达式保留原文（dc=Ibias、fac1=0.3*sigma）

解析结果既能转成 vdb（每个 subckt 一个 cell），也能当 Testbench 直接丢给 spectre。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .vdb import Design, Instance, Library, Schematic

ANALYSES = {"dc", "ac", "tran", "noise", "sp", "pss", "pnoise", "xf", "stb",
            "tf", "sens", "disto", "hb", "hbac", "envlp", "pz", "mpde", "op"}
_MASTER_HINT = re.compile(r"^\s*(\S+)\s*\(\s*([^)]*)\)\s*(\S+)")


@dataclass
class Element:
    name: str
    nodes: list[str]
    master: str
    params: dict[str, str] = field(default_factory=dict)
    line: int = 0

    def to_line(self) -> str:
        kv = " ".join(f"{k}={v}" for k, v in self.params.items())
        body = " ".join(self.nodes)
        return f"{self.name} ({body}) {self.master}" + (f" {kv}" if kv else "")


@dataclass
class Subckt:
    name: str
    pins: list[str] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    elements: list[Element] = field(default_factory=list)

    def to_text(self) -> str:
        head = f"subckt {self.name} ({' '.join(self.pins)})"
        if self.params:
            head += " " + " ".join(f"{k}={v}" for k, v in self.params.items())
        return "\n".join([head] + ["  " + e.to_line() for e in self.elements]
                         + [f"ends {self.name}"])


@dataclass
class AnalysisStmt:
    name: str
    kind: str
    args: dict[str, str] = field(default_factory=dict)

    def to_line(self) -> str:
        kv = " ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.name} {self.kind}" + (f" {kv}" if kv else "")


@dataclass
class Netlist:
    path: str = ""
    simulator: str = "spectre"
    globals: list[str] = field(default_factory=lambda: ["0"])
    parameters: dict[str, str] = field(default_factory=dict)
    includes: list[tuple[str, str | None]] = field(default_factory=list)
    subckts: list[Subckt] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    analyses: list[AnalysisStmt] = field(default_factory=list)
    saves: list[str] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)
    models: dict[str, dict] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)

    # -- 出口 ------------------------------------------------------------- #
    def to_scs(self) -> str:
        out = ["simulator lang=" + self.simulator]
        if self.globals:
            out.append("global " + " ".join(self.globals))
        for path, section in self.includes:
            inc = f'include "{path}"'
            if section:
                inc += f" section={section}"
            out.append(inc)
        if self.parameters:
            out.append("parameters " + " ".join(f"{k}={v}" for k, v in self.parameters.items()))
        out.append("")
        for s in self.subckts:
            out.append(s.to_text())
            out.append("")
        for e in self.elements:
            out.append(e.to_line())
        if self.options:
            out.append("vlightOptions options " +
                       " ".join(f"{k}={v}" for k, v in self.options.items()))
        if self.saves:
            out.append("save " + " ".join(self.saves))
        for a in self.analyses:
            out.append(a.to_line())
        return "\n".join(out) + "\n"

    # -- 转 vdb ----------------------------------------------------------- #
    def to_design(self, lib_name: str = "imported") -> Design:
        design = Design()
        lib = design.add(Library(lib_name, Path(f"<imported>/{lib_name}")))
        for s in self.subckts:
            cell = lib.cell(s.name, create=True)
            sch = cell.schematics["schematic"]
            for p in s.pins:
                sch.add_pin(p, "inputOutput")
            for e in s.elements:
                sch.add_inst(Instance(e.name, lib_name, e.master, "symbol",
                                      params=dict(e.params)))
                for k, node in enumerate(e.nodes):
                    sch.connect(e.name, _term_name(e, k), node)
        return design

    # -- 统计 ------------------------------------------------------------- #
    def stats(self) -> dict:
        return {
            "subckts": len(self.subckts),
            "elements": len(self.elements) + sum(len(s.elements) for s in self.subckts),
            "analyses": [a.kind for a in self.analyses],
            "includes": len(self.includes),
            "parameters": len(self.parameters),
        }


def _term_name(e: Element, index: int) -> str:
    """网表节点顺序 -> 端子名。已知器件用标准名，否则 T0/T1/..."""
    orders = {
        ("nmos", 4): ["D", "G", "S", "B"], ("pmos", 4): ["D", "G", "S", "B"],
        ("res", 2): ["PLUS", "MINUS"], ("cap", 2): ["PLUS", "MINUS"],
        ("ind", 2): ["PLUS", "MINUS"], ("isource", 2): ["PLUS", "MINUS"],
        ("vsource", 2): ["PLUS", "MINUS"],
    }
    m = e.master.lower()
    for (kind, n), names in orders.items():
        if kind in m and len(e.nodes) == n:
            return names[index]
    if len(e.nodes) == 4:                 # 四端器件基本都是 MOS
        return ["D", "G", "S", "B"][index]
    if len(e.nodes) == 2:                 # 两端基本都是二端元件
        return ["PLUS", "MINUS"][index]
    return f"T{index}"


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def _strip_comments(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw
        if line.lstrip().startswith(("*", "//")):
            continue
        line = re.sub(r"//.*$", "", line)
        line = re.sub(r"/\*.*?\*/", "", line)
        out.append(line.rstrip())
    return out


def _join_continuations(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buf = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        merged.append((buf + line).strip())
        buf = ""
    if buf:
        merged.append(buf.strip())
    return [m for m in merged if m]


def _split_kv(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    """把 ['a', 'k=v', 'k2=1'] 拆成 (['a'], {'k': 'v', ...})。"""
    head, kv = [], {}
    for t in tokens:
        if "=" in t and not t.startswith("="):
            k, _, v = t.partition("=")
            kv[k] = v
        else:
            head.append(t)
    return head, kv


def _tokenize(line: str) -> list[str]:
    """按空白切，但括号内保持一组、引号内不切。"""
    tokens, buf, depth, quote = [], "", 0, False
    for ch in line:
        if ch == '"':
            quote = not quote
            buf += ch
        elif quote:
            buf += ch
        elif ch == "(":
            depth += 1
            buf += ch
        elif ch == ")":
            depth -= 1
            buf += ch
        elif ch.isspace() and depth == 0:
            if buf:
                tokens.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def parse(text: str, path: str = "") -> Netlist:
    nl = Netlist(path=path)
    cur: Subckt | None = None
    for lineno, line in enumerate(_join_continuations(_strip_comments(text)), 1):
        toks = _tokenize(line)
        if not toks:
            continue
        head = toks[0]
        low = head.lower()
        if low == "simulator":
            for t in toks[1:]:
                if t.startswith("lang="):
                    nl.simulator = t.split("=", 1)[1]
            continue
        if low == "global":
            nl.globals = toks[1:]
            continue
        if low == "include" and len(toks) >= 2:
            p = toks[1].strip('"')
            sec = next((t.split("=", 1)[1] for t in toks[2:]
                        if t.startswith("section=")), None)
            nl.includes.append((p, sec))
            continue
        if low == "model":
            # model <name> <type> [k=v ...]
            if len(toks) >= 3:
                name, mtype = toks[1], toks[2]
                params = dict(_split_kv(toks[3:])[1])
                nl.models[name] = {"_master": mtype, **params}
            continue
        if low == "parameters":
            nl.parameters.update(dict(_split_kv(toks[1:])[1]))
            continue
        if low == "subckt":
            rest = " ".join(toks[1:])
            m = re.match(r"(\S+)\s*(?:\(([^)]*)\))?\s*(.*)", rest)
            name = m.group(1)
            pins = m.group(2).split() if m.group(2) else []
            params = dict(_split_kv(m.group(3).split())[1])
            cur = Subckt(name, pins, params)
            nl.subckts.append(cur)
            continue
        if low == "ends":
            cur = None
            continue
        if low == "save":
            nl.saves.extend(toks[1:])
            continue
        if low in ("ic", "nodeset"):
            continue
        if low.endswith("options") or low == "options":
            args = dict(_split_kv(toks[1:])[1])
            nl.options.update(args)
            continue
        m = _MASTER_HINT.match(line)
        if m:
            name, nodes, master = m.group(1), m.group(2).split(), m.group(3)
            rest = line[m.end():].strip()
            params = dict(_split_kv(_tokenize(rest))[1]) if rest else {}
            elem = Element(name, nodes, master, params, lineno)
            (cur.elements if cur is not None else nl.elements).append(elem)
            continue
        if low in ANALYSES:
            # 形如: ac1 ac start=.. stop=..
            args = dict(_split_kv(_tokenize(" ".join(toks[2:])))[1])
            nl.analyses.append(AnalysisStmt(head, low, args))
            continue
        if len(toks) >= 2 and toks[1].lower() in ANALYSES:
            kind = toks[1].lower()
            args = dict(_split_kv(toks[2:])[1])
            if kind in ("pnoise",) and len(toks) > 2 and toks[2].startswith("("):
                args["probe"] = toks[2]
            nl.analyses.append(AnalysisStmt(head, kind, args))
            continue
        nl.raw_lines.append(line)
    return nl


def read(path: str | Path) -> Netlist:
    p = Path(path)
    return parse(p.read_text(errors="replace"), str(p))


# --------------------------------------------------------------------------- #
# 与 Testbench 互通
# --------------------------------------------------------------------------- #

def to_testbench(nl: Netlist, design: Design | None = None):
    """把网表直接变成一个可跑的 Testbench（DUT 由网表自己描述）。"""
    from .netlist import DeviceMap
    from .tb import Analysis, Testbench

    design = design or nl.to_design()
    tb = Testbench(design, top=None, title=f"imported {Path(nl.path).name}")
    tb.globals = tuple(nl.globals) or ("0",)
    for path, section in nl.includes:
        tb.include(path, section)
    tb.var(**nl.parameters)
    for e in nl.elements:
        tb.element(e.to_line())
    for a in nl.analyses:
        args = {k: v for k, v in a.args.items() if k != "probe"}
        tb.analysis(Analysis(a.name, a.kind, args))
    if nl.saves:
        tb.save(*nl.saves)
    if nl.options:
        tb.option(**nl.options)
    return tb


__all__ = ["Netlist", "Subckt", "Element", "AnalysisStmt", "parse", "read",
           "to_testbench"]
