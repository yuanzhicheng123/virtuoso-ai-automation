"""alchemy —— Cadence ADE 电路尺寸优化引擎（内部代号 Alchemy / NeoCircuit）的完整复刻。

这一层只是一张"门面"，把引擎各部分摊平导出；实现分散在：

=====================  ======================================================
模块                    内容
=====================  ======================================================
``lattice``            整数格设计空间（``Param`` / ``Domain`` / ``tunables``）
``spec``               规格与代价函数（``SpecInfo`` / ``Goal`` / 三结点模型）
``core``               引擎骨架（``State``/``Fork``/``Call``/``Population`` /
                       ``DesignPointFactory``/``Params``/``Gen`` / ask-tell 驱动）
``algs_local``         BFGS / ConjugateGradient / QuasiNewton / LineMinimize
``algs_direct``        HookeJeeves(+Old/Netlib) / Powell / ParallelPowell /
                       Sweep / NewSweep / Hammersley
``algs_stoch``         GreedyMinimization / Anneal / Anneal2 / PSA / PSA2 / PDE
``algs_doe``           Sensitivity / OFATSearch / ParallelOFATSearch /
                       TwoKFactorial / WorstCaseCorners
``drivers``            OnePoint / Manual / DSO / Optimize / Global / Local
``registry``           ``Factory``：27 个注册名 + 31 个别名
``proto``              ``evaluation.proto`` 的手写编解码
``server``             参考服务端（ask/tell 会话 + 可选 HTTP 端点）
=====================  ======================================================

快速开始::

    from vlight import alchemy as al

    dom  = al.Domain([al.Param("W", 1e-6, 20e-6, nominal=4e-6),
                      al.Param("L", 0.18e-6, 2e-6, nominal=0.5e-6)])
    goal = al.Goal([al.Response("gain", objective="max", target=60.0),
                    al.Response("pm", constraint=("gt", 60.0))])

    res = al.run(al.optimize(dom, goal), dom, goal, evaluate, budget=300)

    # 或者按名字取任意一个算法：
    gen = al.create("Powell", dom, goal)
"""
from __future__ import annotations

from typing import Sequence

# --- 设计空间 ---------------------------------------------------------------- #
from .lattice import Domain, Param

# --- 规格与代价 -------------------------------------------------------------- #
from .spec import (COST_BEYOND_SLOPE, COST_EPS, DISABLED_SPEC_COST, Goal, Response,
                   SpecInfo, SpecType, UNMEASURABLE_COST)

# --- 引擎骨架 ---------------------------------------------------------------- #
from .core import (Cache, DesignPoint, DesignPointFactory, Done, Gen, Join, OptResult,
                   Params, Population, Return, ShortCircuit, run)

# --- 算法 -------------------------------------------------------------------- #
from .algs_local import (CGOLD, bfgs, conjugate_gradient, line_minimize,
                         line_search_algo, quasi_newton)
from .algs_direct import (hammersley, hooke_jeeves, netlib_hooke_jeeves, new_sweep,
                          old_hooke_jeeves, parallel_powell, powell, sweep)
from .algs_stoch import (anneal, anneal2, greedy_minimization, krazkool_temps,
                         metropolis, pde, psa, psa2)
from .algs_doe import (doe_composite, ofat_search, parallel_ofat_search,
                       sensitivity, two_k_factorial, worst_case_corners)
from .drivers import dso, global_search, local_search, manual, one_point, optimize

# --- 注册表 / 协议 / 服务 ----------------------------------------------------- #
from . import extracted, proto, registry, server
from .registry import ALIASES, REGISTRY, create, describe, names, resolve


# --------------------------------------------------------------------------- #
# 兼容入口（与早期单文件版本保持同名）
# --------------------------------------------------------------------------- #

def neocircuit_global(domain: Domain, goal: Goal, batch_size: int = 1,
                      rng=None, keep: int | None = None, population: int = 9,
                      iterations: int = 25, polish: bool = True):
    """ADE「Global Optimization」（``?effort "neocircuitGlobal"``）。

    顺序：OFAT 筛选（全档位扫描 → 2 水平部分因子 → 两两反对角 → Powell）
    → 并行退火 → BFGS 精修。``population`` / ``iterations`` 是退火档位。
    """
    p = Params({"algorithm": "neocircuitGlobal", "population": population,
                "max_steps": max(50, iterations * 20)})
    start = yield from ofat_search(domain, goal, None, p, keep)
    start = yield from anneal(domain, goal, start, p)
    if polish:
        start = yield from bfgs(domain, goal, start, p)
    return start


def neocircuit_local(domain: Domain, goal: Goal, batch_size: int = 1,
                     rng=None, effort: str = "coarse"):
    """ADE「Local Optimization」：``?effort fine`` 只是收紧差分与阈值。"""
    p = Params({"effort": effort})
    return (yield from bfgs(domain, goal, None, p))


def minimize(f, x0: Sequence[float], span: float = 10.0,
             maxiter: int = 4000) -> tuple[list[float], float]:
    """在黑箱函数上跑 Hooke-Jeeves（便捷入口）。"""
    n = len(x0)
    dom = Domain([Param(f"x{i}", x0[i] - span, x0[i] + span, nominal=x0[i],
                        n_values=401) for i in range(n)])
    goal = Goal([Response("f", objective="min")])

    def evaluate(point: dict) -> dict:
        return {"f": f([point[f"x{i}"] for i in range(n)])}

    res = run(hooke_jeeves(dom, goal), dom, goal, evaluate, budget=maxiter)
    return dom.vector(res.best.point), res.best.responses["f"]


__all__ = [
    "Domain", "Param",
    "Goal", "Response", "SpecInfo", "SpecType",
    "DISABLED_SPEC_COST", "COST_BEYOND_SLOPE", "UNMEASURABLE_COST", "COST_EPS",
    "Params", "Population", "DesignPoint", "DesignPointFactory", "Cache",
    "Gen", "ShortCircuit", "Done", "Return", "Join", "OptResult", "run",
    "CGOLD", "line_minimize", "line_search_algo", "bfgs", "conjugate_gradient", "quasi_newton",
    "hooke_jeeves", "old_hooke_jeeves", "netlib_hooke_jeeves", "powell",
    "parallel_powell", "sweep", "new_sweep", "hammersley",
    "greedy_minimization", "anneal", "anneal2", "psa", "psa2", "pde",
    "krazkool_temps", "metropolis",
    "sensitivity", "ofat_search", "parallel_ofat_search", "two_k_factorial",
    "worst_case_corners", "doe_composite",
    "one_point", "manual", "dso", "optimize", "global_search", "local_search",
    "neocircuit_global", "neocircuit_local", "minimize",
    "registry", "proto", "server", "extracted", "create", "names", "describe", "resolve",
    "REGISTRY", "ALIASES",
]
