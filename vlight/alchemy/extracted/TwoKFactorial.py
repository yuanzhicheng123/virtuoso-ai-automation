"""TwoKFactorial — extracted from libalchemy.so (Alchemy::TwoKFactorial, algs/TwoKFactorial.cpp).

states:
    Init        0x3c15a8   -> Start
    Start       0x3c1702   -> Fork<Split>(L0)                  L_i = flags[i] ? tunables[i] : 2
    Split       0x3c1a6a   -> Fork<Split>(L_{t+1}) | PointRun
    PointRun    0x3c20ae   -> ThreadsWait
    ThreadsWait 0x3c21e6   -> throw ShortCircuits::Return      (run over)

hyperparams (Alchemy::TwoKFactorial::Data 0x3c2364):
    modelParamFlags : shared_ptr<vector<bool>>  size == tunables.size()
    (no default value is written: the field stays empty -> size 0 -> assert fires)

Point generation (the whole body):
    level(t, k) = flags[t] ? k : (k ? tunables[t]-1 : 0)
    -> the plain 2^n corner set when every modelParamFlags bit is false;
    -> a full mixed-radix lattice when some bits are set.
    No centre point, no replicate, no effect arithmetic.
"""

# --- .rodata / .data constants (address in libalchemy.so) -------------------
STR_START_RUN   = 0x5766c0   # 'virtual Alchemy::State::Ptr Alchemy::TwoKFactorial::Start::run()'
STR_SRC         = 0x576701   # 'algs/TwoKFactorial.cpp'
STR_MODELFLAGS  = 0x576718   # 'POINTS->tunables.size()==modelParamFlags->size()'
STR_TUN_GT0     = 0x576750   # 'POINTS->tunables[tunable_idx]>0'
STR_SPLIT_RUN   = 0x576770   # 'virtual Alchemy::State::Ptr Alchemy::TwoKFactorial::Split::run()'
STR_FORK_GE0    = 0x5767b1   # 'FORKIDX >= 0'
STR_FORK_LT_TUN = 0x5767c0   # 'FORKIDX<POINTS->tunables[tunable_idx]'
STR_FORK_LT_2   = 0x5767e6   # 'FORKIDX<2'
STR_TUN_GT0_2   = 0x5767f0   # 'POINTS->tunables[tunable_idx] > 0'
TI_RETURN       = 0x6a8a78   # typeinfo ShortCircuits::Return  (R_X86_64_GLOB_DAT -> 0x68eac0)


def _params(params):
    """Accept None / dict / any Params-like object (e.g. vlight.alchemy.Params)."""
    if params is None:
        return {}
    if isinstance(params, dict):
        return dict(params)
    f = getattr(params, "as_dict", None)
    if callable(f):
        try:
            return dict(f())
        except Exception:
            pass
    d = getattr(params, "_d", None)
    if isinstance(d, dict):
        return dict(d)
    try:
        return dict(params)
    except Exception:
        return {}


def _better(goal, a, b):
    """True when cost a is strictly better than b. goal.maximize flips the sense."""
    mx = bool(getattr(goal, "maximize", False)) if goal is not None else False
    return a > b if mx else a < b


def run(domain, goal, start=None, params=None):
    """Yield a batch of points, receive costs via send(); return the corner ranking.

    Protocol:  batch = next(g);  costs = g.send(batch) ...  StopIteration.value = result
    Return value: list of index-vectors sorted best-first (a ranking over the design).
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)
    # The C++ Data ctor leaves modelParamFlags empty and Start 0x3c17ef asserts on the size.
    # Here an absent flag vector defaults to "no flags" so the class is runnable standalone;
    # a supplied vector of the wrong length still trips the assert.
    given = P.get("modelParamFlags")
    flags = list(given) if given else [False] * n

    # Start 0x3c1702: resize(1<<n) happens *before* the empty-domain guard.
    if n == 0:                       # 0x3c179b  throw ShortCircuits::Return
        return []
    if len(flags) != n:              # 0x3c17ef  assert(tunables.size()==modelParamFlags->size())
        raise AssertionError("POINTS->tunables.size()==modelParamFlags->size()")

    # Depth-first enumeration.  tunable 0 is the outermost fork, k ascends.
    corners = []
    cur = [0] * n

    def _rec(t):                                    # Split 0x3c1a6a
        if t == n:                                  # 0x3c1d1f  mTunableIdx == tunables.size()
            corners.append(list(cur))               # PointRun 0x3c20ae
            return
        L = tun[t] if flags[t] else 2               # 0x3c18eb / 0x3c1976 / 0x3c1ec7
        if flags[t] and tun[t] <= 0:                # 0x3c187f / 0x3c1bca
            raise AssertionError("POINTS->tunables[tunable_idx] > 0")
        for k in range(L):                          # FORKIDX = k
            if flags[t]:
                if not (0 <= k < tun[t]):           # 0x3c1b49
                    raise AssertionError("FORKIDX<POINTS->tunables[tunable_idx]")
                cur[t] = k                          # 0x3c1c47   level = FORKIDX
            else:
                if k >= 2:                          # 0x3c1b77
                    raise AssertionError("FORKIDX<2")
                cur[t] = (tun[t] - 1) if k else 0   # 0x3c1cb6 / 0x3c1c7e
            _rec(t + 1)

    _rec(0)

    costs = yield corners                            # one batch: the whole design
    costs = list(costs)

    pairs = list(zip(costs, corners))
    pairs.sort(key=lambda c: c[0], reverse=bool(getattr(goal, "maximize", False)))
    return [p for _, p in pairs]


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class Quad2D:
        """2-D quadratic on a 21-level grid: f = (x-0.3)^2 + 2*(y+0.5)^2."""
        def __init__(self, levels=21):
            self.tunables = [levels, levels]
            self.levels = levels
            self.lo, self.hi = -1.0, 1.0

        def from_index(self, ix):
            return tuple(self.lo + (self.hi - self.lo) * i / (self.levels - 1) for i in ix)

        def to_index(self, p):
            return [int(round((x - self.lo) / (self.hi - self.lo) * (self.levels - 1))) for x in p]

        def cost(self, ix):
            x, y = self.from_index(ix)
            return (x - 0.3) ** 2 + 2.0 * (y + 0.5) ** 2

    dom = Quad2D(21)
    g = run(dom, None, params={"modelParamFlags": [False, False]})
    batch = next(g)
    print("TwoKFactorial design (%d points):" % len(batch))
    for p in batch:
        print("   ", p, "x=%s" % (dom.from_index(p),))
    total = 0
    while True:
        costs = [dom.cost(p) for p in batch]
        total += len(costs)
        try:
            batch = g.send(costs)
        except StopIteration as e:
            rank = e.value
            break
        print("next batch:", batch)
    best = rank[0]
    print("evaluations:", total)
    print("ranking (best first):")
    for p in rank:
        print("   ", p, "cost=%.6f" % dom.cost(p))
    print("best point:", best, dom.from_index(best), "cost=%.6f" % dom.cost(best))
