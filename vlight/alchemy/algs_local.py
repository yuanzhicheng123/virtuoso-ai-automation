"""局部/梯度族 —— ``Alchemy::LineMinimize`` / ``BFGS`` / ``ConjugateGradient`` / ``QuasiNewton``。

共同点（全部从 ``-O0`` 汇编里恢复）：

* 设计空间是**整数格**，梯度是**整数差分**，没有 ``h(·)`` 公式。
* 一次 ``ComputeGradient`` 提交 **2·dim** 个点：先 n 个 ``+1`` 侧、再 n 个 ``-1`` 侧；
  越界的那一侧 ``reset()`` 掉（不提交）。
* 线搜索：候选点 ``p[j] = floor(cur[j] + step·d[j] + 0.5)``；``directional`` 为真只走
  正方向；点数 < ``minimum_steps_for_brent``（默认 30）时**全扫**，否则 Brent
  （``xwv`` 只留 3 项，``CGOLD = 0.381966``，十进制字面量）。
"""
from __future__ import annotations

import math
from typing import Sequence

from .lattice import Domain
from .spec import COST_EPS, Goal
from .core import Gen, Params

# 线搜索常数（.rodata 0x568a20 / 0x5691f0）
CGOLD = 0.381966
MIN_STEPS_FOR_BRENT = 30
FAIL_COUNT_LIMIT = 0


# --------------------------------------------------------------------------- #
# 线搜索
# --------------------------------------------------------------------------- #

def line_candidates(domain: Domain, cur: Sequence[int], d: Sequence[float],
                    one_sided: bool = True, limit: int = 256):
    """枚举可行步长区间 ``[lo, hi]``（整数步）。"""
    tun = domain.tunables
    n = len(tun)

    def at(t: int):
        ix = [int(math.floor(cur[i] + t * d[i] + 0.5)) for i in range(n)]
        if any(x < 0 or x >= tun[i] for i, x in enumerate(ix)):
            return None
        return ix

    hi = 0
    while hi < limit and at(hi + 1) is not None:
        hi += 1
    lo = 0
    if not one_sided:
        while lo > -limit and at(lo - 1) is not None:
            lo -= 1
    return lo, hi, at


def line_minimize(domain: Domain, cur, d, c_cur, one_sided: bool = True,
                  min_steps: int = 0, max_evals: int = 40):
    """``Alchemy::LineMinimize``。

    点数不够 ``minimum_steps_for_brent`` 时全扫（``Min_Exhaustive``）；
    否则先用「倍数扩张」把极小值括起来，再在括号内做黄金分割（``Min_Brent``）。

    扩张这一步是必要的：直接在整个可行区间上取两个黄金分割点，对很长的线
    （几十个档位）会两个点都落在很差的远端，导致“线搜索永远失败”。
    """
    lo, hi, at = line_candidates(domain, cur, d, one_sided)
    if hi == lo:
        return list(cur), c_cur
    if (hi - lo + 1) <= max(2, min_steps):
        ts = list(range(lo, hi + 1))
        costs = yield [domain.from_index(at(t)) for t in ts]
        k = min(range(len(costs)), key=lambda i: costs[i])
        return (at(ts[k]) or list(cur)), costs[k]

    def ev(t):
        return (yield [domain.from_index(at(t))])[0]

    # ---- 1) 从 0 出发做倍数扩张，找括号 ----
    best_t, best_f = 0, c_cur
    if hi >= 1:
        f1 = yield from ev(1)
        if f1 < c_cur:
            best_t, best_f = 1, f1
            a, fa = 0, c_cur
            b, fb = 1, f1
            step = 1
            while True:
                step *= 2
                nxt = min(b + step, hi)
                if nxt == b:
                    break
                fn = yield from ev(nxt)
                if fn < fb:
                    a, fa, b, fb = b, fb, nxt, fn
                    if fn < best_f:
                        best_t, best_f = nxt, fn
                else:
                    a, fa, b, fb = b, fb, nxt, fn      # 括号 [a, b] 含极小
                    break
            lo_b, hi_b = a, b
        else:
            lo_b = hi_b = None
    else:
        lo_b = hi_b = None

    if lo_b is None and not one_sided and lo <= -1:
        f1 = yield from ev(-1)
        if f1 < c_cur:
            best_t, best_f = -1, f1
            a, b, step = -1, 0, 1
            while True:
                step *= 2
                nxt = max(a - step, lo)
                if nxt == a:
                    break
                fn = yield from ev(nxt)
                if fn < best_f:
                    best_t, best_f = nxt, fn
                    b = a
                    a = nxt
                else:
                    break
            lo_b, hi_b = a, b
        else:
            lo_b = hi_b = None

    if lo_b is None:
        return (at(best_t) or list(cur)), best_f

    # ---- 2) 括号内黄金分割 ----
    a, b = lo_b, hi_b
    c1 = a + int(round(CGOLD * (b - a)))
    c2 = b - int(round(CGOLD * (b - a)))
    if c1 >= c2:
        c1, c2 = a + 1, b - 1
    f1 = yield from ev(c1)
    f2 = yield from ev(c2)
    for t, f in ((c1, f1), (c2, f2)):
        if f < best_f:
            best_t, best_f = t, f
    evals = 2
    while b - a > 1 and evals < max_evals:
        if f1 < f2:
            b, c2, f2 = c2, c1, f1
            c1 = a + int(round(CGOLD * (b - a)))
            if c1 >= c2:
                c1 = c2 - 1
            if c1 <= a:
                break
            f1 = yield from ev(c1)
            evals += 1
            if f1 < best_f:
                best_t, best_f = c1, f1
        else:
            a, c1, f1 = c1, c2, f2
            c2 = b - int(round(CGOLD * (b - a)))
            if c2 <= c1:
                c2 = c1 + 1
            if c2 >= b:
                break
            f2 = yield from ev(c2)
            evals += 1
            if f2 < best_f:
                best_t, best_f = c2, f2
    return (at(best_t) or list(cur)), best_f


# --------------------------------------------------------------------------- #
# 梯度
# --------------------------------------------------------------------------- #

def _gradient_points(domain: Domain, cur):
    tun = domain.tunables
    n = len(tun)
    pts, meta = [], []
    for sign in (+1, -1):
        for i in range(n):
            j = cur[i] + sign
            if 0 <= j < tun[i]:
                v = list(cur)
                v[i] = j
                pts.append(domain.from_index(v))
            meta.append((i, sign))
    return pts, meta


def _bfgs_gradient(cur, costs, meta):
    """``BFGS::WaitForGradientPoints`` 的合成分支。"""
    got, k = {}, 0
    c0 = costs.get("f0")
    for i, sign in meta:
        if (i, sign) in costs["index"]:
            got[(i, sign)] = costs["index"][(i, sign)]
    n = len(cur)
    g = [0.0] * n
    for i in range(n):
        dr = got.get((i, +1))
        dl = got.get((i, -1))
        dr = None if dr is None else dr - c0
        dl = None if dl is None else c0 - dl
        cnt = int(dr is not None) + int(dl is not None)
        if cnt == 0:
            g[i] = 0.0
        elif dr is not None and dl is not None and dl <= 0 and dr >= 0:
            g[i] = 0.0                       # 离散局部极小
        elif dr is not None and dr < 0:
            g[i] = dr
        elif dl is not None and dl > 0:
            g[i] = dl
        else:
            g[i] = ((dl or 0.0) + (dr or 0.0)) / max(cnt, 1)
    return g


def line_search_algo(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                     params: Params | None = None):
    """``Alchemy::LineMinimize`` 作为独立算法：沿最陡方向做一次完整线搜索。

    原库把它当子算法（Powell / BFGS 内嵌）用；单独注册是为了能按名字取出来对拍。
    """
    p = params or Params()
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    direction = [1.0 if i == 0 else 0.0 for i in range(len(domain))]
    best_ix, best_c = yield from line_minimize(domain, cur, direction, c,
                                               one_sided=False,
                                               min_steps=p.int("minimum_steps_for_brent", 0))
    return best_ix


def bfgs(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
         params: Params | None = None, max_iter: int = 40):
    """``Alchemy::BFGS``（状态机 ``Start → ComputeGradient → WaitForGradientPoints
    → LineSearchDone``）。

    超参：``double_side_gradient``（默认真）、``fail_count_limit``（默认 0）、
    ``directional``（调用方硬编码 true）。
    """
    p = params or Params()
    fail_limit = p.int("fail_count_limit", 3)   # BFGS 调用方硬编码 3
    double_side = p.bool("double_side_gradient", True)
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    H = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    g_prev, x_prev = None, None
    fails = 0
    for _ in range(max_iter):
        pts, meta = _gradient_points(domain, cur)
        raw = yield pts
        idx = {}
        k = 0
        for i, sign in meta:
            if 0 <= cur[i] + sign < tun[i]:
                idx[(i, sign)] = raw[k]
                k += 1
        g = _bfgs_gradient(cur, {"f0": c, "index": idx}, meta)
        if not double_side:
            g = [gi if gi != 0 else 0.0 for gi in g]
        if all(abs(x) < 1e-15 for x in g):
            break
        if g_prev is not None:
            sdiff = [cur[i] - x_prev[i] for i in range(n)]
            ydiff = [g[i] - g_prev[i] for i in range(n)]
            sy = sum(sdiff[i] * ydiff[i] for i in range(n))
            yy = sum(y * y for y in ydiff)
            ss = sum(s * s for s in sdiff)
            if sy > math.sqrt(1e-10 * yy * ss):
                Hy = [sum(H[i][j] * ydiff[j] for j in range(n)) for i in range(n)]
                yHy = sum(ydiff[i] * Hy[i] for i in range(n))
                if yHy > 0:
                    rho = 1.0 / sy
                    for i in range(n):
                        for j in range(n):
                            H[i][j] += (rho * sdiff[i] * sdiff[j]
                                        - Hy[i] * Hy[j] / yHy
                                        + yHy * (rho * sdiff[i] - Hy[i] / yHy)
                                        * (rho * sdiff[j] - Hy[j] / yHy))
                            H[j][i] = H[i][j]
        d = [-sum(H[i][j] * g[j] for j in range(n)) for i in range(n)]
        m = max((abs(x) for x in d), default=0.0)
        if m < 1e-12:
            break
        d = [x / m for x in d]          # 方向归一：t=1 就是「一个格点」
        un, cn = yield from line_minimize(domain, cur, d, c, one_sided=True,
                                          min_steps=p.int("minimum_steps_for_brent", 0))
        if cn >= c - 1e-15:
            fails += 1
            if fails > fail_limit:
                break
            d = [-1.0 if gi > 0 else (1.0 if gi < 0 else 0.0) for gi in g]
            un, cn = yield from line_minimize(domain, cur, d, c, one_sided=True)
            if cn >= c - 1e-15:
                break
        else:
            fails = 0
        x_prev, g_prev = list(cur), g
        cur, c = list(un), cn
    return cur


def conjugate_gradient(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                       params: Params | None = None, max_iter: int = 40):
    """``Alchemy::ConjugateGradient``（Polak-Ribiere）。

    中心差分 ``g_i = (f(x+e_i) − f(x−e_i))/2``，再乘该维档位数；离散局部极小吃零；
    ``beta = g·(g−g_old)/‖g_old‖²`` 截断为 0；每 n 轮重启。``max_iter`` 在库里读了不用。
    """
    p = params or Params()
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    g_prev, d = None, None
    for it in range(max_iter):
        pts, meta = _gradient_points(domain, cur)
        raw = yield pts
        got, k = {}, 0
        for i, sign in meta:
            if 0 <= cur[i] + sign < tun[i]:
                got[(i, sign)] = raw[k]
                k += 1
        g = []
        for i in range(n):
            fp, fm = got.get((i, +1)), got.get((i, -1))
            if fp is not None and fm is not None:
                gi = (fp - fm) / 2.0 * tun[i]
            elif fp is not None:
                gi = fp - c
            elif fm is not None:
                gi = c - fm
            else:
                gi = 0.0
            g.append(gi)
        if all(abs(x) < 1e-15 for x in g):
            break
        if g_prev is None:
            d = [-x for x in g]
        else:
            num = sum(g[i] * (g[i] - g_prev[i]) for i in range(n))
            den = sum(x * x for x in g_prev)
            beta = max(0.0, num / den) if den > 0 else 0.0
            d = [beta * (d[i] if d else 0.0) - g[i] for i in range(n)]
            if it % max(1, n) == 0:
                d = [-x for x in g]
        m = max((abs(x) for x in d), default=0.0)
        if m < 1e-12:
            break
        d = [x / m for x in d]
        un, cn = yield from line_minimize(domain, cur, d, c, one_sided=True,
                                          min_steps=p.int("minimum_steps_for_brent", 30))
        if cn >= c - 1e-15:
            break
        g_prev = g
        cur, c = list(un), cn
    return cur


def quasi_newton(domain: Domain, goal: Goal, start: Sequence[int] | None = None,
                 params: Params | None = None, max_iter: int = 40):
    """``Alchemy::QuasiNewton``：秩 2 更新走 ublas，邻居缺失时用 ``f(current)`` 补。

    与 BFGS 的差别：不做离散局部极小吃零；停止准则是 ``top() == current_point``；
    ``iteration_limit`` 读了不用。还有那个著名怪点：更新时 ``s`` 槽位其实装的是
    **上一轮方向**（``AlgState+0x88`` 在同一函数末尾被覆盖）—— 这里照抄，
    用「上一轮方向」代替位移。
    """
    p = params or Params()
    tun = domain.tunables
    n = len(tun)
    if n == 0:
        yield []
        return
    cur = domain.clamp_index(start) if start is not None else domain.center_index()
    c = (yield [domain.from_index(cur)])[0]
    H = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    g_prev, d_prev = None, None
    for _ in range(max_iter):
        pts, meta = _gradient_points(domain, cur)
        raw = yield pts
        got, k = {}, 0
        for i, sign in meta:
            if 0 <= cur[i] + sign < tun[i]:
                got[(i, sign)] = raw[k]
                k += 1
        g = []
        for i in range(n):
            fp, fm = got.get((i, +1)), got.get((i, -1))
            fp = c if fp is None else fp
            fm = c if fm is None else fm
            g.append((fp - fm) / 2.0)
        if all(abs(x) < 1e-15 for x in g):
            break
        if g_prev is not None and d_prev is not None:
            s = list(d_prev)                  # ← 原实现的“怪点”：用上一轮方向当 s
            y = [g[i] - g_prev[i] for i in range(n)]
            sy = sum(s[i] * y[i] for i in range(n))
            if sy > 0:
                Hy = [sum(H[i][j] * y[j] for j in range(n)) for i in range(n)]
                yHy = sum(y[i] * Hy[i] for i in range(n))
                if yHy > 0:
                    rho = 1.0 / sy
                    for i in range(n):
                        for j in range(n):
                            H[i][j] += (rho * s[i] * s[j] - Hy[i] * Hy[j] / yHy)
                            H[j][i] = H[i][j]
        d = [-sum(H[i][j] * g[j] for j in range(n)) for i in range(n)]
        m = max((abs(x) for x in d), default=0.0)
        if m < 1e-12:
            break
        d = [x / m for x in d]
        un, cn = yield from line_minimize(domain, cur, d, c, one_sided=True,
                                          min_steps=p.int("minimum_steps_for_brent", 3))
        if cn >= c - 1e-15:
            break
        g_prev, d_prev = g, d
        cur, c = list(un), cn
    return cur
