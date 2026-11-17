"""驱动层 —— ``OnePoint`` / ``Manual`` / ``DSO`` / ``Optimize`` / ``Global`` / ``Local``。

逐指令结论：

* ``Optimize::Start`` @0x364ca0：``algorithm == "neocircuitGlobal"`` **或** ``"PDE"``
  → ``Global::Init``，否则 ``Local::Init``。
* ``Global::Start`` @0x325fb2 在 ``Sweep`` / ``PDE`` / ``PSA`` 之间选；
  ``Local::LocalOptStart`` @0x33ebf6 按 ``run_bfgs`` / ``run_brent_powell`` /
  ``run_conjugate_gradient`` / ``run_hooke_jeeves`` / ``run_ofat_search`` /
  ``run_parallel_ofat_search`` / ``run_until_no_improvement`` 依次跑。
* ``DSO``（``algs/DSO.cpp``）把外部优化器包成算法：``Goal::addResponse("ade:opt:merit")``
  后把 ``cost()`` 当成唯一响应喂给外部插件。
* ``OnePoint`` 只评估一个点；``Manual`` 由用户给点序列。
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from .lattice import Domain
from .spec import Goal
from .core import Params
from .algs_local import bfgs, conjugate_gradient
from .algs_direct import hooke_jeeves, powell, sweep
from .algs_stoch import pde, psa
from .algs_doe import ofat_search


# --------------------------------------------------------------------------- #
# 最小算法
# --------------------------------------------------------------------------- #

def one_point(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
              params: Params | None = None):
    """``Alchemy::OnePoint``：只评估一个设计点（状态 ``Start → PointWait``）。"""
    ix = domain.clamp_index(start) if start is not None else domain.nominal_index()
    yield [domain.from_index(ix)]


def manual(domain: Domain, goal: Goal, points: Sequence[dict] | None = None,
           params: Params | None = None):
    """``Alchemy::Manual``：由调用方直接给点。"""
    for p in (points or []):
        yield [p]


# --------------------------------------------------------------------------- #
# DSO：把外部优化器包进来
# --------------------------------------------------------------------------- #

def dso(domain: Domain, goal: Goal, plugin=None, params: Params | None = None):
    """``Alchemy::DSO``：外部优化器适配层。

    原实现先把 ``cost()`` 注册成 ``"ade:opt:merit"`` 响应再交给插件；这里同样把
    整个 goal 折成一个 merit 传给插件。``plugin`` 是一个生成器工厂
    ``plugin(domain, merit) -> generator``，按 ask/tell 推进。
    """
    p = params or Params()
    if plugin is None:
        raise ValueError("dso() needs a plugin generator factory")
    merit = _MeritGoal(goal)
    gen = plugin(domain, merit)
    try:
        batch = next(gen)
    except StopIteration:
        return None
    while True:
        costs = yield batch
        try:
            batch = gen.send(costs)
        except StopIteration as exc:
            return exc.value


class _MeritGoal:
    """把多响应折成单响应 ``ade:opt:merit``（DSO::Start 的做法）。"""

    def __init__(self, goal: Goal):
        self.goal = goal

    def cost(self, responses: dict):
        return self.goal.cost(responses)

    @property
    def responses(self):
        return self.goal.responses

    @property
    def specs(self):
        return self.goal.specs


# --------------------------------------------------------------------------- #
# Global / Local / Optimize
# --------------------------------------------------------------------------- #

def global_search(domain: Domain, goal: Goal, params: Params | None = None,
                  start: Sequence[int] | None = None):
    """``Alchemy::Global``：``Sweep`` / ``PDE`` / ``PSA`` 三选一，再交局部精修。

    ADE 的 ``?effort "neocircuitGlobal"`` 走的就是这条：先 DoE 摸底（OFAT/Sweep），
    再全局退火或差分进化跳出局部极小，最后 BFGS 收敛。
    """
    p = params or Params()
    mode = p.str("global_algorithm", "neocircuitGlobal")
    if mode == "sweep":
        yield from sweep(domain, goal, p)
        return None
    if mode == "PDE":
        best = yield from pde(domain, goal, start, p)
    elif mode == "PSA":
        best = yield from psa(domain, goal, start, p)
    else:
        best = yield from ofat_search(domain, goal, start, p)
    # 局部精修
    best = yield from bfgs(domain, goal, best, p)
    return best


def local_search(domain: Domain, goal: Goal, params: Params | None = None,
                 start: Sequence[int] | None = None):
    """``Alchemy::Local``：按 gate 依次跑 BFGS / Powell / CG / HJ / OFAT。"""
    p = params or Params()
    cur = start
    stages = []
    if p.bool("run_bfgs", True):
        stages.append(bfgs)
    if p.bool("run_brent_powell", False):
        stages.append(powell)
    if p.bool("run_conjugate_gradient", False):
        stages.append(conjugate_gradient)
    if p.bool("run_hooke_jeeves", False):
        stages.append(hooke_jeeves)
    if p.bool("run_ofat_search", False):
        stages.append(ofat_search)
    if not stages:
        stages = [hooke_jeeves]
    for stage in stages:
        cur = yield from stage(domain, goal, cur, p)
    return cur


# --------------------------------------------------------------------------- #
# Optimize：按 algorithm 名字分发
# --------------------------------------------------------------------------- #

def optimize(domain: Domain, goal: Goal, params: Params | None = None,
             start: Sequence[int] | None = None):
    """``Alchemy::Optimize``：读 hyperParam ``optimizerName`` 决定 Global / Local。"""
    p = params or Params()
    name = p.str("algorithm", p.str("optimizerName", "neocircuitGlobal"))
    if name in ("neocircuitGlobal", "PDE", "Global Optimization"):
        return (yield from global_search(domain, goal, p, start))
    return (yield from local_search(domain, goal, p, start))
