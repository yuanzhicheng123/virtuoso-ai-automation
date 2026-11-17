"""ade —— ADE 状态（analyses / variables / outputs / model files）的读写。

两条路：
1. 我们自己的状态文件（JSON），`ADELState.save/load`。
2. **读真实 ADE 的 `*.state` 目录**。这些文件其实是 SKILL 代码
   （`analysis(dc fields start) "0"`、`savedComponent = (let (...) ...)`），
   所以直接丢给 `vlight.skill` 解释器执行，再把结构体取出来——不用猜格式。

实测样本：`RFIC-GPT_Wizard_Active/CSPA/CSPA_DM_lvt4t/webdisplay_state1/`
（23 个 .state，dc+sp 分析、temp=85、13 个变量、若干 output 表达式）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import skill
from .tb import Analysis, Testbench, ac, dc, noise, pss, sp, tran
from .vdb import Design, num, parse_skill, unparse

_LINE = re.compile(r"^\(?\s*(\w+)[\s(]([^)]*)\)\s*(.*)$")


@dataclass
class Output:
    name: str
    expression: str = ""
    signal: str = ""
    raw: Any = None

    def to_dict(self) -> dict:
        return {"name": self.name, "expression": self.expression,
                "signal": self.signal}


@dataclass
class ADELState:
    name: str = "ade"
    lib: str = ""
    cell: str = ""
    view: str = "schematic"
    analyses: list[Analysis] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    outputs: list[Output] = field(default_factory=list)
    model_files: list[tuple[str, str]] = field(default_factory=list)
    elements: list[str] = field(default_factory=list)      # 激励（对应 graphicalStimuli）
    options: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)

    # -- 存取 ------------------------------------------------------------- #
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "format": "vlight.ade/1", "name": self.name,
            "lib": self.lib, "cell": self.cell, "view": self.view,
            "analyses": [{"name": a.name, "kind": a.kind,
                          "args": {k: (v if not isinstance(v, float) else repr(v))
                                   for k, v in a.args.items()}}
                         for a in self.analyses],
            "variables": self.variables,
            "outputs": [o.to_dict() for o in self.outputs],
            "modelFiles": [list(m) for m in self.model_files],
            "elements": self.elements,
            "options": self.options, "env": self.env,
        }, indent=1, ensure_ascii=False) + "\n")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "ADELState":
        d = json.loads(Path(path).read_text())
        st = cls(name=d.get("name", "ade"), lib=d.get("lib", ""),
                 cell=d.get("cell", ""), view=d.get("view", "schematic"))
        for a in d.get("analyses", []):
            st.analyses.append(Analysis(a["name"], a["kind"],
                                        {k: num(v) if num(v) is not None else v
                                         for k, v in a.get("args", {}).items()}))
        st.variables = d.get("variables", {})
        st.outputs = [Output(**o) for o in d.get("outputs", [])]
        st.model_files = [tuple(m) for m in d.get("modelFiles", [])]
        st.elements = d.get("elements", [])
        st.options = d.get("options", {})
        st.env = d.get("env", {})
        return st

    # -- 变 Testbench ------------------------------------------------------ #
    def to_testbench(self, design: Design, top: str | None = None,
                     flatten: bool = False) -> Testbench:
        tb = Testbench(design, top, flatten=flatten, title=f"ADE state {self.name}")
        for path, section in self.model_files:
            tb.include(path, section or None)
        for k, v in self.variables.items():
            tb.var(**{k: v})
        if "temp" in self.options:
            tb.option(temp=self.options["temp"])
        for line in self.elements:
            tb.element(line)
        tb.analysis(*self.analyses)
        return tb

    def summary(self) -> dict:
        return {"name": self.name,
                "analyses": [a.kind for a in self.analyses],
                "variables": list(self.variables),
                "outputs": [o.name for o in self.outputs],
                "models": len(self.model_files),
                "options": self.options}


# --------------------------------------------------------------------------- #
# 读真实 ADE 状态目录
# --------------------------------------------------------------------------- #

_STRUCT_FILES = {"variables.state": "variables", "outputs.state": "outputs"}


def _session() -> skill.Session:
    s = skill.Session()
    s.created = []                     # 记录被构造出来的 struct（状态文件最后会清变量）

    def factory(*a, **k):
        obj = skill.SkillStruct()
        s.created.append(obj)
        return obj

    for name in ("make_sevVariableStruct", "make_sevOutputStruct",
                 "make_sevAnalysisStruct", "make_sevModelStruct"):
        s.env.vars[name] = factory
    s.env.vars.setdefault("ncons", lambda x: [x])
    s.env.vars.setdefault("nconc", lambda *ls: sum((list(x or []) for x in ls), []))
    return s


def import_state_dir(path: str | Path) -> ADELState:
    d = Path(path)
    st = ADELState(name=d.name, lib=(d.parent.parent.name if d.parent.parent else ""))
    sess = _session()

    # 1) 简单 key/value 的 state 文件：analysis(...)/opts/envOpts
    ana: dict[str, dict] = {}
    for f in d.glob("*.state"):
        text = f.read_text(errors="replace")
        for line in text.splitlines():
            m = _LINE.match(line.strip())
            if not m:
                continue
            tool, pathparts, raw = m.groups()
            parts = pathparts.split()
            val = parse_skill(raw) if raw else None
            if tool == "analysis":
                if len(parts) < 2:
                    continue
                kind, slot = parts[0], parts[1]
                name = parts[2] if len(parts) > 2 else ""
                ana.setdefault(kind, {"fields": {}, "options": {}})[slot][name] = val
            elif tool == "opts" and parts:
                st.options[parts[0]] = val
            elif tool == "envOpts" and parts:
                st.env[parts[0]] = val

    # 2) analyses：把字段翻成我们的 Analysis
    order = st.env.get("analysisOrder") or list(ana)
    for kind in order:
        info = ana.get(kind)
        if not info:
            continue
        fields = {k: v for k, v in info["fields"].items()
                  if v not in ("", None, [], False)}
        if info["fields"].get("enable") in (None, [], False):
            continue
        st.analyses.append(_to_analysis(kind, fields))

    # 3) savedComponent 块（变量/输出）：直接执行 SKILL
    for fname, slot in _STRUCT_FILES.items():
        f = d / fname
        if not f.is_file():
            continue
        sess.created = []
        try:
            val = sess.run_file(str(f))
        except Exception:                                # noqa: BLE001 状态文件可能不完整
            val = None
        items = sess.created or _flatten_structs(val)
        if slot == "variables":
            for it in items:
                name = getattr(it, "name", None)
                if name:
                    st.variables[str(name)] = getattr(it, "expression", "") or ""
        else:
            for it in items:
                name = getattr(it, "name", None)
                if not name:
                    continue
                raw = getattr(it, "expression", None)
                st.outputs.append(Output(name=str(name).strip(),
                                         expression=expr_to_string(raw),
                                         signal=str(getattr(it, "signal", "") or ""),
                                         raw=None))

    # 4) model files
    mf = d / "modelSetup.state"
    if mf.is_file():
        try:
            data = parse_skill(mf.read_text().strip())
            for item in (data or []):
                if isinstance(item, list) and len(item) >= 2:
                    st.model_files.append((str(item[0]), str(item[1])))
        except Exception:                                # noqa: BLE001
            pass

    # 5) 环境里的设计名
    for key in ("lib", "cell", "view"):
        val = st.env.get(key)
        if isinstance(val, str) and val:
            setattr(st, key, val)
    return st


def _flatten_structs(val) -> list:
    out: list = []

    def walk(v):
        if isinstance(v, skill.SkillStruct):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(val)
    return out


def _to_analysis(kind: str, f: dict) -> Analysis:
    g = lambda k, d=None: num(f.get(k)) if f.get(k) not in (None, "") else d  # noqa: E731
    if kind == "ac":
        return ac("ac", start=g("start", 1.0), stop=g("stop", 1e9),
                  dec=int(g("dec", 10) or 10))
    if kind == "sp":
        if f.get("step"):
            return sp("sp", start=g("start", 1e6), stop=g("stop", 1e10),
                      step=g("step"))
        return sp("sp", start=g("start", 1e6), stop=g("stop", 1e10),
                  dec=int(g("dec", 10) or 10))
    if kind == "tran":
        return tran("tran", stop=g("stop", 1e-6), step=g("step"),
                    start=g("start", 0.0) or 0.0)
    if kind == "dc":
        return dc("dc")
    if kind == "noise":
        return noise("noise", start=g("start", 1e3), stop=g("stop", 1e9),
                     dec=int(g("dec", 10) or 10))
    if kind == "pss":
        return pss("pss", fund=g("fund", 1e9))
    return Analysis(kind, kind, {})


# --------------------------------------------------------------------------- #
# output 表达式（ADE 存的是 list(fn, arg...) 树）
# --------------------------------------------------------------------------- #

def expr_to_string(tree) -> str:
    """把 `list('ymin, list('b1f, ...))` 这种编译形式还原成表达式文本。"""
    if tree is None:
        return ""
    if isinstance(tree, str):
        return tree
    if isinstance(tree, (int, float)):
        return repr(tree)
    if isinstance(tree, list):
        if not tree:
            return "()"
        head, args = tree[0], tree[1:]
        if isinstance(head, str) and not isinstance(head, skill.Symbol):
            return '"%s"' % head
        rendered, i = [], 0
        while i < len(args):
            a = args[i]
            if isinstance(a, skill.Symbol) and str(a).startswith("?"):
                key = str(a)[1:]
                val = args[i + 1] if i + 1 < len(args) else ""
                rendered.append(f"{key}={expr_to_string(val)}")
                i += 2
                continue
            rendered.append(expr_to_string(a))
            i += 1
        return f"{head}({', '.join(rendered)})"
    if isinstance(tree, skill.Symbol):
        return str(tree)
    return unparse(tree)


__all__ = ["ADELState", "Output", "import_state_dir", "expr_to_string"]
