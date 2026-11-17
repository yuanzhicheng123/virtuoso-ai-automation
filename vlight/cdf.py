"""cdf —— 器件参数引擎（Virtuoso CDF 的等价物）。

为什么必须有这一层：Virtuoso 的网表不是从实例属性读的，而是从 **CDF 有效值**读的。
CDF 里既有默认值，也有表达式（`iPar("model")`、`pPar("w")`）和回调
（`netlistProcedure`、`simInfo`）。上一版把这些硬编码进了映射表，器件一换就得改代码。

这里做三件事：
1. `CDF`/`CDFParam`：一个 master 的参数表（默认值、类型、prompt、仿真信息）。
2. `effective(inst)`：实例覆盖值 > base 默认值，并求值 `iPar`/`pPar`。
3. `netlistProcedure` 注册表：master 可以带自己的网表过程（analogLib/PDK 就是这么干的）。

可以从真实 dump 建 CDF：`CDFdb.from_dump(read_dump_text(...))`
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .vdb import Instance, num, parse_skill

_IPAR = re.compile(r'^\s*iPar\s*\(\s*"([^"]+)"\s*\)\s*$')
_PPAR = re.compile(r'^\s*pPar\s*\(\s*"([^"]+)"\s*\)\s*$')


@dataclass
class CDFParam:
    name: str
    default: Any = None
    type: str = "string"           # string / number / boolean / cyclic / radio
    prompt: str = ""
    units: str = ""
    parse_as: str = ""             # SKILL 侧的存储类型（instData 等）
    callback: str = ""
    editable: bool = True

    def value(self, inst_params: dict[str, Any], pcell_params: dict[str, Any] | None = None) -> Any:
        raw = inst_params.get(self.name, self.default)
        return evaluate_expr(raw, inst_params, pcell_params)


@dataclass
class CDF:
    lib: str
    cell: str
    params: list[CDFParam] = field(default_factory=list)
    term_order: list[str] = field(default_factory=list)
    netlist_procedure: str = ""
    sim_info: dict[str, Any] = field(default_factory=dict)
    prop: dict[str, Any] = field(default_factory=dict)

    # -- 参数访问 --------------------------------------------------------- #
    def names(self) -> list[str]:
        return [p.name for p in self.params]

    def param(self, name: str) -> CDFParam | None:
        for p in self.params:
            if p.name == name:
                return p
        return None

    def add(self, p: CDFParam) -> CDFParam:
        old = self.param(p.name)
        if old is not None:
            self.params[self.params.index(old)] = p
            return p
        self.params.append(p)
        return p

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def effective(self, inst: Instance, pcell: dict | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for p in self.params:
            val = p.value(inst.params, pcell)
            if val is not None and val != "":
                out[p.name] = val
        for k, v in inst.params.items():        # CDF 里没声明的实例属性也带上
            out.setdefault(k, v)
        return out

    def __repr__(self) -> str:
        return f"<CDF {self.lib}/{self.cell} params={len(self.params)}>"


# --------------------------------------------------------------------------- #
# 表达式求值（iPar / pPar / 字面量）
# --------------------------------------------------------------------------- #

def evaluate_expr(value: Any, inst_params: dict[str, Any],
                  pcell_params: dict[str, Any] | None = None) -> Any:
    """CDF 值求值：iPar("x") -> 实例参数，pPar("x") -> pcell 参数。"""
    if not isinstance(value, str):
        return value
    m = _IPAR.match(value)
    if m:
        return inst_params.get(m.group(1))
    m = _PPAR.match(value)
    if m:
        return (pcell_params or {}).get(m.group(1))
    return value


def ipar(inst_params: dict[str, Any], name: str) -> Any:
    return inst_params.get(name)


# --------------------------------------------------------------------------- #
# CDF 数据库
# --------------------------------------------------------------------------- #

class CDFdb:
    """所有 master 的 CDF 集合（等价 `cdfGetBaseCellCDF` 的仓库）。"""

    def __init__(self):
        self._cdf: dict[str, CDF] = {}

    # -- 增删查 ----------------------------------------------------------- #
    def add(self, cdf: CDF) -> CDF:
        self._cdf[f"{cdf.lib}/{cdf.cell}"] = cdf
        return cdf

    def get(self, lib: str, cell: str) -> CDF | None:
        return self._cdf.get(f"{lib}/{cell}") or self._cdf.get(cell)

    def require(self, lib: str, cell: str, create: bool = True) -> CDF:
        cdf = self.get(lib, cell)
        if cdf is None:
            if not create:
                raise KeyError(f"没有 {lib}/{cell} 的 CDF")
            cdf = self.add(CDF(lib, cell))
        return cdf

    def __len__(self) -> int:
        return len(self._cdf)

    def __iter__(self):
        return iter(self._cdf.values())

    # -- 从 SKILL dump 建 -------------------------------------------------- #
    def from_dump_text(self, text: str) -> int:
        """dump 里的 `P <name> <type> = <value>` 行就是 CDF 参数。"""
        cur: CDF | None = None
        seen: dict[str, CDFParam] = {}
        n = 0
        for line in text.splitlines():
            m = re.match(r'^SCH\s+(\S+)/(\S+)\b', line)
            if m:
                cur = None
                continue
            m = re.match(r'^  I\s+(\S+)\s+(\S+?)/(\S+?)/(\S+?)', line)
            if m:
                cur = self.require(m.group(2), m.group(3))
                seen = {p.name: p for p in cur.params}
                continue
            m = re.match(r"^    P\s+(\S+)\s+(\S+)\s+=\s*(.*)$", line)
            if m and cur is not None:
                name, ptype, raw = m.groups()
                if name in seen:
                    continue
                val = parse_skill(raw) if len(raw) < 512 else None
                p = CDFParam(name=name, default=val, type=_guess_type(val),
                             parse_as=ptype)
                cur.add(p)
                seen[name] = p
                n += 1
        return n

    # -- 便捷 ------------------------------------------------------------- #
    def effective(self, inst: Instance, pcell: dict | None = None) -> dict:
        cdf = self.get(inst.lib, inst.cell)
        if cdf is None:
            return dict(inst.params)
        return cdf.effective(inst, pcell)


def _guess_type(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if v is None:
        return "string"
    return "string"


# --------------------------------------------------------------------------- #
# netlistProcedure
# --------------------------------------------------------------------------- #

NLPROCS: dict[str, Callable] = {}


def netlist_procedure(name: str | None = None):
    """注册一个网表过程：proc(ctx) -> 网表行（或行列表）。"""
    def deco(fn: Callable) -> Callable:
        NLPROCS[name or fn.__name__] = fn
        return fn
    return deco


def run_procedure(name: str, ctx: dict) -> str | None:
    fn = NLPROCS.get(name)
    if fn is None:
        return None
    out = fn(ctx)
    if isinstance(out, (list, tuple)):
        return "\n".join(str(x) for x in out)
    return out


# 几个通用器件过程（analogLib 风格）
@netlist_procedure("vlight_res")
def _nl_res(ctx: dict) -> str:
    p = ctx["params"]
    r = p.get("r", p.get("resistance", "1k"))
    return f"{ctx['name']} ({' '.join(ctx['nodes'])}) resistor r={r}"


@netlist_procedure("vlight_cap")
def _nl_cap(ctx: dict) -> str:
    p = ctx["params"]
    c = p.get("c", p.get("capacitance", "1p"))
    return f"{ctx['name']} ({' '.join(ctx['nodes'])}) capacitor c={c}"


@netlist_procedure("vlight_ind")
def _nl_ind(ctx: dict) -> str:
    p = ctx["params"]
    l = p.get("l", p.get("inductance", "1n"))
    return f"{ctx['name']} ({' '.join(ctx['nodes'])}) inductor l={l}"


@netlist_procedure("vlight_vsource")
def _nl_vsrc(ctx: dict) -> str:
    p = ctx["params"]
    parts = [f"{ctx['name']} ({' '.join(ctx['nodes'])}) vsource"]
    for k in ("dc", "mag", "type"):
        if p.get(k) not in (None, ""):
            parts.append(f"{k}={p[k]}")
    return " ".join(parts)


# 典型 PDK 过程：把 CDF 参数名翻成 spectre 参数名
@netlist_procedure("tsmc_rf_mos")
def _nl_rf_mos(ctx: dict) -> str:
    p, cdf = ctx["params"], ctx.get("cdf")
    model = p.get("model") or (cdf.cell if cdf else ctx["master"])
    kv = {k: p[k] for k in ("lr", "wr", "nr", "m") if p.get(k) not in (None, "", 0)}
    body = " ".join(f"{k}={v}" for k, v in kv.items())
    return f"{ctx['name']} ({' '.join(ctx['nodes'])}) {model} {body}".strip()


def catalog() -> list[str]:
    return sorted(NLPROCS)


__all__ = ["CDFParam", "CDF", "CDFdb", "netlist_procedure", "run_procedure",
           "evaluate_expr", "ipar", "NLPROCS", "catalog"]
