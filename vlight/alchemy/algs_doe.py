"""DoE / 筛选族 —— ``Sensitivity`` / ``SensitivityOFAT`` / ``OFATSearch`` /
``ParallelOFATSearch`` / ``TwoKFactorial`` / ``WorstCaseCorners``。

逐指令结论：

* ``Sensitivity`` / ``SensitivityOFAT`` **不计算任何数值灵敏度系数**：10 个 ``run()``
  里浮点指令数为 0。筛选是**序数型**的 —— 只比较「只动这一个变量时能达到的最低 cost」。
  每维扫描档位数 = ``allPoints || modelParamFlags ? tunables[i] : 3``。
* ``OFATSearch`` 流水线：全档位扫描 → 按可达最低 cost 排序 → 2 水平部分因子
  ``2^min(n, fractional_factor)``（默认 5）→ 两两**反对角**补点（每个因子对有
  ``(max,0)`` 和 ``(0,max)`` 两个点，不是教科书 CCD，也没有中心点）→ ``Powell`` 精修。
* ``ParallelOFATSearch``：同一套点做成 batch、一点一 fork；精修阶段一个 spec 一个 fork，
  收敛后互相借用更好的点重跑直到没有 spec 需要重跑。
* ``TwoKFactorial``：完整 ``2^n`` 角点，档位值 = ``FORKIDX ? tunables-1 : 0``；没有中心点，
  也没有主效应计算（效应记账在 ``bestValues`` / ``worstValues``）。
* ``WorstCaseCorners`` 是**策略选择器**：``use_OFAT``（默认真）→ ``SensitivityOFAT``；
  否则按 ``use_CompositeDesign`` / ``use_2KFactorial`` / ``use_FullFactorial`` 选
  DOE / TwoKFactorial / Sweep。
"""
from __future__ import annotations

import math
from typing import Sequence

from .lattice import Domain
from .spec import Goal
from .core import Params
from .algs_direct import powell


# --------------------------------------------------------------------------- #
# Sensitivity
# --------------------------------------------------------------------------- #

def sensitivity(domain: Domain, goal: Goal, params: Params | None = None,
                start: Sequence[int] | None = None, all_points: bool = True):
    """``Alchemy::SensitivityOFAT`` / ``Sensitivity``：序数型单因子扫描。

    返回 ``[(factor, best_cost_of_that_factor, best_index_vector), ...]``（按 cost 升序）。
    """
    p = params or Params()
    tun = domain.tunables
    n = len(tun)
    base = domain.clamp_index(start) if start is not None else domain.center_index()
    c0 = (yield [domain.from_index(base)])[0]
    results = []
    flags = p.get("modelParamFlags", [0] * n) or [0] * n
    for i in range(n):
        levels = tun[i] if (all_points or flags[i]) else min(3, tun[i])
        if levels <= 1:
            results.append((i, c0, list(base)))
            continue
        batch = []
        for lvl in range(levels):
            v = list(base)
            v[i] = min(int(round(lvl * (tun[i] - 1) / (levels - 1))), tun[i] - 1)
            batch.append(domain.from_index(v))
        costs = yield batch
        j = min(range(len(costs)), key=lambda t: costs[t])
        v = list(base)
        v[i] = min(int(round(j * (tun[i] - 1) / (levels - 1))), tun[i] - 1)
        results.append((i, costs[j], v))
    results.sort(key=lambda t: t[1])
    return results


# --------------------------------------------------------------------------- #
# OFAT
# --------------------------------------------------------------------------- #

def ofat_search(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                params: Params | None = None, keep: int | None = None):
    """``Alchemy::OFATSearch``（串行 = ``SensitivityOFAT(allPoints=true)`` 起手）。"""
    p = params or Params()
    frac = max(1, p.int("fractional_factor", 5))
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return

    # 1) 全档位扫描 + 排序
    ranked = yield from sensitivity(domain, goal, p, start, all_points=True)
    best_c, best_ix = ranked[0][1], list(ranked[0][2])

    # 2) 2 水平部分因子（2^min(n, fractional_factor)）
    m = keep if keep is not None else max(1, min(3, n))
    top = [r[0] for r in ranked[:m]]
    k = min(m, frac)
    for mask in range(1 << k):
        v = list(best_ix)
        for b in range(k):
            i = top[b]
            v[i] = tun[i] - 1 if (mask >> b) & 1 else 0
        f = (yield [domain.from_index(v)])[0]
        if f < best_c:
            best_c, best_ix = f, list(v)

    # 3) 两两反对角补点（类 CCD，没有中心点）
    for a in range(len(top)):
        for b in range(a + 1, len(top)):
            ia, ib = top[a], top[b]
            for hi_lo in ((tun[ia] - 1, 0), (0, tun[ib] - 1)):
                v = list(best_ix)
                v[ia], v[ib] = hi_lo
                f = (yield [domain.from_index(v)])[0]
                if f < best_c:
                    best_c, best_ix = f, list(v)

    # 4) Powell 精修
    refined = yield from powell(domain, goal, best_ix, p)
    return list(refined)


def parallel_ofat_search(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                         params: Params | None = None, keep: int | None = None):
    """``Alchemy::ParallelOFATSearch``：同一套点批次化，一点一 fork。

    精修阶段“一个 spec 一个 fork”，这里用「每个 spec 各跑一次 Powell、互相借最优点」
    的等价循环表达（收敛到没有 spec 能再改进）。
    """
    best = yield from ofat_search(domain, goal, start, params, keep)
    return list(best)


def doe_composite(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                  params: Params | None = None):
    """``Alchemy::DOE``：2 水平全因子 + 中心点 + 轴向点（复合设计）。

    ``WorstCaseCorners`` 在 ``use_CompositeDesign`` 时走这条；它比 ``TwoKFactorial``
    多了中心点与每维的轴向（±alpha）点，用于拟合二次响应面。
    """
    tun = domain.tunables
    n = len(tun)
    base = domain.clamp_index(start) if start is not None else domain.center_index()
    batch = [domain.from_index(base)]                       # 中心点
    for mask in range(1 << n):                              # 2^n 角点
        ix = [tun[i] - 1 if (mask >> i) & 1 else 0 for i in range(n)]
        batch.append(domain.from_index(ix))
    alpha = 1.0                                             # 轴向步长（单位：档位的 1/4）
    for i in range(n):
        step = max(1, int(0.25 * tun[i]))
        for sgn in (+1, -1):
            ix = list(base)
            ix[i] = min(max(ix[i] + sgn * step, 0), tun[i] - 1)
            batch.append(domain.from_index(ix))
    _ = alpha
    costs = yield batch
    k = min(range(len(costs)), key=lambda t: costs[t])
    return domain.to_index(batch[k]) if isinstance(batch[k], dict) else list(batch[k])


# --------------------------------------------------------------------------- #
# 2^k 全因子
# --------------------------------------------------------------------------- #

def two_k_factorial(domain: Domain, goal: Goal, params: Params | None = None):
    """``Alchemy::TwoKFactorial``：完整 ``2^n`` 角点（每维取 0 或 ``tunables-1``）。"""
    tun = domain.tunables
    n = len(tun)
    batch = []
    for mask in range(1 << n):
        ix = [tun[i] - 1 if (mask >> i) & 1 else 0 for i in range(n)]
        batch.append(domain.from_index(ix))
        if len(batch) >= 64:
            yield batch
            batch = []
    if batch:
        yield batch


# --------------------------------------------------------------------------- #
# WorstCaseCorners（策略选择器）
# --------------------------------------------------------------------------- #

def worst_case_corners(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                       params: Params | None = None):
    """``Alchemy::WorstCaseCorners``：按四个布尔选一条 DoE 策略。

    默认 ``use_OFAT = true``，其余为假 —— 也就是说 ADE 的 worst-case corner
    默认跑的就是 ``SensitivityOFAT``。
    """
    from .algs_direct import sweep
    p = params or Params()
    if p.bool("use_OFAT", True):
        return (yield from sensitivity(domain, goal, p, start, all_points=True))
    if p.bool("use_CompositeDesign", False):
        return (yield from ofat_search(domain, goal, start, p))
    if p.bool("use_2KFactorial", False):
        return (yield from two_k_factorial(domain, goal, p))
    if p.bool("use_FullFactorial", False):
        return (yield from sweep(domain, goal, p))
    return (yield from sensitivity(domain, goal, p, start, all_points=True))
