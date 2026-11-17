"""spice —— 自研 SPICE 引擎（MNA + Newton + 时域/频域/噪声）。

不依赖 spectre：把网表读进来自己解。这是"脱离 Cadence 也能复现"的地基。

算法栈：
    改进节点分析 MNA     未知量 = 节点电压 + 电压源/电感支路电流
    Newton-Raphson       非线性器件用伴随模型（gm/gds/gmb），带阻尼与 gmin/源步进
    DC 工作点            gmin stepping -> source stepping -> 直接牛顿
    AC 小信号            jωC / jωL，复矩阵逐频点求解
    瞬态                 后向欧拉（可切梯形），自适应步长用 LTE 估计
    噪声                 电阻热噪声 4kTR、MOS 沟道 8kT·gm/3，伴随法求传递

器件：R C L V I E(VCVS) G(VCCS) D(二极管) M(MOS level-1，含体效应)
用法：
    ckt = spice.Circuit.from_netlist(spicein.parse(text))
    dc  = ckt.dc();  ac = ckt.ac(freqs);  tr = ckt.tran(10e-9, 1e-12)
也能直接接 Testbench：`spice.Engine(design).run(tb)`
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from . import spicein
from .psf import Plot
from .sim import Results
from .vdb import num

KB = 1.380649e-23
T0 = 300.15
GMIN = 1e-12


class SpiceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 元件
# --------------------------------------------------------------------------- #

@dataclass
class Element:
    name: str
    nodes: list[str]
    kind: str                       # R C L V I E G D M
    prev_i: float = 0.0             # 梯形积分的上一拍电流
    prev_v: float = 0.0             # 上一拍电压
    value: float = 0.0
    params: dict[str, float] = field(default_factory=dict)
    dc: float = 0.0
    ac_mag: float = 1.0
    ac_phase: float = 0.0
    waveform: str = "dc"            # dc / sine
    model: str = ""

    # -- 端口 ------------------------------------------------------------- #
    def p(self, key: str, default: float = 0.0) -> float:
        return float(self.params.get(key, default))

    def __repr__(self) -> str:
        return f"<{self.kind} {self.name} {' '.join(self.nodes)}>"


# --------------------------------------------------------------------------- #
# 电路
# --------------------------------------------------------------------------- #

class Circuit:
    def __init__(self, title: str = ""):
        self.title = title
        self.elements: list[Element] = []
        self.node_index: dict[str, int] = {"0": 0}
        self.node_names: list[str] = ["0"]
        self.branches: dict[str, int] = {}      # 元件名 -> 支路未知量下标
        self.models: dict[str, dict[str, float]] = {}
        self.temp = 27.0
        self.warnings: list[str] = []

    # -- 组装 ------------------------------------------------------------- #
    def node(self, name: str) -> int:
        if name not in self.node_index:
            self.node_index[name] = len(self.node_names)
            self.node_names.append(name)
        return self.node_index[name]

    def add(self, e: Element) -> Element:
        self.elements.append(e)
        for n in e.nodes:
            self.node(n)
        return e

    @property
    def n(self) -> int:
        return len(self.node_names) - 1          # 不含地

    def size(self) -> int:
        """节点数 + 支路未知量数。"""
        return self.n + len(self.branches)

    # -- 从网表建 ---------------------------------------------------------- #
    @classmethod
    def from_netlist(cls, nl: spicein.Netlist, temp: float = 27.0) -> "Circuit":
        ckt = cls(nl.path or "imported")
        ckt.temp = temp
        table: dict[str, float] = {}
        for name, val in nl.parameters.items():
            got = _eval_param(val, table)
            table[name] = 0.0 if got is None else got
        ckt.models["__params__"] = table
        for name, m in nl.models.items():          # 模型卡
            ckt.models[name] = {k: (num(v) if num(v) is not None else v)
                                for k, v in m.items() if k != "_master"}
            ckt.models[name]["_master"] = m.get("_master", "")
        subckts = {s.name: s for s in nl.subckts}
        for e in nl.elements:
            ckt._add_element(e, {}, subckts, depth=0, table=table)
        return ckt

    def _add_element(self, e: spicein.Element, bindings: dict[str, str],
                     subckts: dict[str, spicein.Subckt], depth: int = 0,
                     table: dict[str, float] | None = None) -> None:
        table = table if table is not None else self.models.get("__params__", {})
        if depth > 16:
            raise SpiceError("subckt 嵌套过深（可能递归）")
        nodes = [bindings.get(n, n) for n in e.nodes]
        params = {k: _subst(v, bindings) for k, v in e.params.items()}
        master = e.master.lower()

        if e.master in subckts:                       # 层次展开
            sub = subckts[e.master]
            if len(nodes) != len(sub.pins):
                raise SpiceError(f"{e.name}: {sub.name} 需要 {len(sub.pins)} 个节点，给了 {len(nodes)}")
            inner = {p: n for p, n in zip(sub.pins, nodes)}
            for sp, val in sub.params.items():
                inner[sp] = params.get(sp, val)
            for se in sub.elements:
                self._add_element(se, inner, subckts, depth + 1, table)
            return

        if e.master in self.models:
            kind = _kind_of(str(self.models[e.master].get("_master", "")), self.models)
            if kind is not None:
                params = {**self.models[e.master], **e.params}
                el = Element(e.name, nodes, kind, model=e.master)
                pv = {}
                for k, v in params.items():
                    got = _eval_param(v, table)
                    pv[k] = 0.0 if got is None else got
                el.params = pv
                if kind in ("V", "I"):
                    el.dc = pv.get("dc", 0.0)
                    el.ac_mag = pv.get("mag", 0.0)
                    self.branches[el.name] = 0
                elif kind == "L":
                    el.value = pv.get("l", 1e-9)
                    self.branches[el.name] = 0
                elif kind == "E":
                    self.branches[el.name] = 0
                elif kind == "R":
                    el.value = pv.get("r", 1e3)
                elif kind == "C":
                    el.value = pv.get("c", 1e-12)
                self.add(el)
                return
        kind = _kind_of(master, self.models)
        if kind is None:
            self.warnings.append(f"跳过不支持的器件 {e.name} ({e.master})")
            return
        el = Element(e.name, nodes, kind, model=e.master)
        pv = {}
        for k, v in params.items():
            got = _eval_param(v, table)
            pv[k] = 0.0 if got is None else got
        el.params = pv

        if kind == "R":
            el.value = pv.get("r", pv.get("resistance", 1e3))
        elif kind == "C":
            el.value = pv.get("c", pv.get("capacitance", 1e-12))
        elif kind == "L":
            el.value = pv.get("l", pv.get("inductance", 1e-9))
            self.branches[el.name] = 0            # 具体下标在 assemble 时分配
        elif kind == "V":
            el.dc = pv.get("dc", 0.0)
            el.ac_mag = pv.get("mag", 0.0)      # 没写 mag 就不是 AC 激励
            el.ac_phase = pv.get("phase", 0.0)
            el.waveform = str(params.get("type", "dc")).strip('"').lower()
            self.branches[el.name] = 0
        elif kind == "I":
            el.dc = pv.get("dc", 0.0)
            el.ac_mag = pv.get("mag", 1.0)
            el.waveform = str(params.get("type", "dc")).strip('"').lower()
        elif kind == "E":
            el.value = pv.get("gain", 1.0)
            self.branches[el.name] = 0
        elif kind == "G":
            el.value = pv.get("gain", pv.get("gm", 1e-3))
        elif kind == "port":
            # port = 源 + 内阻；等效成 vsource(dc) + resistor
            self.add(Element(e.name, nodes, "V", dc=pv.get("dc", 0.0),
                             ac_mag=pv.get("mag", 0.0), waveform="dc"))
            r = pv.get("r", 50.0)
            if r > 0:
                self.add(Element(e.name + "_R", nodes, "R", r))
            return
        self.add(el)

    # -- 装配下标 ---------------------------------------------------------- #
    def _index(self) -> None:
        self.branches.clear()
        k = 0
        for e in self.elements:
            if e.kind in ("V", "L", "E"):
                self.branches[e.name] = self.n + k
                k += 1

    def _bi(self, name: str) -> int:
        return self.branches[name]


def _kind_of(master: str, models: dict | None = None) -> str | None:
    m = master.lower()
    if models and m in models:
        return _kind_of(str(models[m].get("_master", "")), None)
    if m in ("resistor", "res", "r"):
        return "R"
    if m in ("capacitor", "cap", "c"):
        return "C"
    if m in ("inductor", "ind", "l"):
        return "L"
    if m in ("vsource", "vdc", "vsine", "v", "vsource"):
        return "V"
    if m in ("isource", "idc", "i"):
        return "I"
    if m in ("vcvs", "e"):
        return "E"
    if m in ("vccs", "g"):
        return "G"
    if m in ("diode", "d"):
        return "D"
    if m in ("mos1", "nmos", "pmos", "nmos4", "pmos4"):
        return "M"
    if m in ("port",):
        return "port"
    if m.startswith(("nmos", "pmos", "nch", "pch", "mos", "nmos4", "pmos4")):
        return "M"
    return None


_EXPR_OK = set("0123456789.+-*/() eE")


def _eval_param(text: str, table: dict[str, float]) -> float | None:
    """网表参数求值：名字查表，纯算式用受限的递归下降（不用 eval）。"""
    if text is None:
        return None
    s = str(text).strip().strip('"')
    v = num(s)
    if v is not None:
        return v
    if s in table:
        return float(table[s])
    expr = s
    for name, val in sorted(table.items(), key=lambda kv: -len(kv[0])):
        expr = expr.replace(name, repr(float(val)))
    expr = expr.strip()
    if not expr or any(ch not in _EXPR_OK for ch in expr):
        return None
    try:
        node = compile(expr, "<param>", "eval")
    except SyntaxError:
        return None
    return float(eval(node, {"__builtins__": {}}, {}))     # 只含数字与运算符


def _subst(value: str, bindings: dict[str, str]) -> str:
    """子电路参数替换：把 subckt 形参换成实参。"""
    if value in bindings:
        return bindings[value]
    out = value
    for k, v in bindings.items():
        out = out.replace(f"{{{k}}}", v)
    return out


# --------------------------------------------------------------------------- #
# 一次求解的上下文
# --------------------------------------------------------------------------- #

class Sys:
    """MNA 矩阵：x = [节点电压..., 支路电流...]"""

    def __init__(self, ckt: Circuit, complex_: bool = False):
        self.ckt = ckt
        ckt._index()
        self.N = ckt.size()
        dt = np.complex128 if complex_ else float
        self.A = np.zeros((self.N, self.N), dtype=dt)
        self.b = np.zeros(self.N, dtype=dt)

    # 节点对地（地=0，不占行列）
    def g(self, i: str, j: str, val: complex) -> None:
        a, b = self.ckt.node(i), self.ckt.node(j)
        if a:
            self.A[a - 1, a - 1] += val
        if b:
            self.A[b - 1, b - 1] += val
        if a and b:
            self.A[a - 1, b - 1] -= val
            self.A[b - 1, a - 1] -= val

    def gi(self, n: str, val: complex) -> None:          # 对地支路
        a = self.ckt.node(n)
        if a:
            self.A[a - 1, a - 1] += val

    def i(self, n: str, val: complex) -> None:           # 注入电流（流入节点）
        a = self.ckt.node(n)
        if a:
            self.b[a - 1] += val

    def stamp_jac(self, nodes: list[str], J: list[list[float]],
                  i0: list[float] | None = None,
                  v0: list[float] | None = None) -> None:
        """非线性器件线性化：J[i][j]=∂I_i/∂V_j，I_i 为**流出**节点 i 的电流。

        伴随形式 A=J、b=J·v0 - I(v0)，缺了 J·v0 这一项牛顿就会发散。
        """
        idx = [self.ckt.node(n) for n in nodes]
        m = len(nodes)
        if i0 is not None:
            v = v0 if v0 is not None else [0.0] * m
            for a, na in enumerate(idx):
                if not na:
                    continue
                grad = sum(J[a][b_] * v[b_] for b_ in range(m))
                self.b[na - 1] += grad - i0[a]
        for a, na in enumerate(idx):
            if not na:
                continue
            for b_, nb in enumerate(idx):
                if nb and J[a][b_]:
                    self.A[na - 1, nb - 1] += J[a][b_]

    def br(self, name: str) -> int:
        return self.ckt._bi(name)          # 0 基下标：节点占 0..n-1，支路从 n 开始


# --------------------------------------------------------------------------- #
# 求解器
# --------------------------------------------------------------------------- #

@dataclass
class DCResult:
    x: np.ndarray
    converged: bool
    iters: int
    ckt: Circuit
    method: str = "newton"

    def v(self, node: str) -> float:
        i = self.ckt.node_index.get(node, 0)
        return 0.0 if i == 0 else float(np.real(self.x[i - 1]))

    def i(self, name: str) -> float:
        return float(np.real(self.x[self.ckt.branches[name]]))

    def nodes(self) -> dict[str, float]:
        return {n: self.v(n) for n in self.ckt.node_names[1:]}


def _mos_current(e: Element, vd: float, vg: float, vs: float, vb: float,
                 ckt: Circuit) -> tuple[float, float, float, float]:
    """level-1 MOS：返回 (Id, gm, gds, gmb)。Id 从漏极流向源极。"""
    md = ckt.models.get(e.model, {})
    pol = str(md.get("type", "")).lower()
    master = str(md.get("_master", "")).lower()
    name = e.model.lower()
    pmos = pol.startswith("p") or "pmos" in master or name.startswith("p") \
        or (name[:3].endswith("p"))
    p = dict(ckt.models.get(e.model, {}))
    p.update(e.params)
    w = p.get("w", 10e-6)
    l = max(p.get("l", 0.1e-6), 1e-12)
    kp = p.get("kp", p.get("u0", 120e-6))
    vth0 = p.get("vth0", p.get("vto", p.get("vt0", 0.45 if not pmos else -0.45)))
    gamma = p.get("gamma", 0.4)
    phi = p.get("phi", 0.7)
    lam = p.get("lambda", 0.05)
    beta = kp * w / l

    if pmos:                     # 转成 n 型等效计算
        vd, vg, vs, vb = -vd, -vg, -vs, -vb
        vth0, gamma, lam = -vth0, gamma, lam
    vgs, vds, vbs = vg - vs, vd - vs, vb - vs
    sb = math.sqrt(max(2 * phi - vbs, 1e-9))
    vth = vth0 + gamma * (sb - math.sqrt(2 * phi))
    vov = vgs - vth
    if vov <= 0:
        return (0.0, 0.0, 0.0, 0.0) if not pmos else (0.0, 0.0, 0.0, 0.0)
    if vds < vov:                                    # 线性区
        idd = beta * (vov * vds - 0.5 * vds * vds) * (1 + lam * vds)
        gm = beta * vds * (1 + lam * vds)
        gds = beta * (vov - vds) * (1 + lam * vds) + beta * (vov * vds - 0.5 * vds ** 2) * lam
    else:                                            # 饱和区
        idd = 0.5 * beta * vov * vov * (1 + lam * vds)
        gm = beta * vov * (1 + lam * vds)
        gds = 0.5 * beta * vov * vov * lam
    gmb = gm * gamma / (2 * sb) if sb > 0 else 0.0
    if pmos:
        return -idd, gm, gds, gmb
    return idd, gm, gds, gmb


def _limit(v_new: float, v_old: float, vcrit: float = 0.5) -> float:
    """pn 结限幅，防止指数爆炸。"""
    if v_new > vcrit and abs(v_new - v_old) > 2 * vcrit:
        return v_old + math.copysign(math.log1p(abs(v_new - v_old)) * vcrit, v_new - v_old)
    return v_new


def dc_solve(ckt: Circuit, x0: np.ndarray | None = None, maxit: int = 200,
             reltol: float = 1e-6, abstol: float = 1e-9) -> DCResult:
    """DC 工作点：gmin stepping -> source stepping -> 直接牛顿。"""
    if x0 is not None:
        res = _newton(ckt, x0, maxit, reltol, abstol)
        if res.converged:
            return res
    x = x0
    for gmin in (1e-3, 1e-4, 1e-5, 1e-6, 1e-8, 1e-10, 1e-12, 0.0):
        res = _newton(ckt, x, maxit, reltol, abstol, gmin=gmin)
        if not res.converged:
            break
        x = res.x                        # 一路走到底，不能停在 gmin 解上
        if gmin == 0.0:
            res.method = "gmin" if x0 is None else "newton"
            return res
    x = _source_stepping(ckt, maxit, reltol, abstol)
    if x is not None:
        res = _newton(ckt, x, maxit, reltol, abstol)
        res.method = "source-step"
        return res
    return _newton(ckt, None, maxit, reltol, abstol)


def _src_scale(ckt: Circuit, s: float) -> list[tuple[Element, float]]:
    saved = []
    for e in ckt.elements:
        if e.kind in ("V", "I") and e.waveform == "dc":
            saved.append((e, e.dc))
            e.dc *= s
    return saved


def _source_stepping(ckt: Circuit, maxit: int, reltol: float,
                     abstol: float) -> np.ndarray | None:
    x = None
    for s in (0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.85, 1.0):
        pairs = _src_scale(ckt, s)
        try:
            res = _newton(ckt, x, maxit, reltol, abstol)
            if res.converged:
                x = res.x
            else:
                return None
        finally:
            for e, v in pairs:
                e.dc = v
    return x


def _newton(ckt: Circuit, x0: np.ndarray | None, maxit: int, reltol: float,
            abstol: float, gmin: float = 0.0) -> DCResult:
    N = ckt.size()
    x = np.zeros(N) if x0 is None else np.array(x0, dtype=float)
    if len(x) != N:
        x = np.zeros(N)
    it = 0
    for it in range(1, maxit + 1):
        sys = Sys(ckt)
        if gmin:
            for n in ckt.node_names[1:]:
                sys.gi(n, gmin)
        _stamp_dc(sys, x)
        try:
            xn = np.linalg.solve(sys.A, sys.b)
        except np.linalg.LinAlgError:
            xn = np.linalg.lstsq(sys.A, sys.b, rcond=None)[0]
        # 电压限幅（前 30 次迭代）
        if it < 30:
            for k in range(min(ckt.n, len(xn))):
                xn[k] = _limit(float(xn[k]), float(x[k]), 0.4)
        dx = np.max(np.abs(xn - x)) if N else 0.0
        scale = np.max(np.abs(xn)) if N else 1.0
        x = xn
        if dx <= reltol * max(scale, 1.0) + abstol:
            res = DCResult(x, True, it, ckt)
            return res
    return DCResult(x, False, it, ckt)


def _vt(ckt: Circuit, n: float = 1.0) -> float:
    T = ckt.temp + 273.15
    return n * 1.380649e-23 * T / 1.602176634e-19


def _stamp_dc(sys: Sys, x: np.ndarray) -> None:
    ckt = sys.ckt
    for e in ckt.elements:
        k = e.kind
        if k == "R":
            if e.value <= 0:
                continue
            sys.g(e.nodes[0], e.nodes[1], 1.0 / e.value)
        elif k == "V":
            bi = sys.br(e.name)
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            if a:
                sys.A[a - 1, bi] += 1
                sys.A[bi, a - 1] += 1
            if b:
                sys.A[b - 1, bi] -= 1
                sys.A[bi, b - 1] -= 1
            sys.b[bi] += e.dc
        elif k == "I":
            sys.i(e.nodes[0], -e.dc)
            sys.i(e.nodes[1], e.dc)
        elif k == "E":                     # VCVS: v+ - v- = gain*(vc+ - vc-)
            bi = sys.br(e.name)
            p, m = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            if p:
                sys.A[p - 1, bi] += 1
                sys.A[bi, p - 1] += 1
            if m:
                sys.A[m - 1, bi] -= 1
                sys.A[bi, m - 1] -= 1
            cp, cm = ckt.node(e.nodes[2]), ckt.node(e.nodes[3])
            if cp:
                sys.A[bi, cp - 1] -= e.value
            if cm:
                sys.A[bi, cm - 1] += e.value
        elif k == "G":                     # VCCS
            cp, cm = ckt.node(e.nodes[2]), ckt.node(e.nodes[3])
            dp, dm = e.nodes[0], e.nodes[1]
            for dn, sign in ((dp, 1.0), (dm, -1.0)):
                di = ckt.node(dn)
                if not di:
                    continue
                if cp:
                    sys.A[di - 1, cp - 1] += sign * e.value
                if cm:
                    sys.A[di - 1, cm - 1] -= sign * e.value
        elif k == "D":
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            vd = (x[a - 1] if a else 0.0) - (x[b - 1] if b else 0.0)
            isat, nvt = e.p("is", 1e-14), _vt(ckt, e.p("n", 1.0))
            idd = isat * (math.exp(min(vd / nvt, 80)) - 1)
            gd = isat / nvt * math.exp(min(vd / nvt, 80))
            sys.stamp_jac(e.nodes[:2], [[gd, -gd], [-gd, gd]], [idd, -idd],
                          [vd, 0.0])
        elif k == "M":
            d, g, s, bb = (e.nodes + ["0"] * 4)[:4]
            vd = x[ckt.node(d) - 1] if ckt.node(d) else 0.0
            vg = x[ckt.node(g) - 1] if ckt.node(g) else 0.0
            vs = x[ckt.node(s) - 1] if ckt.node(s) else 0.0
            vb = x[ckt.node(bb) - 1] if ckt.node(bb) else 0.0
            idd, gm, gds, gmb = _mos_current(e, vd, vg, vs, vb, ckt)
            gsum = gm + gds + gmb
            J = [[gds, gm, -gsum, gmb],
                 [0.0, 0.0, 0.0, 0.0],
                 [-gds, -gm, gsum, -gmb],
                 [0.0, 0.0, 0.0, 0.0]]
            sys.stamp_jac([d, g, s, bb], J, [idd, 0.0, -idd, 0.0], [vd, vg, vs, vb])
        elif k == "L":
            bi = sys.br(e.name)
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            if a:
                sys.A[a - 1, bi] += 1
                sys.A[bi, a - 1] += 1
            if b:
                sys.A[b - 1, bi] -= 1
                sys.A[bi, b - 1] -= 1
            # DC 短路：v = 0


# --------------------------------------------------------------------------- #
# AC / 噪声
# --------------------------------------------------------------------------- #

def _stamp_ac(sys: Sys, omega: float, xop: np.ndarray | None) -> None:
    ckt = sys.ckt
    for e in ckt.elements:
        k = e.kind
        if k == "R":
            if e.value > 0:
                sys.g(e.nodes[0], e.nodes[1], 1.0 / e.value)
        elif k == "C":
            sys.g(e.nodes[0], e.nodes[1], 1j * omega * e.value)
        elif k == "L":
            bi = sys.br(e.name)
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            if a:
                sys.A[a - 1, bi] += 1
                sys.A[bi, a - 1] += 1
            if b:
                sys.A[b - 1, bi] -= 1
                sys.A[bi, b - 1] -= 1
            sys.A[bi, bi] -= 1j * omega * e.value
        elif k == "V":
            bi = sys.br(e.name)
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            if a:
                sys.A[a - 1, bi] += 1
                sys.A[bi, a - 1] += 1
            if b:
                sys.A[b - 1, bi] -= 1
                sys.A[bi, b - 1] -= 1
            sys.b[bi] += e.ac_mag * np.exp(1j * math.radians(e.ac_phase))
        elif k == "I":
            val = e.ac_mag * np.exp(1j * math.radians(e.ac_phase))
            sys.i(e.nodes[0], -val)
            sys.i(e.nodes[1], val)
        elif k == "E":
            bi = sys.br(e.name)
            p, m = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            if p:
                sys.A[p - 1, bi] += 1
                sys.A[bi, p - 1] += 1
            if m:
                sys.A[m - 1, bi] -= 1
                sys.A[bi, m - 1] -= 1
            cp, cm = ckt.node(e.nodes[2]), ckt.node(e.nodes[3])
            if cp:
                sys.A[bi, cp - 1] -= e.value
            if cm:
                sys.A[bi, cm - 1] += e.value
        elif k == "G":
            cp, cm = ckt.node(e.nodes[2]), ckt.node(e.nodes[3])
            for dn, sign in ((e.nodes[0], 1.0), (e.nodes[1], -1.0)):
                di = ckt.node(dn)
                if not di:
                    continue
                if cp:
                    sys.A[di - 1, cp - 1] += sign * e.value
                if cm:
                    sys.A[di - 1, cm - 1] -= sign * e.value
        elif k in ("M", "D"):
            if xop is None:
                continue
            _stamp_small_signal(sys, e, xop, omega)


def _stamp_small_signal(sys: Sys, e: Element, x: np.ndarray, omega: float) -> None:
    ckt = sys.ckt
    if e.kind == "D":
        a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
        vd = (x[a - 1] if a else 0.0) - (x[b - 1] if b else 0.0)
        nvt = _vt(ckt, e.p("n", 1.0))
        gd = e.p("is", 1e-14) / nvt * math.exp(min(vd / nvt, 80))
        sys.g(e.nodes[0], e.nodes[1], gd)
        return
    d, g, s, bb = (e.nodes + ["0"] * 4)[:4]
    vd = x[ckt.node(d) - 1] if ckt.node(d) else 0.0
    vg = x[ckt.node(g) - 1] if ckt.node(g) else 0.0
    vs = x[ckt.node(s) - 1] if ckt.node(s) else 0.0
    vb = x[ckt.node(bb) - 1] if ckt.node(bb) else 0.0
    _, gm, gds, gmb = _mos_current(e, vd, vg, vs, vb, ckt)
    gsum = gm + gds + gmb
    J = [[gds, gm, -gsum, gmb],
         [0.0, 0.0, 0.0, 0.0],
         [-gds, -gm, gsum, -gmb],
         [0.0, 0.0, 0.0, 0.0]]
    sys.stamp_jac([d, g, s, bb], J)
    # 本征电容（常数模型）
    cgs, cgd = e.p("cgs", 0.0), e.p("cgd", 0.0)
    if cgs:
        sys.g(e.nodes[1], e.nodes[2], 1j * omega * cgs)
    if cgd:
        sys.g(e.nodes[1], e.nodes[0], 1j * omega * cgd)


def ac_solve(ckt: Circuit, freqs: Iterable[float],
             xop: np.ndarray | None = None) -> dict[str, np.ndarray]:
    if xop is None:
        xop = dc_solve(ckt).x
    freqs = np.asarray(list(freqs), dtype=float)
    out: dict[str, np.ndarray] = {n: np.zeros(len(freqs), dtype=complex)
                                  for n in ckt.node_names[1:]}
    for k, f in enumerate(freqs):
        sys = Sys(ckt, complex_=True)
        _stamp_ac(sys, 2 * math.pi * f, xop)
        try:
            x = np.linalg.solve(sys.A, sys.b)
        except np.linalg.LinAlgError:
            x = np.linalg.lstsq(sys.A, sys.b, rcond=None)[0]
        for n, idx in ckt.node_index.items():
            if idx:
                out[n][k] = x[idx - 1]
    return out


# --------------------------------------------------------------------------- #
# 瞬态
# --------------------------------------------------------------------------- #

def tran_solve(ckt: Circuit, tstop: float, tstep: float | None = None,
               tstart: float = 0.0, maxstep: float | None = None,
               method: str = "trap", uic: np.ndarray | None = None,
               reltol: float = 1e-3, abstol: float = 1e-6) -> dict[str, Any]:
    """瞬态：梯形/后向欧拉 + 预测-校正式 LTE 步长控制。"""
    if tstep is None:
        tstep = tstop / 1000
    h = float(tstep)
    hmax = float(maxstep or h)
    x = dc_solve(ckt).x if uic is None else np.array(uic, dtype=float)
    xprev2 = x.copy()
    times = [0.0]
    hist = {n: [x[i - 1] if i else 0.0] for n, i in ckt.node_index.items() if i}
    xprev = x.copy()
    t = 0.0
    guard = 0
    while t < tstop - 1e-18:
        guard += 1
        if guard > 5_000_000:
            raise SpiceError("瞬态步数过多")
        step = min(h, hmax, tstop - t)
        snap = [(e, e.prev_i, e.prev_v) for e in ckt.elements
                if e.kind in ("C", "L")]
        xn = _tran_step(ckt, xprev, step, method, t + step)   # 源在步末求值
        # 预测-校正误差估计（梯形用 /3，后向欧拉用 /1）
        pred = 2 * xprev - xprev2
        scale = max(float(np.max(np.abs(xn))) if len(xn) else 0.0, 1.0)
        err = float(np.max(np.abs(xn - pred))) / (3.0 if method == "trap" else 1.0)
        if err > reltol * scale + abstol and step > 1e-16:
            for e, pi, pv in snap:                 # 回滚状态再缩步
                e.prev_i, e.prev_v = pi, pv
            h = max(step / 2, 1e-16)
            continue
        xprev2 = xprev
        xprev = xn
        t += step
        times.append(t)
        for n, i in ckt.node_index.items():
            if i:
                hist[n].append(xn[i - 1])
        if err < 0.1 * (reltol * scale + abstol):
            h = min(h * 1.5, hmax)
    return {"t": np.asarray(times), "nodes": {k: np.asarray(v) for k, v in hist.items()},
            "x": xprev}


def _tran_step(ckt: Circuit, xprev: np.ndarray, h: float, method: str, t: float) -> np.ndarray:
    """一步积分：后向欧拉 或 梯形（含 C/L 的伴随模型与状态更新）。"""
    trap = method == "trap"
    x = xprev.copy()
    for _ in range(60):
        sys = Sys(ckt)
        for e in ckt.elements:
            if e.kind == "C":
                a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
                vprev = (xprev[a - 1] if a else 0.0) - (xprev[b - 1] if b else 0.0)
                if trap:
                    geq = 2 * e.value / h
                    ieq = geq * vprev + e.prev_i
                else:
                    geq = e.value / h
                    ieq = geq * vprev
                sys.g(e.nodes[0], e.nodes[1], geq)
                sys.i(e.nodes[0], ieq)
                sys.i(e.nodes[1], -ieq)
            elif e.kind == "L":
                bi = sys.br(e.name)
                a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
                if a:
                    sys.A[a - 1, bi] += 1
                    sys.A[bi, a - 1] += 1
                if b:
                    sys.A[b - 1, bi] -= 1
                    sys.A[bi, b - 1] -= 1
                r = (2 * e.value / h) if trap else (e.value / h)
                sys.A[bi, bi] -= r
                rhs = r * e.prev_i + (e.prev_v if trap else 0.0)
                sys.b[bi] -= rhs
        _stamp_dc(sys, x)
        for e in ckt.elements:
            if e.kind in ("V", "I") and e.waveform in ("sine", "pulse", "pwl"):
                if e.waveform == "sine":
                    val = e.dc + e.ac_mag * math.sin(2 * math.pi * e.p("freq", 1e9) * t)
                elif e.waveform == "pulse":
                    val = _pulse(e, t)
                else:
                    val = _pwl(e, t)
                if e.kind == "V":
                    sys.b[sys.br(e.name)] += val - e.dc
                else:
                    sys.i(e.nodes[0], -(val - e.dc))
                    sys.i(e.nodes[1], val - e.dc)
        try:
            xn = np.linalg.solve(sys.A, sys.b)
        except np.linalg.LinAlgError:
            xn = np.linalg.lstsq(sys.A, sys.b, rcond=None)[0]
        if np.max(np.abs(xn - x)) < 1e-10:
            x = xn
            break
        x = xn
    # 更新储能元件状态
    for e in ckt.elements:
        if e.kind == "C":
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            vn = (x[a - 1] if a else 0.0) - (x[b - 1] if b else 0.0)
            vprev = (xprev[a - 1] if a else 0.0) - (xprev[b - 1] if b else 0.0)
            geq = (2 * e.value / h) if trap else (e.value / h)
            e.prev_i = geq * (vn - vprev) - (e.prev_i if trap else 0.0)
            e.prev_v = vn
        elif e.kind == "L":
            bi = ckt.branches[e.name]
            a, b = ckt.node(e.nodes[0]), ckt.node(e.nodes[1])
            vn = (x[a - 1] if a else 0.0) - (x[b - 1] if b else 0.0)
            e.prev_i = x[bi]
            e.prev_v = vn
    return x


def _pulse(e: Element, t: float) -> float:
    v1, v2 = e.p("v1", 0.0), e.p("v2", 1.0)
    td, tr = e.p("delay", 0.0), max(e.p("rise", 1e-12), 1e-15)
    tf = max(e.p("fall", tr), 1e-15)
    pw = e.p("width", 1e-9)
    per = e.p("period", 0.0)
    tt = t - td
    if tt < 0:
        return v1
    if per > 0:
        tt = math.fmod(tt, per)
    if tt < tr:
        return v1 + (v2 - v1) * tt / tr
    if tt < tr + pw:
        return v2
    if tt < tr + pw + tf:
        return v2 + (v1 - v2) * (tt - tr - pw) / tf
    return v1


def _pwl(e: Element, t: float) -> float:
    pts = e.params.get("_pwl")
    if not pts:
        return e.dc
    for (t0, v0), (t1, v1) in zip(pts[:-1], pts[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1e-18)
    return pts[-1][1]


# --------------------------------------------------------------------------- #
# 噪声
# --------------------------------------------------------------------------- #

def noise_solve(ckt: Circuit, freqs: Iterable[float], out_node: str,
                temp_c: float | None = None) -> dict[str, np.ndarray]:
    """输出噪声谱密度 V²/Hz：伴随法，逐个噪声源求到输出的传递。"""
    T = (temp_c if temp_c is not None else ckt.temp) + 273.15
    xop = dc_solve(ckt).x
    freqs = np.asarray(list(freqs), dtype=float)
    total = np.zeros(len(freqs))
    per = {}
    for k, f in enumerate(freqs):
        w = 2 * math.pi * f
        sys = Sys(ckt, complex_=True)
        _stamp_ac(sys, w, xop)
        # 伴随解：A^T y = e_out
        outi = ckt.node(out_node)
        rhs = np.zeros(sys.N, dtype=complex)
        if outi:
            rhs[outi - 1] = 1.0
        try:
            y = np.linalg.solve(sys.A.conj().T, rhs)
        except np.linalg.LinAlgError:
            y = np.linalg.lstsq(sys.A.conj().T, rhs, rcond=None)[0]
        acc = 0.0
        for e in ckt.elements:
            psd = _noise_psd(e, f, T, ckt)
            if psd <= 0:
                continue
            a = ckt.node(e.nodes[0])
            b = ckt.node(e.nodes[1]) if len(e.nodes) > 1 else 0
            h = (y[a - 1] if a else 0.0) - (y[b - 1] if b else 0.0)
            contrib = abs(h) ** 2 * psd
            acc += contrib
            per[e.name] = per.get(e.name, np.zeros(len(freqs)))
            per[e.name][k] += contrib
        total[k] = acc
    out = {"total": total}
    out.update(per)
    return out


def _noise_psd(e: Element, f: float, T: float, ckt: Circuit) -> float:
    if e.kind == "R" and e.value > 0:
        return 4 * KB * T / e.value
    if e.kind == "M":
        p = dict(ckt.models.get(e.model, {}))
        p.update(e.params)
        gm = p.get("gm", 0.0)
        if gm <= 0:
            return 0.0
        kf = p.get("kf", 0.0)
        return 8.0 / 3.0 * KB * T * gm + kf * gm * gm / max(f, 1.0)
    return 0.0


# --------------------------------------------------------------------------- #
# 与 vlight 结果结构对接
# --------------------------------------------------------------------------- #

class Engine:
    """把 Testbench 交给自研引擎跑，返回和 spectre 一样的 Results。"""

    def __init__(self, design=None, temp: float = 27.0):
        self.design = design
        self.temp = temp

    def run(self, tb, name: str | None = None) -> Results:
        text = tb.scs()
        nl = spicein.parse(text)
        ckt = Circuit.from_netlist(nl, temp=self.temp)
        plots: dict[str, Plot] = {}
        for a in tb.analyses:
            if a.kind == "dc":
                r = dc_solve(ckt)
                if not r.converged:
                    raise SpiceError(f"DC 不收敛（{r.iters} 次迭代）")
                y = {n: np.array([v]) for n, v in r.nodes().items()}
                for en, bi in ckt.branches.items():
                    y[f"{en}:p"] = np.array([float(np.real(r.x[bi - 1]))])
                plots[a.name] = Plot(name=a.name, kind="dc", xname="index",
                                     x=np.zeros(1), y=y)
            elif a.kind == "ac":
                f0 = num(a.args.get("start", 1e6)) or 1e6
                f1 = num(a.args.get("stop", 1e9)) or 1e9
                dec = int(num(a.args.get("dec", 10)) or 10)
                npts = max(int(dec * math.log10(f1 / f0)) + 1, 2)
                freqs = np.logspace(math.log10(f0), math.log10(f1), npts)
                res = ac_solve(ckt, freqs)
                plots[a.name] = Plot(name=a.name, kind="ac", xname="freq", x=freqs,
                                     y=dict(res))
            elif a.kind == "tran":
                tstop = num(a.args.get("stop", 1e-9)) or 1e-9
                step = num(a.args.get("step", tstop / 200)) or tstop / 200
                res = tran_solve(ckt, tstop, step, maxstep=step)
                plots[a.name] = Plot(name=a.name, kind="tran", xname="time",
                                     x=res["t"], y=dict(res["nodes"]))
            else:
                raise SpiceError(f"自研引擎暂不支持 {a.kind} 分析")
        return Results(plots=plots, netlist=text, log="vlight-spice engine")


__all__ = ["Circuit", "Element", "DCResult", "dc_solve", "ac_solve",
           "tran_solve", "noise_solve", "Engine", "SpiceError"]
