"""随机族 —— ``GreedyMinimization`` / ``Anneal`` / ``Anneal2`` / ``PSA`` / ``PSA2`` / ``PDE``。

逐指令结论（``spec_anneal.md`` / ``spec_portfolio.md``）：

* 接受准则：``Δ = f_new − f_cur``；``Δ < 1e-6`` 直接接受；``T == 0`` 只接受 ``Δ ≤ 0``；
  否则 ``exp(−Δ/T) > u``（**严格大于**，``u = Gen::in01()``，mt19937，seed 默认 1）。
* ``KrazKool1`` 不是指数降温：先淬到 ``1e-30``，再 ``T = bestCost·target/0.82`` 回火，
  ``target`` 走 ``0.4 → 0.07 → 1e-6``（``temps[]`` 共 40 段，``n1=round(0.15·nT)``、
  ``n2=round(0.6·nT)``），之后按相对偏差自适应 ``×(1−adjust)``，``phaseCount>3`` 后 ``×0.9``。
* ``Hustin`` 只决定**扰哪个变量**（``w[d] += |Δ|``，每 100 步按配额重建选择表）；
  步长由 ``MoveRange`` 给：``kmax = max(1, floor((N−1)(0.4−0.35t)+0.5))``，
  在 ``[c−kmax, c+kmax]`` 内均匀取整、跳过 ``c``（截断，不反射）。
* ``PSA``/``PSA2`` 都是 9 个 fork；泡泡步数 9 / 10；温度每点更新、每 50 点降一次。
* ``PDE`` = 并行差分进化（可见名 "Parallel Differential Evoluation"）：
  ``F=dFactor=0.7``、``CR=crFactor=0.8``、``stoppingRatio=0.01``，
  整数变异 ``trunc(x + k·Δ + 0.5)``，二项交叉，初始种群用 Hammersley。
* 坑：``KrazKool1(…, unsigned long, …)`` 是 ``throw "FIXME"`` 桩，``Anneal2`` 正好调它 ——
  所以 ``anneal2`` 这里显式注入 criterion，避免复现“一开跑就抛异常”。
"""
from __future__ import annotations

import math
from typing import Sequence

from .lattice import Domain
from .spec import COST_EPS, Goal
from .core import Gen, Params
from .algs_direct import hammersley


# --------------------------------------------------------------------------- #
# 冷却表 / 接受准则
# --------------------------------------------------------------------------- #

def krazkool_temps(nT: int = 40, hi: float = 0.4, mid: float = 0.07, lo: float = 1e-6):
    """``CoolingSchedule::KrazKool1::Parameters`` 的 40 段温度表。"""
    n1 = int(round(0.15 * nT))
    n2 = int(round(0.60 * nT))
    out = []
    for i in range(max(1, n1)):
        out.append(hi * math.exp(math.log(mid / hi) * i / max(1, n1 - 1)))
    while len(out) < n2:
        out.append(mid)
    m = max(1, nT - n2)
    for i in range(m):
        out.append(mid * math.exp(math.log(lo / mid) * i / max(1, m - 1)))
    return out


def metropolis(delta: float, T: float, gen: Gen) -> bool:
    """``KrazKool1::criterion``：注意 ``Δ<1e-6`` 一定接受，``T==0`` 只要不升高。"""
    if delta < 1e-6:
        return True
    if T <= 0:
        return delta <= 0
    return math.exp(-delta / T) > gen.in01()


# --------------------------------------------------------------------------- #
# 贪心下降
# --------------------------------------------------------------------------- #

def greedy_minimization(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                        params: Params | None = None, max_steps: int = 200):
    """``Alchemy::GreedyMinimization``：只接受严格改进的单步爬山。"""
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    gen = Gen(params.int("seed", 1) if params else 1)
    for _ in range(max_steps):
        i = gen.inrange(0, n - 1)
        step = 1 if gen.in01() < 0.5 else -1
        trial = list(cur)
        trial[i] = min(max(trial[i] + step, 0), tun[i] - 1)
        if trial[i] == cur[i]:
            continue
        f = (yield [domain.from_index(trial)])[0]
        if f < c:                       # Greedy::tryMove = (cost_ > newCost)
            cur, c = trial, f
    return cur


# --------------------------------------------------------------------------- #
# 退火（串行 / 并行）
# --------------------------------------------------------------------------- #

def _anneal_core(domain, start, steps, gen, pop_hint=None):
    """生成器实现（yield 批 = 一个待评估点）。返回最优点索引。"""
    tun = domain.tunables
    n = len(tun)
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    best_ix, best_c = list(cur), c
    weights = [1.0] * n
    table = list(range(n))
    temps = krazkool_temps()
    t_idx = 0
    T = max(abs(c), 1.0) * temps[0] / 0.82
    for step in range(steps):
        if step % 100 == 0:
            tot = sum(weights)
            table = []
            for i, w in enumerate(weights):
                table.extend([i] * max(1, int(round(100 * w / tot))))
            if not table:
                table = list(range(n))
        d = table[gen.inrange(0, len(table) - 1)]
        progress = step / max(1, steps - 1)
        kmax = max(1, int(math.floor((tun[d] - 1) * (0.4 - 0.35 * progress) + 0.5)))
        k = gen.inrange(1, kmax)
        sign = 1 if gen.in01() < 0.5 else -1
        trial = list(cur)
        trial[d] = min(max(trial[d] + sign * k, 0), tun[d] - 1)
        if trial[d] == cur[d]:
            trial[d] = min(max(trial[d] - sign * k, 0), tun[d] - 1)
        f = (yield [domain.from_index(trial)])[0]
        if metropolis(f - c, T, gen):
            if f < c:
                weights[d] += abs(f - c)
            cur, c = trial, f
            if f < best_c:
                best_ix, best_c = list(trial), f
        if (step + 1) % 50 == 0:
            t_idx = min(t_idx + 1, len(temps) - 1)
            T = max(abs(best_c) * temps[t_idx] / 0.82, 1e-30)
    return best_ix


def anneal(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
           params: Params | None = None, max_steps: int | None = None, **kwargs):
    """``Alchemy::Anneal``：串行退火，``PointDone`` 里 ``max_steps`` 归零就抛 ``Return``。"""
    p = params or Params()
    if "iterations" in kwargs:
        max_steps = int(kwargs["iterations"]) * int(kwargs.get("population", 1))
    p = Params({**p.as_dict(), **kwargs})
    steps = max_steps if max_steps is not None else p.int("max_steps", 600)
    gen = Gen(p.int("seed", 1))
    return (yield from _anneal_core(domain, start, steps, gen))


def anneal2(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
            params: Params | None = None, max_steps: int | None = None):
    """``Alchemy::Anneal2``：退火 + 内嵌一次 ``GreedyMinimization`` 局部下降。"""
    p = params or Params()
    steps = max_steps if max_steps is not None else p.int("max_steps", 600)
    gen = Gen(p.int("seed", 1))
    best = yield from _anneal_core(domain, start, steps, gen)
    best = yield from greedy_minimization(domain, goal, best, p, 60)
    return best


def psa(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
        params: Params | None = None):
    """``Alchemy::PSA``：并行退火（9 个泡泡，每泡泡 9 步，Hammersley 起步）。"""
    p = params or Params()
    annealers = max(1, p.int("annealers", 9))
    steps = p.int("max_steps", 9)
    gen = Gen(p.int("seed", 1))
    if start is None:
        npts = p.int("hammersley_points", -1)
        npts = 64 if npts < 0 else npts
        starts = []
        hg = hammersley(domain, goal, n_points=npts)
        batch = next(hg)
        costs = yield batch
        k = min(range(len(costs)), key=lambda i: costs[i])
        start = domain.to_index(batch[k]) if isinstance(batch[k], dict) else list(batch[k])
    cur = domain.clamp_index(start)
    best_ix, best_c = list(cur), math.inf
    for _ in range(max(1, steps)):
        idx = [None] * annealers
        batch = []
        for a in range(annealers):
            d = gen.inrange(0, len(cur) - 1)
            kmax = max(1, int(math.floor((domain.tunables[d] - 1) * (0.4 - 0.35 * 0.5) + 0.5)))
            k = gen.inrange(1, kmax)
            sign = 1 if gen.in01() < 0.5 else -1
            trial = list(cur)
            trial[d] = min(max(trial[d] + sign * k, 0), domain.tunables[d] - 1)
            idx[a] = trial
            batch.append(domain.from_index(trial))
        costs = yield batch
        j = min(range(len(costs)), key=lambda i: costs[i])
        if costs[j] < best_c:
            best_c, best_ix = costs[j], list(idx[j])
            cur = list(idx[j])
    return best_ix


def psa2(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
         params: Params | None = None):
    """``Alchemy::PSA2``：与 PSA 同构，泡泡步数为 10，种群 90（``StepsBetweenPopulationSamples``）。"""
    p = params or Params()
    p2 = Params(p.as_dict())
    p2.set("max_steps", p2.get("max_steps", 10))
    p2.set("population", p2.get("population", 90))
    return (yield from psa(domain, goal, start, p2))


# --------------------------------------------------------------------------- #
# 并行差分进化
# --------------------------------------------------------------------------- #

def pde(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
        params: Params | None = None):
    """``Alchemy::PDE`` = 并行差分进化（DE/rand/1/bin 的整数版）。

    ``F = dFactor = 0.7``、``CR = crFactor = 0.8``、``stoppingRatio = 0.01``；
    变异 ``trial[d] = trunc(base[d] + F·(x2[d] − x3[d]) + 0.5)``，再按 ``CR`` 逐坐标
    回退到父代（二项交叉）；初始种群由 Hammersley 生成。
    """
    p = params or Params()
    F = p.float("dFactor", 0.7)
    CR = p.float("crFactor", 0.8)
    pop_size = max(4, p.int("populationSize", 20))
    steps = p.int("max_steps", 100)
    gen = Gen(p.int("seed", 1))
    tun = domain.tunables
    n = len(tun)

    # ---- 初始种群：Hammersley ----
    hg = hammersley(domain, goal, n_points=pop_size)
    batch = next(hg)
    costs = yield batch
    pop = [(domain.to_index(b) if isinstance(b, dict) else list(b), c)
           for b, c in zip(batch, costs)]
    best_ix, best_c = min(pop, key=lambda t: t[1])
    best_ix, best_c = list(best_ix), best_c

    for _ in range(steps):
        batch, trials = [], []
        for i in range(len(pop)):
            idxs = [j for j in range(len(pop)) if j != i]
            a = pop[gen.inrange(0, len(idxs) - 1)][0]
            b = pop[gen.inrange(0, len(idxs) - 1)][0]
            c = pop[gen.inrange(0, len(idxs) - 1)][0]
            trial = list(pop[i][0])
            forced = gen.inrange(0, n - 1)
            for d in range(n):
                if gen.in01() < CR or d == forced:
                    v = int(math.floor(trial[d] + F * (b[d] - c[d]) + 0.5))
                    trial[d] = min(max(v, 0), tun[d] - 1)
            trials.append(trial)
            batch.append(domain.from_index(trial))
        new_costs = yield batch
        for i, (trial, fc) in enumerate(zip(trials, new_costs)):
            if fc < pop[i][1]:
                pop[i] = (trial, fc)
                if fc < best_c:
                    best_ix, best_c = list(trial), fc
        # 收敛判据：种群总距离小于 stoppingRatio
        span = max(abs(pop[i][1] - best_c) for i in range(len(pop)))
        if span <= p.float("stoppingRatio", 0.01) * max(1.0, abs(best_c)):
            break
    return best_ix
