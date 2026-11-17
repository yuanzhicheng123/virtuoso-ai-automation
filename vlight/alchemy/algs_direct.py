"""直接搜索族 —— ``HookeJeeves`` / ``OldHookeJeeves`` / ``NetlibHookeJeeves`` /
``Powell`` / ``ParallelPowell`` / ``Sweep`` / ``NewSweep`` / ``Hammersley``。

逐指令结论：

* ``HookeJeeves`` 与 ``OldHookeJeeves`` **逐指令相同**，只是注册名不同。
  初值 ``steps[i] = trunc(0.1·tunables[i])``（为 0 提升为 1），减半是
  ``trunc(0.5·step)``，所以整数步长 ``1 → 0`` 就终止，整轮只经历 3~4 次减半。
* ``NetlibHookeJeeves`` 是把整数点映射成 ``x_i = design_i + 0.5`` 后调用独立的
  ``hooke()``；而本库里的 ``hooke()`` 只有 65 字节、返回空 vector（**桩**），
  所以这里实现的是经典 Netlib HJ 语义（无改进则减半）。
* ``Powell``：单位方向集 + 嵌套 ``LineMinimize``（整数 Brent，``CGOLD=0.381966``）。
* ``ParallelPowell``：每批开 ``min(剩余方向数, node_count)`` 条线搜索，worker k 负责方向
  ``dim + FORKIDX``。
* ``Sweep``：完整笛卡尔积（每层 ``Fork(tunables[dim])``），**没有超参**。
* ``NewSweep``：混合进制里程表，``values[t]++`` 进位，溢出即终止。
* ``Hammersley``：``Phi(base,k)`` radical inverse；第 0 维线性网格 ``(i+1)/N``，
  第 d 维用第 d 个素数、``k=(i+1)·409``；落格是 ``floor(tunables·u)``。
"""
from __future__ import annotations

import math
from typing import Sequence

from .lattice import Domain
from .spec import COST_EPS, Goal
from .core import Params
from .algs_local import line_minimize


# --------------------------------------------------------------------------- #
# Hooke-Jeeves
# --------------------------------------------------------------------------- #

def hooke_jeeves(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                 params: Params | None = None):
    """``Alchemy::HookeJeeves``（``Move`` 生成集 + 外推）。

    一轮 = 2n 个轴向探测点（一次批量提交）→ 有改进就 ``base = 2·current − base``；
    没改进先回撤 ``base = current``，再整数减半；``steps`` 全零终止。
    """
    p = params or Params()
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    base = list(cur)
    steps = [int(0.1 * t) for t in tun]
    for i in range(n):
        if steps[i] == 0:
            steps[i] = 1
    c = (yield [domain.from_index(cur)])[0]
    best_ix, best_c = list(cur), c
    guard = 0
    while guard < 10000:
        guard += 1
        batch = []
        for m in range(2 * n):
            i, sgn = m >> 1, m & 1
            if steps[i] == 0:
                continue
            trial = list(base)
            if sgn == 1:
                if trial[i] == 0:
                    continue
                trial[i] = max(0, trial[i] - steps[i])
            else:
                if trial[i] + 1 == tun[i]:
                    continue
                trial[i] = min(tun[i] - 1, trial[i] + steps[i])
            batch.append(trial)
        if not batch:
            break
        costs = yield [domain.from_index(ix) for ix in batch]
        for ix, cost in zip(batch, costs):
            if cost < best_c - 1e-15:
                best_c, best_ix = cost, list(ix)
        if best_ix != cur:
            cur = list(best_ix)
            base = [min(max(2 * a - b, 0), tun[i] - 1)
                    for i, (a, b) in enumerate(zip(cur, base))]
        elif base != cur:
            base = list(cur)
        else:
            steps = [int(s * 0.5) for s in steps]
            if all(s == 0 for s in steps):
                break
    return cur


def old_hooke_jeeves(domain: Domain, goal: Goal, **kw):
    """``Alchemy::OldHookeJeeves``：与 ``HookeJeeves`` **逐指令相同**，仅注册名不同。"""
    return hooke_jeeves(domain, goal, **kw)


def netlib_hooke_jeeves(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                        params: Params | None = None, rho: float = 0.5,
                        step0: float = 0.1):
    """``Alchemy::NetlibHookeJeeves``。

    原实现把整数点映射到 ``x = design + 0.5``，再用一个带记忆缓存的目标函数调用
    Netlib 的 ``hooke()``；本库里的 ``hooke()`` 是 65 字节的桩（返回空 vector），
    所以这里实现经典 Netlib 语义：坐标轮询 + 失败就 ``rho`` 减半。
    """
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    steps = [max(1, int(step0 * t)) for t in tun]
    while any(s > 0 for s in steps):
        improved = False
        for i in range(n):
            for sgn in (+1, -1):
                trial = list(cur)
                trial[i] = min(max(trial[i] + sgn * steps[i], 0), tun[i] - 1)
                if trial[i] == cur[i]:
                    continue
                f = (yield [domain.from_index(trial)])[0]
                if f < c - 1e-15:
                    cur, c = trial, f
                    improved = True
        if not improved:
            steps = [int(s * rho) for s in steps]
    return cur


# --------------------------------------------------------------------------- #
# Powell / ParallelPowell
# --------------------------------------------------------------------------- #

def _brent_direction(domain: Domain, cur, d, c_cur, one_sided=False):
    return (yield from line_minimize(domain, cur, d, c_cur, one_sided=one_sided,
                                     min_steps=0))


def powell(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
           params: Params | None = None, maxima: int = 30):
    """``Alchemy::Powell``：方向集 + 整数 Brent 线搜索（标准 Powell 流程）。

    每轮：沿 n 个方向各做一次线搜索，记录**下降最大的方向**；用净位移方向再搜一次，
    然后把它替换掉下降最大的那个方向。这套替换规则是 Powell 原文的写法，
    比"直接替换最老方向"稳得多（后者在 Rosenbrock 山谷里会退化）。
    """
    p = params or Params()
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    dirs = [[1 if i == j else 0 for j in range(n)] for i in range(n)]
    tol = p.float("PowellTolerance", 1e-9)
    for _ in range(maxima):
        cur0, c0 = list(cur), c
        biggest, big_idx = 0.0, 0
        for k in range(n):
            before = c
            cur, c = yield from _brent_direction(domain, cur, dirs[k], c)
            if before - c > biggest:
                biggest, big_idx = before - c, k
        if 2.0 * (c0 - c) <= tol * (abs(c0) + abs(c)) + 1e-12:
            break
        delta = [cur[i] - cur0[i] for i in range(n)]
        if all(v == 0 for v in delta):
            break
        un, cn = yield from _brent_direction(domain, cur, delta, c)
        if cn < c:
            cur, c = un, cn
        dirs[big_idx] = dirs[-1]
        dirs[-1] = delta
    return cur


def parallel_powell(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                    params: Params | None = None, maxima: int = 8):
    """``Alchemy::ParallelPowell``：每批同时推进 ``node_count`` 条线搜索。

    原实现 worker k 负责方向 ``dim + FORKIDX``；这里把同一轮里未完成的方向并行提交，
    每个方向各自维护一条 ``Population``。
    """
    p = params or Params()
    nodes = max(1, p.int("node_count", 4))
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    dirs = [[1 if i == j else 0 for j in range(n)] for i in range(n)]
    for _ in range(maxima):
        cur0, c0 = list(cur), c
        k = 0
        while k < n:
            # 一批同时试 min(nodes, 剩余) 个方向的第 1 段
            width = min(nodes, n - k)
            batch, meta = [], []
            for w in range(width):
                d = dirs[k + w]
                for sign in (+1, -1):
                    ix = [min(max(cur[i] + sign * d[i], 0), tun[i] - 1) for i in range(n)]
                    if ix != cur:
                        batch.append(domain.from_index(ix))
                        meta.append((k + w, sign, ix))
            if not batch:
                k += width
                continue
            costs = yield batch
            j = min(range(len(costs)), key=lambda t: costs[t])
            if costs[j] < c - 1e-15:
                cur, c = list(meta[j][2]), costs[j]
            k += width
        delta = [cur[i] - cur0[i] for i in range(n)]
        if all(v == 0 for v in delta):
            break
        un, cn = yield from _brent_direction(domain, cur, delta, c)
        if cn < c:
            cur, c = un, cn
            dirs = dirs[1:] + [delta]
        if abs(c0 - c) <= 1e-9 * max(1.0, abs(c0)):
            break
    return cur


# --------------------------------------------------------------------------- #
# 采样
# --------------------------------------------------------------------------- #

def _cartesian(sizes):
    idx = [0] * len(sizes)
    if not sizes or any(t <= 0 for t in sizes):
        return
    while True:
        yield list(idx)
        j = len(sizes) - 1
        while j >= 0:
            idx[j] += 1
            if idx[j] < sizes[j]:
                break
            idx[j] = 0
            j -= 1
        if j < 0:
            return


def sweep(domain: Domain, goal: Goal, params: Params | None = None,
          max_points: int | None = None):
    """``Alchemy::Sweep``：完整笛卡尔网格，字典序，最后一维变化最快。"""
    tun = domain.tunables
    total = domain.n_points()
    stride = 1
    if max_points and total > max_points:
        stride = int(math.ceil(total / max_points))
    batch, k = [], 0
    for ix in _cartesian(tun):
        if k % stride:
            k += 1
            continue
        k += 1
        batch.append(domain.from_index(ix))
        if len(batch) >= 32:
            yield batch
            batch = []
    if batch:
        yield batch


def new_sweep(domain: Domain, goal: Goal, params: Params | None = None,
              dims: Sequence[int] | None = None, start: Sequence[int] | None = None):
    """``Alchemy::NewSweep``：混合进制里程表（``values[t]++`` 进位，溢出终止）。"""
    p = params or Params()
    tun = domain.tunables
    dims = list(dims) if dims is not None else p.get("values", None) or list(range(len(tun)))
    dims = [int(d) for d in dims]
    base = domain.clamp_index(start) if start is not None else [0] * len(tun)
    pos = 0
    batch = []
    while True:
        batch.append(domain.from_index(base))
        if len(batch) >= 32:
            yield batch
            batch = []
        while pos < len(dims) and base[dims[pos]] + 1 >= tun[dims[pos]]:
            base[dims[pos]] = 0
            pos += 1
        if pos >= len(dims):
            break
        base[dims[pos]] += 1
        pos = 0
    if batch:
        yield batch


_PRIMES: list[int] = []


def _primes(n: int) -> list[int]:
    if len(_PRIMES) >= n:
        return _PRIMES
    limit = max(6000, n * 20)
    sieve = bytearray([1]) * limit
    sieve[0:2] = b"\x00\x00"
    for i in range(2, int(limit ** 0.5) + 1):
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(sieve[i * i::i]))
    _PRIMES[:] = [i for i, v in enumerate(sieve) if v]
    return _PRIMES


def hammersley(domain: Domain, goal: Goal, params: Params | None = None,
               n_points: int = 64, dim_first: bool = True, seed: int = 0):
    """``Alchemy::Hammersley``：低差异序列。

    ``dim 0 = (i+1)/N``；``dim d>=1`` 用 ``Primes[d-1]``，``k=(i+1)·409``
    （``stride`` 在 ``dimFirst=false`` 时为 1）；落格 ``floor(tunables·u)``。
    """
    n = len(domain)
    tun = domain.tunables
    primes = _primes(max(1, n))
    stride = 409 if dim_first else 1

    def phi(base: int, k: int) -> float:
        f, r = 1.0 / base, 0.0
        while k > 0:
            r += f * (k % base)
            k //= base
            f /= base
        return r

    batch = []
    for i in range(n_points):
        u = []
        if dim_first:
            u.append((i + 1) / n_points)
        for d in range(1 if dim_first else 0, n):
            base = primes[d - 1] if dim_first else primes[d]
            u.append(phi(base, (i + 1) * stride))
        ix = [min(int(math.floor(t * ui)), t - 1) for t, ui in zip(tun, u)]
        batch.append(domain.from_index(ix))
        if len(batch) >= 32:
            yield batch
            batch = []
    if batch:
        yield batch
