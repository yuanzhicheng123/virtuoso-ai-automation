"""DOE — extracted from libalchemy.so (Alchemy::DOE, compiled in algs/*.cpp, visible name "DOE").

states:
    Init       0x30b87a   -> Start
    Start      0x30ba10   -> PointsWait                     (builds the whole design up front)
    PointsWait 0x30c1d4   -> self until every point isEvaluated() -> ThreadsWait
    ThreadsWait 0x30c332  -> throw ShortCircuits::Return    (run over)

hyperparams (Alchemy::DOE::Data 0x30c56e):
    current_point      : shared_ptr<DesignPoint>        (no literal default; params must supply it)
    interaction_level  : uint   default 2                  (0x30c83d  mov DWORD PTR [rax],0x2)

Point generation (the whole body, Start 0x30ba10):
    k = min(interaction_level, n)                           0x30ba64-0x30bab8 (clamped, then written back)
    base = current_point->values                            0x30bae7
    queue = [[0],[1],...,[n-1]]                            0x30bb82 / 0x30bb9f
    while queue:                                           0x30bbc5
        S = queue.pop_front()                              0x30bc0d / 0x30bc22
        hi = lo = base; up = low = true
        for i in S:                                        0x30bceb
            if base[i] == tunables[i]-1: up  = false       0x30bd39 / 0x30bd46
            else                       : hi[i] = tunables[i]-1
            if base[i] == 0            : low = false       0x30bdd8 / 0x30bde1
            else                       : lo[i] = 0
        if up : emit hi                                    0x30be5f (factory[0x18]) / 0x30bea4 push
        if low: emit lo                                    0x30bee1 / 0x30bf29 push
        if |S| < k: for j in S[-1]+1 .. n-1: queue.push(S+[j])      0x30bf5d-0x30c003
    -> 2*(2^n - 1) points in the worst case; with the default k = 2 exactly
       n*(n+1) points (2n axial + 2*C(n,2) diagonal), minus the corners the base
       point already occupies.  There is NO explicit centre point and NO 2^n cube.
"""

# --- .rodata constants ------------------------------------------------------
KEY_CURRENT_POINT   = 0x564ea4   # 'current_point'
KEY_INTERACTION     = 0x564eb2   # 'interaction_level'
TI_RETURN           = 0x6a8a78   # typeinfo ShortCircuits::Return
DEFAULT_INTERACTION = 0x2        # 0x30c83d


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
    mx = bool(getattr(goal, "maximize", False)) if goal is not None else False
    return a > b if mx else a < b


def _driver_start(domain, start):
    """Driver call #0 -- the start point evaluated before the algorithm runs."""
    tun = [int(t) for t in domain.tunables]
    if start is None:
        return [(t - 1) // 2 for t in tun]
    if isinstance(start, dict):
        if hasattr(domain, "to_index"):
            return list(domain.to_index(start))
        return [min(max(int(start["x%d" % i]), 0), t - 1) for i, t in enumerate(tun)]
    ix = [int(v) for v in start]
    if hasattr(domain, "clamp_index"):
        return list(domain.clamp_index(ix))
    return [min(max(v, 0), t - 1) for v, t in zip(ix, tun)]


def run(domain, goal, start=None, params=None):
    """Yield one batch with the whole composite design; return the ranking.

    Return value: list of index-vectors sorted best-first.
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)
    if start is None:
        start = [(t - 1) // 2 for t in tun]          # nominal / centre of the grid
    base = list(start)

    k = int(P.get("interaction_level", DEFAULT_INTERACTION))
    if k >= n:                                        # 0x30ba64  clamp
        k = n
    P["interaction_level"] = k                        # 0x30bab8  written back into the param

    if n == 0:
        return []

    # driver call #0: FunctionOptimizer::run (0x3ce70a) evaluates ``start`` before
    # DOE::Start @0x30ba10 builds the composite design (the design uses the start as
    # its centre but never re-submits it -- no centre point, see DOE.md §4).
    yield [domain.from_index(_driver_start(domain, start))]

    queue = [[i] for i in range(n)]                   # 0x30bb82
    pts = []
    while queue:                                      # 0x30bbc5
        S = queue.pop(0)                              # 0x30bc0d / 0x30bc22  pop_front
        hi = list(base)
        lo = list(base)
        up = low = True                               # 0x30bc27 / 0x30bc2b
        for i in S:
            if base[i] == tun[i] - 1:                 # 0x30bd39
                up = False
            else:
                hi[i] = tun[i] - 1                    # 0x30bda6
            if base[i] == 0:                          # 0x30bdd8
                low = False
            else:
                lo[i] = 0                             # 0x30be07
        if up:                                        # 0x30be2b
            pts.append(hi)                            # 0x30bea4
        if low:                                       # 0x30bead
            pts.append(lo)                            # 0x30bf29
        if len(S) < k:                                # 0x30bf5d
            for j in range(S[-1] + 1, n):             # 0x30bf7c  vec.back()+1
                queue.append(S + [j])                 # 0x30c003

    costs = yield pts                                 # PointsWait: whole design evaluated as one batch
    costs = list(costs)

    pairs = list(zip(costs, pts))
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
    start = dom.to_index((0.0, 0.0))                  # centre of the box -> index 10
    print("start (centre):", start, dom.from_index(start))
    for k in (2, 3):
        g = run(dom, None, start=start, params={"interaction_level": k})
        batch = next(g)                      # driver call #0: the start point
        batch = g.send([dom.cost(p) for p in batch])
        print("\ninteraction_level=%d -> %d points:" % (k, len(batch)))
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
        for p in rank[:6]:
            print("   ", p, "cost=%.6f" % dom.cost(p))
        print("best point:", best, dom.from_index(best), "cost=%.6f" % dom.cost(best))
