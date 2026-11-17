"""opt —— 电路尺寸优化与良率（蒙特卡洛）。

两个真正会被用到的算法：
1. **Nelder-Mead 单纯形**（无导数，适合"跑一次仿真拿一个指标"的黑箱）
2. **蒙特卡洛**：器件失配/工艺偏差 -> 抽样 -> 良率
   - 独立正态抽样，或给协方差矩阵做 Cholesky 分解产生相关抽样
   - 良率 = 满足 spec 的样本比例；同时给出 Cpk 与最坏样本

用法：
    from vlight.opt import nelder_mead, monte_carlo, yield_estimate
    r = nelder_mead(lambda x: (x[0]-1)**2 + (x[1]-2)**2, [0, 0])
    y = monte_carlo({"r1": (1e3, 50), "r2": (2e3, 100)}, n=2000)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np


# --------------------------------------------------------------------------- #
# Nelder-Mead
# --------------------------------------------------------------------------- #

@dataclass
class OptResult:
    x: np.ndarray
    fx: float
    iters: int
    nfev: int
    history: list[float] = field(default_factory=list)


def nelder_mead(f: Callable[[np.ndarray], float], x0, step: float | None = None,
                maxiter: int = 400, tol: float = 1e-10) -> OptResult:
    x0 = np.asarray(x0, dtype=float)
    n = len(x0)
    step = step or 0.05 * max(np.abs(x0).max(), 1.0)
    simplex = [x0]
    for i in range(n):
        v = x0.copy()
        v[i] += step
        simplex.append(v)
    simplex = np.array(simplex)
    fx = np.array([f(v) for v in simplex])
    nfev = n + 1
    hist = [float(fx.min())]
    for it in range(maxiter):
        idx = np.argsort(fx)
        simplex, fx = simplex[idx], fx[idx]
        if np.max(np.abs(simplex[1:] - simplex[0])) < tol:
            break
        centroid = simplex[:-1].mean(axis=0)
        worst = simplex[-1]
        xr = centroid + (centroid - worst)
        fr = f(xr)
        nfev += 1
        if fx[0] <= fr < fx[-2]:
            simplex[-1], fx[-1] = xr, fr
        elif fr < fx[0]:
            xe = centroid + 2 * (centroid - worst)
            fe = f(xe)
            nfev += 1
            if fe < fr:
                simplex[-1], fx[-1] = xe, fe
            else:
                simplex[-1], fx[-1] = xr, fr
        else:
            xc = centroid + 0.5 * (worst - centroid)
            fc = f(xc)
            nfev += 1
            if fc < fx[-1]:
                simplex[-1], fx[-1] = xc, fc
            else:                                     # 收缩
                for i in range(1, n + 1):
                    simplex[i] = simplex[0] + 0.5 * (simplex[i] - simplex[0])
                    fx[i] = f(simplex[i])
                    nfev += 1
        hist.append(float(fx.min()))
    i = int(np.argmin(fx))
    return OptResult(simplex[i], float(fx[i]), it, nfev, hist)


def coordinate_descent(f, x0, steps=None, maxiter: int = 200):
    """更朴素但稳的坐标下降（尺寸这类问题经常比 NM 好）。"""
    x = np.asarray(x0, dtype=float)
    steps = np.asarray(steps if steps is not None else np.abs(x) * 0.1 + 1e-3)
    fx = f(x)
    nfev = 1
    for _ in range(maxiter):
        improved = False
        for i in range(len(x)):
            for sign in (1, -1):
                y = x.copy()
                y[i] += sign * steps[i]
                fy = f(y)
                nfev += 1
                if fy < fx:
                    x, fx, improved = y, fy, True
                    break
            else:
                steps[i] *= 0.5
        if not improved and np.all(steps < 1e-12):
            break
    return OptResult(x, float(fx), 0, nfev)


# --------------------------------------------------------------------------- #
# 蒙特卡洛
# --------------------------------------------------------------------------- #

@dataclass
class MCResult:
    samples: dict[str, np.ndarray]
    values: np.ndarray
    n: int

    def stats(self) -> dict:
        v = self.values
        return {"n": self.n, "mean": float(np.mean(v)), "std": float(np.std(v)),
                "min": float(np.min(v)), "max": float(np.max(v)),
                "p1": float(np.percentile(v, 1)), "p99": float(np.percentile(v, 99))}


def monte_carlo(params: dict[str, tuple[float, float]], evaluate: Callable,
                n: int = 1000, corr: np.ndarray | None = None,
                seed: int = 1) -> MCResult:
    """params: {名字: (标称, sigma)}；corr: 相关系数矩阵（可选）。"""
    rng = np.random.default_rng(seed)
    names = list(params)
    mu = np.array([params[k][0] for k in names], dtype=float)
    sd = np.array([params[k][1] for k in names], dtype=float)
    if corr is None:
        z = rng.standard_normal((n, len(names)))
    else:
        L = np.linalg.cholesky(np.asarray(corr, dtype=float))
        z = rng.standard_normal((n, len(names))) @ L.T
    xs = mu + z * sd
    samples = {k: xs[:, i] for i, k in enumerate(names)}
    values = np.array([float(evaluate({k: xs[i, j] for j, k in enumerate(names)}))
                       for i in range(n)])
    return MCResult(samples, values, n)


def yield_estimate(values: np.ndarray, lower: float | None = None,
                   upper: float | None = None) -> dict:
    ok = np.ones(len(values), dtype=bool)
    if lower is not None:
        ok &= values >= lower
    if upper is not None:
        ok &= values <= upper
    mu, sd = float(np.mean(values)), float(np.std(values))
    spec = []
    if lower is not None:
        spec.append((mu - lower) / sd if sd else np.inf)
    if upper is not None:
        spec.append((upper - mu) / sd if sd else np.inf)
    cpk = min(spec) / 3 if spec and sd else float("inf")
    return {"n": len(values), "pass": int(ok.sum()),
            "yield": float(ok.mean()), "cpk": float(cpk),
            "mean": mu, "std": sd,
            "worst_low": float(values.min()), "worst_high": float(values.max())}


__all__ = ["OptResult", "nelder_mead", "coordinate_descent", "monte_carlo",
           "MCResult", "yield_estimate"]
