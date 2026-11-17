"""ocean —— OCEAN 脚本接口（ADE 的命令行自动化语言）。

Virtuoso 里 `ocean -restore script.ocn` 是批处理仿真的标准做法。这里是同一个 API 的子集：

    design(lib cell view)        desVar("name" value)      analysis('ac ?start .. ?stop ..)
    save('v "/out")              temp(27)                  run()
    selectResult('ac)            ocnPrint(expr)            plot(expr)
    v("/out")  i("/V1/PLUS")     ymax ymin xmax xmin cross average rms integ value dB20
    openResults("dir")           foreach / let / procedure（复用 SKILL 解释器）

底层就是 vlight 的 Testbench + Simulator，所以远端 ssh 跑 spectre 也一样能用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import skill
from .psf import Plot
from .sim import Results, Simulator
from .tb import Analysis, Testbench


@dataclass
class Wave:
    """OCEAN 的波形对象：x 轴 + 一组 y。"""
    x: np.ndarray
    y: np.ndarray
    name: str = ""

    def __len__(self):
        return len(self.x)

    def __repr__(self):
        return f"<Wave {self.name or 'wave'} n={len(self.x)}>"

    # 算术
    def _bin(self, other, op):
        if isinstance(other, Wave):
            return Wave(self.x, op(self.y, other.y), f"({self.name}{op.__name__}{other.name})")
        return Wave(self.x, op(self.y, other), self.name)

    def __add__(self, o): return self._bin(o, np.add)
    def __sub__(self, o): return self._bin(o, np.subtract)
    def __mul__(self, o): return self._bin(o, np.multiply)
    def __truediv__(self, o): return self._bin(o, np.divide)
    __radd__ = __add__
    __rmul__ = __mul__


def _wave(x) -> Wave:
    if isinstance(x, Wave):
        return x
    return Wave(np.asarray([0.0]), np.asarray([float(x)]), "const")


def _val(x) -> float:
    return float(np.real(np.asarray(x.y if isinstance(x, Wave) else x).reshape(-1)[-1]))


class Ocean:
    """一次 OCEAN 会话。"""

    def __init__(self, design, simulator: Simulator | None = None,
                 workdir: str = "ocean"):
        self.design = design
        self.sim = simulator or Simulator(workdir=workdir)
        self.workdir = workdir
        self.dut: tuple[str, str, str] | None = None
        self.analyses: list[Analysis] = []
        self.variables: dict[str, Any] = {}
        self.elements: list[str] = []
        self.includes: list[tuple[str, str | None]] = []
        self.saves: list[str] = []
        self.options: dict[str, Any] = {}
        self.results: Results | None = None
        self.plot: Plot | None = None
        self.printed: list[str] = []
        self.session = skill.Session(design)
        self._install()

    # ------------------------------------------------------------------ #
    def _install(self) -> None:
        e = self.session.env.vars
        e.update({
            "design": self.design_,
            "desVar": self.des_var,
            "analysis": self.analysis,
            "save": self.save,
            "temp": self.temp,
            "run": self.run_,
            "selectResult": self.select_result,
            "openResults": self.open_results,
            "ocnPrint": self.ocn_print,
            "ocnDisplay": self.ocn_print,
            "plot": self.ocn_print,
            "v": self.v,
            "i": self.i,
            "ymax": lambda w: _val(_wave(w).y.max()),
            "ymin": lambda w: _val(_wave(w).y.min()),
            "xmax": lambda w: float(_wave(w).x[int(np.argmax(np.real(_wave(w).y)))]),
            "xmin": lambda w: float(_wave(w).x[int(np.argmin(np.real(_wave(w).y)))]),
            "average": lambda w: float(np.mean(np.real(_wave(w).y))),
            "rms": lambda w: float(np.sqrt(np.mean(np.abs(_wave(w).y) ** 2))),
            "integ": lambda w: float((getattr(np, "trapezoid", None) or np.trapz)(
                np.real(_wave(w).y), _wave(w).x)),
            "value": lambda w: _val(w),
            "dB20": lambda w: Wave(_wave(w).x, 20 * np.log10(np.abs(_wave(w).y) + 1e-300), "dB20"),
            "dB10": lambda w: Wave(_wave(w).x, 10 * np.log10(np.abs(_wave(w).y) + 1e-300), "dB10"),
            "abs": lambda w: Wave(_wave(w).x, np.abs(_wave(w).y), "abs"),
            "mag": lambda w: Wave(_wave(w).x, np.abs(_wave(w).y), "mag"),
            "real": lambda w: Wave(_wave(w).x, np.real(_wave(w).y), "real"),
            "imag": lambda w: Wave(_wave(w).x, np.imag(_wave(w).y), "imag"),
            "phase": lambda w: Wave(_wave(w).x, np.degrees(np.angle(_wave(w).y)), "phase"),
            "deriv": lambda w: Wave(_wave(w).x, np.gradient(np.real(_wave(w).y),
                                                            np.real(_wave(w).x)), "deriv"),
            "cross": self.cross,
            "element": self.element,
            "include": self.include,
            # OCEAN 里 null/其他日志函数的无害替身
            "ocnHelp": lambda *a: None,
            "ocnCloseSession": lambda *a: True,
        })

    # ------------------------------------------------------------------ #
    # OCEAN 函数
    # ------------------------------------------------------------------ #
    def design_(self, lib, cell, view="schematic"):
        self.dut = (str(lib), str(cell), str(view))
        return f"{lib}/{cell}/{view}"

    def des_var(self, name, value):
        self.variables[str(name)] = value
        return value

    def analysis(self, kind, *kv):
        kind = str(kind)
        args: dict[str, Any] = {}
        i = 0
        while i + 1 < len(kv) + 1 and i < len(kv):
            key = kv[i]
            if isinstance(key, skill.Symbol) or (isinstance(key, str) and key.startswith("?")):
                name = str(key).lstrip("?")
                val = kv[i + 1] if i + 1 < len(kv) else None
                args[name] = val
                i += 2
            else:
                i += 1
        a = Analysis(f"{kind}1", kind, args)
        self.analyses.append(a)
        return a

    def save(self, kind, path):
        sig = f"{str(kind)} {path}".strip('"')
        self.saves.append(str(path).strip('"'))
        return sig

    def temp(self, value):
        self.options["temp"] = value
        return value

    def element(self, line):
        self.elements.append(str(line))
        return True

    def include(self, path, section=None):
        self.includes.append((str(path).strip('"'), section))
        return True

    # ------------------------------------------------------------------ #
    def run_(self) -> Any:
        top = f"{self.dut[0]}/{self.dut[1]}" if self.dut else None
        flatten = top is not None
        tb = Testbench(self.design, top, flatten=flatten, title="ocean")
        tb.globals = ("0",)
        for path, section in self.includes:
            tb.include(path, section)
        tb.var(**self.variables)
        if self.options:
            tb.option(**self.options)
        for line in self.elements:
            tb.element(line)
        for a in self.analyses:
            tb.analysis(a)
        if self.saves:
            tb.save(*self.saves)
        self.results = self.sim.run(tb, name=f"ocean_{len(self.analyses)}")
        if self.analyses:
            self.select_result(self.analyses[-1].kind)
        return True

    def select_result(self, kind):
        if self.results is None:
            raise RuntimeError("还没有 run()")
        self.plot = self.results.plot(str(kind))
        return True

    def open_results(self, path):
        from .sim import collect_raw
        raw = Path(path) / "raw"
        self.results = collect_raw(raw if raw.is_dir() else Path(path))
        if self.results.plots:
            self.plot = next(iter(self.results.plots.values()))
        return True

    # -- 取信号 ---------------------------------------------------------- #
    def _sig(self, path) -> Wave:
        if self.plot is None:
            raise RuntimeError("先 selectResult('ac|dc|tran)")
        name = str(path).strip('"').lstrip("/")
        p = self.plot
        for cand in (name, name.replace("/", "."), name.replace(".", "/")):
            if cand in p.y:
                return Wave(p.x, p.y[cand], cand)
        for cand in p.y:
            if cand.lower() == name.lower():
                return Wave(p.x, p.y[cand], cand)
        raise KeyError(f"结果里没有 {name!r}；有 {list(p.y)[:10]}")

    def v(self, node) -> Wave:
        return self._sig(node)

    def i(self, branch) -> Wave:
        raw = str(branch).strip('"').lstrip("/")
        cands = [raw]
        if "/" in raw:                                    # /V1/PLUS -> V1:p
            inst, _, term = raw.partition("/")
            cands += [f"{inst}:{term[0].lower()}", f"{inst}:{term}"]
        for c in cands:
            try:
                return self._sig(c)
            except KeyError:
                continue
        return self._sig(raw)

    # -- 测量 ------------------------------------------------------------ #
    def cross(self, wave, level=0.0, n=1, edge="either"):
        w = _wave(wave)
        y = np.real(w.y) - float(level)
        idx = np.where(np.sign(y[:-1]) != np.sign(y[1:]))[0]
        if len(idx) == 0:
            return float("nan")
        k = 0 if n > 0 else len(idx) - 1
        i = int(idx[k])
        denom = y[i] - y[i + 1]
        t = 0.0 if denom == 0 else y[i] / denom
        return float(w.x[i] + t * (w.x[i + 1] - w.x[i]))

    def ocn_print(self, value) -> Any:
        if isinstance(value, Wave):
            txt = f"{value.name or 'wave'}: last={_val(value):.6g} n={len(value)}"
        else:
            txt = f"{_val(value):.6g}" if isinstance(value, (int, float)) else str(value)
        self.printed.append(txt)
        print(txt)
        return value

    # ------------------------------------------------------------------ #
    def run_script(self, text: str):
        out = self.session.run(text)
        return out


def run_ocean(design, script: str, simulator: Simulator | None = None,
              workdir: str = "ocean") -> Ocean:
    oc = Ocean(design, simulator, workdir)
    oc.run_script(script)
    return oc


__all__ = ["Ocean", "Wave", "run_ocean"]
