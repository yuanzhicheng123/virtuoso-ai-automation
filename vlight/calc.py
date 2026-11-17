"""calc —— ADE calculator 的极小等价物。

表达式直接作用在一个 psf.Plot 上（x 轴 + 信号），白名单 AST 求值，不用 eval。

    dB20(V(out))          -> 20log10|V(out)|
    phase(V(out))         -> 度
    mag(I(V1:p))
    deriv(dB20(V(out)), f) / 1e9
    at(V(out), 2.4e9)     -> 取 2.4GHz 那一点
    cross(dB20(V(out)), 0)  -> 增益过 0dB 的频率
"""
from __future__ import annotations

import ast
import math
import re
from typing import Any, Callable

import numpy as np

from .psf import Plot


class CalcError(ValueError):
    pass


def _arr(x: Any) -> np.ndarray:
    if isinstance(x, np.ndarray):
        return x
    if isinstance(x, (list, tuple)):
        return np.asarray(x)
    return np.asarray(x)


def _f(x: Any) -> float:
    a = _arr(x)
    return float(np.real(a.reshape(-1)[-1]) if a.ndim else np.real(a))


def _db20(x):
    return 20 * np.log10(np.abs(_arr(x)) + 1e-300)


def _phase(x):
    return np.degrees(np.angle(_arr(x)))


def _deriv(y, x):
    y = _arr(y)
    x = _arr(x)
    if len(x) < 2:
        return np.zeros_like(y)
    return np.gradient(np.real(y), np.real(x))


def _integ(y, x):
    y = _arr(y)
    x = _arr(x)
    if len(x) < 2:
        return np.zeros_like(y)
    out = np.concatenate([[0], np.cumsum(0.5 * (np.real(y)[1:] + np.real(y)[:-1])
                                         * np.diff(np.real(x)))])
    return out


def _at(y, x_or_f) -> float:
    a = _arr(y)
    if a.size == 1:
        return _f(a)
    return float(np.real(a.reshape(-1)[-1]))


def _cross(y, level: float = 0.0, x=None, rising: bool | None = None) -> float:
    yy = np.real(_arr(y))
    if x is None:
        return float("nan")
    xx = np.real(_arr(x))
    d = yy - level
    idx = np.where(np.sign(d[:-1]) != np.sign(d[1:]))[0]
    if len(idx) == 0:
        return float("nan")
    i = int(idx[0] if rising in (None, True) else idx[-1])
    if d[i] == d[i + 1]:
        return float(xx[i])
    t = d[i] / (d[i] - d[i + 1])
    return float(xx[i] + t * (xx[i + 1] - xx[i]))


def _dbm(x, r: float = 50.0):
    p = np.abs(_arr(x)) ** 2 / (2 * r)
    return 10 * np.log10(p + 1e-300)


FUNCS: dict[str, Callable] = {
    "abs": np.abs, "mag": np.abs, "real": np.real, "imag": np.imag,
    "phase": _phase, "angle": _phase, "dB20": _db20, "db20": _db20,
    "dB10": lambda x: 10 * np.log10(np.abs(_arr(x)) + 1e-300),
    "dbm": _dbm, "sqrt": np.sqrt, "log": np.log, "log10": np.log10,
    "exp": np.exp, "min": np.min, "max": np.max, "mean": np.mean,
    "sum": np.sum, "deriv": _deriv, "integ": _integ, "at": _at, "cross": _cross,
    "value": _f, "rms": lambda x: float(np.sqrt(np.mean(np.abs(_arr(x)) ** 2))),
    "argmax": lambda x: float(np.argmax(np.abs(_arr(x)))),
    "argmin": lambda x: float(np.argmin(np.abs(_arr(x)))),
}

CONSTS = {"pi": math.pi, "e": math.e, "inf": math.inf}


_SIG = re.compile(r"\b(V|I|signal)\(\s*([^(),]+?)\s*\)")


def _quote_signals(expr: str) -> str:
    """V(out) / V(V1:p) -> V("out")，让 in/not/is 这类名字也能解析。"""
    def sub(m):
        name = m.group(2).strip().strip('"')
        return f'{m.group(1)}({name!r})'
    return _SIG.sub(sub, expr)


def evaluate(expr: str, plot: Plot) -> Any:
    """在一个 Plot 上求值。V(name)/I(name) 取信号，f/w 是横轴。"""
    tree = ast.parse(_quote_signals(expr), mode="eval")
    f = np.asarray(plot.x, dtype=float)
    env: dict[str, Any] = {"f": f, "w": 2 * math.pi * f, "n": len(f),
                           **CONSTS}

    def sig(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return plot.signal(node.value)
        name = ast.unparse(node)
        return plot.signal(name.strip())

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise CalcError(f"未知名字 {node.id!r}（信号请写 V(x)/I(x)）")
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            op = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
                  ast.Div: np.divide, ast.Pow: np.power,
                  ast.Mod: np.mod}.get(type(node.op))
            if op is None:
                raise CalcError("不支持的运算符")
            return op(a, b)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return +v
            raise CalcError("不支持的一元运算")
        if isinstance(node, ast.Compare):
            a, b = ev(node.left), ev(node.comparators[0])
            return {ast.Lt: np.less, ast.Gt: np.greater, ast.LtE: np.less_equal,
                    ast.GtE: np.greater_equal, ast.Eq: np.equal,
                    ast.NotEq: np.not_equal}[type(node.ops[0])](a, b)
        if isinstance(node, ast.IfExp):
            return np.where(ev(node.test), ev(node.body), ev(node.orelse))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in ("V", "I", "signal"):
                return sig(node.args[0])
            if fn not in FUNCS:
                raise CalcError(f"不支持的函数 {fn!r}")
            args = [ev(a) for a in node.args]
            if fn in ("deriv", "integ", "cross"):
                if fn == "cross":
                    lvl = args[1] if len(args) > 1 else 0.0
                    return _cross(args[0], lvl, f)
                if len(args) < 2:
                    args.append(f)
            if fn == "at":
                return _at(args[0], f)
            return FUNCS[fn](*args)
        raise CalcError(f"不支持的表达式节点 {type(node).__name__}")

    return ev(tree)


def measure(expr: str, plot: Plot) -> float:
    v = evaluate(expr, plot)
    a = _arr(v)
    return float(np.real(a.reshape(-1)[-1])) if a.size else float(np.real(a))


__all__ = ["evaluate", "measure", "CalcError", "FUNCS"]
