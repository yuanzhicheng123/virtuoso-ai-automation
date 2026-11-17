"""HookeJeeves — extracted from libalchemy.so (algs/HookeJeeves.cpp).

states:
    Init -> Start -> {Start_WaitForStartingPoint, Start_HaveStartingPoint}
    Start_HaveStartingPoint -> Fork(2*n) of Move   (parallel)   @0x32fc67
    Start_HaveStartingPoint -> Move                (sequential) @0x32fced
    Move -> {Move, MoveDone}                       @0x330294 / @0x3302ac
    MoveWait -> {MoveWait, MoveDone}               @0x330440 / @0x330503
    MoveDone -> {Done, Fork(2*n), Move}            @0x330a4a / @0x330b71 / @0x330c0c
    Done -> {Return (throw), Start (restart)}      @0x330dd5 / @0x330e3e

hyperparams (Data::Data(Params) @0x33100c):
    current_point  shared_ptr<DesignPoint>  default: new empty DesignPoint  0x3310f8
    population     shared_ptr<Population>   default: Population(1)          0x3312f9
    parallel       shared_ptr<bool>         default: true                   0x331506
    should_rerun   shared_ptr<bool>         default: true                   0x3316f7

The step vector is derived from the discrete granularity, never from bounds:
    steps[i] = trunc(0.1 * tunables[i])        (0 promoted to 1)
    steps    *= 0.5                            on a failed sweep
    terminate when every steps[i] == 0
"""

# --- .rodata constants (VMA == file offset) ---------------------------------
INITIAL_STEP_FACTOR = 0.1    # Alchemy::HookeJeeves::InitialStepFactor 0x567f08
STEP_REDUCTION_FACTOR = 0.5  # Alchemy::HookeJeeves::StepReductionFactor 0x567f10

# --- hyper-parameter keys (rodata addresses) --------------------------------
K_CURRENT_POINT = "current_point"  # 0x567f18
K_POPULATION = "population"        # 0x567f26
K_PARALLEL = "parallel"            # 0x567f31
K_SHOULD_RERUN = "should_rerun"    # 0x567f3a

DEFAULTS = {
    K_CURRENT_POINT: None,   # fresh empty DesignPoint
    K_POPULATION: None,      # Population(1)
    K_PARALLEL: True,        # movb $0x1 @0x331506
    K_SHOULD_RERUN: True,    # movb $0x1 @0x3316f7
}


def _trunc_half(steps):
    """Alchemy::operator*(vector<int> const&, double const&) @0x3e5bca:
    cvtsi2sd / mulsd / cvttsd2si -> truncation toward zero."""
    return [int(s * STEP_REDUCTION_FACTOR) for s in steps]


def _resolve_start(domain, start, param_value):
    """start may be a point (dict / coordinate sequence) or an index list.
    The engine's own default is an *empty* DesignPoint, so a standalone run
    falls back to the nominal level (or the grid centre)."""
    s = start if start is not None else param_value
    n = len(domain.tunables)
    if s is None:
        if hasattr(domain, "nominal_index"):
            return list(domain.nominal_index())
        return list(domain.center_index())
    if isinstance(s, dict):
        return list(domain.to_index(s))
    if (isinstance(s, (list, tuple)) and len(s) == n
            and all(isinstance(v, int) for v in s)):
        return list(s)
    return list(domain.to_index(s))


_PARAM_KEYS = (K_CURRENT_POINT, K_POPULATION, K_PARALLEL, K_SHOULD_RERUN)

def _as_params(params, keys):
    """Accept a plain dict or an Alchemy::Params-like object (.has/.get)."""
    if params is None:
        return {}
    if isinstance(params, dict):
        return dict(params)
    inner = getattr(params, "_d", None)
    if isinstance(inner, dict):
        return dict(inner)
    if hasattr(params, "items"):
        try:
            return dict(params.items())
        except Exception:
            pass
    if hasattr(params, "has") and hasattr(params, "get"):
        return {k: params.get(k) for k in keys if params.has(k)}
    return {}

# --------------------------------------------------------------------------- #
# 与引擎一致的排序键：DesignPoint::operator< @0x3d1dda
#   cost(optional 空<有值) -> isCacheHit(false 先) -> values 的 32 位哈希 -> 字典序
# 哈希循环（0x3d1ec2..0x3d2044，逐指令）：
#     h = 0; mul = 0xef32f8c9            // 0x3d1ed0 / 0x3d1ed7
#     for i in range(len(values)):
#         h = h*mul + values[i]          // imul/add 0x3d1f08..0x3d1f57
#         r = ((values[i] % 16) + 16) % 16   // cdq/shr/and/sub 0x3d1f73..0x3d1f80
#         h = (h << r) + (h >> (16-r))   // 16-bit rotate 0x3d1f87..0x3d1fc5
#         mul *= 0x5c6b7                 // 0x3d2036
#
# **关键**：这个比较不是“每点一个哈希键”。0x3d1fc8 处第二次取旋转量时读的是
# **左操作数** `this->values[i]`，所以右操作数的哈希是用左操作数的 r 序列折出来的：
#     h1 = fold(a, rot=a)   h2 = fold(b, rot=a)
# 这使 `operator<` 对 (a,b) 与 (b,a) 并不来自同一个键 —— 也解释了 native 里
# (70,30) 与 (30,70) 互不小于（等价）的现象。用“对称的每点哈希”会在等代价并列时
# 选错点（quad 的第 10 次评估）。模型已用 native/probe_cmp.cpp 直接 call 真符号验证：
# 7x7 网格 2401 对全部一致。
# --------------------------------------------------------------------------- #
_HASH_MUL = 0xEF32F8C9      # 0x3d1ed0  初始乘子
_HASH_MUL_STEP = 0x5C6B7    # 0x3d2036  每维推进


def _hash_fold(values, rot_src):
    """fold(values) using the rotation amounts taken from `rot_src` (0x3d1ec2..0x3d2044)."""
    h = 0
    mul = _HASH_MUL
    for i, v in enumerate(values):
        v = int(v)
        h = (h * mul + (v & 0xFFFFFFFF)) & 0xFFFFFFFF     # 0x3d1f08..0x3d1f57
        rv = int(rot_src[i])
        r = ((rv % 16) + 16) % 16                          # 0x3d1f73..0x3d1f80
        h = ((h << r) + (h >> (16 - r))) & 0xFFFFFFFF     # 0x3d1f87..0x3d1fc5
        mul = (mul * _HASH_MUL_STEP) & 0xFFFFFFFF         # 0x3d2036
    return h


def _dp_less(ia, ca, ib, cb, ha=False, hb=False):
    """Exact `DesignPoint::operator<` @0x3d1dda."""
    if ca < cb:                                          # 0x3d1e01
        return True
    if cb < ca:                                          # 0x3d1e2a
        return False
    if (not ha) and hb:                                  # 0x3d1e3d..0x3d1e58
        return True
    if ha and (not hb):                                  # 0x3d1e62..0x3d1e7d
        return False
    if len(ia) != len(ib):                               # 0x3d1e87..0x3d1ebd
        return False
    h1 = _hash_fold(ia, ia)                              # 0x3d1ec2..0x3d1fc5
    h2 = _hash_fold(ib, ia)                              # 0x3d1fc8..0x3d2033 (r from ia!)
    if h1 < h2:                                          # 0x3d2049..0x3d2068 (unsigned)
        return True
    if h2 < h1:
        return False
    for x, y in zip(ia, ib):                             # 0x3d2074..0x3d2127
        if x < y:
            return True
        if y < x:
            return False
    return False                                         # 0x3d212c


def _better(ix, cost, best_ix, best_cost):
    """等价于 `DesignPoint(ix,cost) < DesignPoint(best_ix,best_cost)`。"""
    return _dp_less(list(ix), float(cost), list(best_ix), float(best_cost))


def run(domain, goal, start=None, params=None):
    """yield a batch of points; receive costs via send(); return best index list"""
    params = _as_params(params, _PARAM_KEYS)
    parallel = bool(params.get(K_PARALLEL, DEFAULTS[K_PARALLEL]))
    should_rerun = bool(params.get(K_SHOULD_RERUN, DEFAULTS[K_SHOULD_RERUN]))

    tun = [int(t) for t in domain.tunables]
    n = len(tun)

    # ---- Start / Start_HaveStartingPoint  @0x32fa18 -------------------------
    start_ix = list(domain.clamp_index(
        _resolve_start(domain, start, params.get(K_CURRENT_POINT))))

    cache = {}

    def evaluate(batch_ix):
        """yield one batch, return the list of costs."""
        # 注意：引擎**不**跳过缓存命中（native 实测 Sweep 10202 次评估 / 10201 个点），
        # 这里也不过滤——要一比一就得重复提交。
        costs = yield [domain.from_index(ix) for ix in batch_ix]
        if len(costs) != len(batch_ix):
            raise ValueError("expected %d costs, got %d" % (len(batch_ix), len(costs)))
        for ix, c in zip(batch_ix, costs):
            cache[tuple(ix)] = float(c)
        return [float(c) for c in costs]

    # the starting point is pre-evaluated in the engine (Start::run @0x32f73a
    # checks isEvaluated() first); a standalone run has to fetch its cost once.
    (c0,) = yield from evaluate([start_ix])

    best_ix = list(start_ix)
    best_cost = c0

    # Done::run @0x330da8 -> Start: one re-entry when should_rerun is set
    passes = 2 if should_rerun else 1
    for _pass in range(passes):
        cur = list(start_ix)              # 0x32fa79  current := startingPoint.design
        cur_cost = c0
        base = list(cur)                  # 0x32fa92  base    := current
        steps = [int(INITIAL_STEP_FACTOR * t) for t in tun]   # 0x32fabc
        for i in range(n):                                    # 0x32fb1c
            if steps[i] == 0:
                # assert(POINTS->tunables[i] > 1)  algs/HookeJeeves.cpp:47
                steps[i] = 1                                  # 0x32fb97
        move_index = 0                                        # 0x32fba6

        while True:
            if parallel:
                # ---- Fork<AlgState,Move>(state, 2*n)  @0x32fc67 -------------
                order = []
                for m in range(2 * n):
                    i, s = m >> 1, m & 1           # 0x32fe38 / 0x32ff29
                    if steps[i] == 0:              # 0x32fe59 -> MoveDone
                        continue
                    trial = list(base)             # 0x32ff17
                    if s:                          # ---- minus direction ----
                        if trial[i] == 0:          # 0x32ff4b -> MoveDone
                            continue
                        trial[i] -= steps[i]       # 0x330020
                        if trial[i] < 0:           # 0x330059
                            trial[i] = 0
                    else:                          # ---- plus direction ----
                        if trial[i] + 1 == tun[i]: # 0x330079 -> MoveDone
                            continue
                        trial[i] += steps[i]       # 0x33017c
                        if trial[i] >= tun[i]:     # 0x330208
                            trial[i] = tun[i] - 1
                    order.append(trial)

                for c, ix in zip((yield from evaluate(order)), order):
                    if _better(ix, c, best_ix, best_cost):
                        best_cost, best_ix = c, list(ix)

                # ---- MoveDone::run  @0x330566 ------------------------------
                if best_ix != cur:                            # 0x330678
                    cur = list(best_ix)                       # 0x3306e0
                    base = [c + (c - b) for c, b in zip(cur, base)]  # 0x33070b..0x33074f
                    for i in range(n):                        # clamp 0x330772
                        if base[i] < 0:
                            base[i] = 0                       # 0x3307bb
                        elif base[i] >= tun[i]:
                            base[i] = tun[i] - 1              # 0x330830
                elif base != cur:                             # 0x3308a4 retract
                    base = list(cur)                          # 0x3308c9
                else:
                    steps = _trunc_half(steps)                # 0x33090b (parallel)
            else:
                # ---- one Move per batch (sequential) ----------------------
                if move_index >= 2 * n:
                    move_index = 0
                i, s = move_index >> 1, move_index & 1
                trial = list(base)
                valid = False
                if steps[i] != 0:
                    if s:
                        if trial[i] != 0:
                            trial[i] = max(0, trial[i] - steps[i])
                            valid = True
                    else:
                        if trial[i] + 1 != tun[i]:
                            trial[i] = min(tun[i] - 1, trial[i] + steps[i])
                            valid = True
                if valid:
                    (c,) = yield from evaluate([trial])
                    if _better(trial, c, best_ix, best_cost):
                        best_cost, best_ix = c, list(trial)

                if best_ix != cur:                            # 0x330678
                    cur = list(best_ix)
                    base = [c + (c - b) for c, b in zip(cur, base)]
                    for i in range(n):
                        if base[i] < 0:
                            base[i] = 0
                        elif base[i] >= tun[i]:
                            base[i] = tun[i] - 1
                elif base != cur:                             # 0x3308a4
                    base = list(cur)
                else:                                         # 0x330952
                    move_index += 1
                    if move_index == 2 * n:                   # 0x330986
                        steps = _trunc_half(steps)            # 0x3309af
                        move_index = 0

            # ---- termination: steps == 0  @0x330a07 -> Done 0x330a4a ------
            if all(s == 0 for s in steps):
                break

    return list(best_ix)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class Quad:
        """2-D quadratic, 41 levels per dimension, optimum at level (20,20)."""

        tunables = [41, 41]

        def from_index(self, ix):
            return [-2.0 + 4.0 * ix[0] / 40.0, -2.0 + 4.0 * ix[1] / 40.0]

        def to_index(self, point):
            return [self.clamp(i) for i in
                    (int(round((point[0] + 2.0) * 10)), int(round((point[1] + 2.0) * 10)))]

        def center_index(self):
            return [20, 20]

        def clamp(self, i):
            return max(0, min(40, int(i)))

        def clamp_index(self, ix):
            return [self.clamp(i) for i in ix]

    dom = Quad()
    nfev = [0]
    seen = {}

    def cost(p):
        nfev[0] += 1
        ix = tuple(dom.to_index(p))
        seen[ix] = p[0] * p[0] + p[1] * p[1]
        return seen[ix]

    for mode in (True, False):
        nfev[0] = 0
        seen.clear()
        gen = run(dom, None, start=dom.from_index([0, 0]),
                  params={"parallel": mode, "should_rerun": False})
        batch = next(gen)
        while True:
            try:
                batch = gen.send([cost(p) for p in batch])
            except StopIteration as stop:
                best = stop.value
                break
        print("parallel=%-5s final=%s cost=%.6e nfev=%d"
              % (mode, best, seen[tuple(best)], nfev[0]))
