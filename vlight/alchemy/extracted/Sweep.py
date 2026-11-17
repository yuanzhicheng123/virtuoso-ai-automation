"""Sweep — extracted from libalchemy.so (algs/Sweep.cpp).

states:
    Init -> Start                                    @0x3bf5d0 / @0x3bf71c
    Start -> {Return, Fork<AlgState,Split>(tunables[0])}
                                                     @0x3bf7c9 / @0x3bf804
    Split -> {PointRun, Fork<AlgState,Split>(tunables[dim])}
                                                     @0x3bf99b / @0x3bfac7
    PointRun -> ThreadsWait                          @0x3bfbd6
    ThreadsWait -> {self, JOIN.../Return}            @0x3bfd31 / @0x3bfd5e

hyperparams: NONE.  Sweep::Data::Data(Params) @0x3bfe8c is 31 bytes and contains no
Alchemy::Params::operator[] call at all — the algorithm is not tunable.

Enumeration: one Fork per dimension, dimension 0 outermost.  Every combination of
tunables[0] x ... x tunables[n-1] is visited exactly once; a leaf (PointRun) submits
its point and blocks in ThreadsWait until that point is evaluated.  There is no
batch-size control: the whole fork tree is materialised, so up to
prod(tunables) points can be in flight.  This module emits the complete grid as
one batch to mirror "all leaves active"; chunk it downstream if the grid is large.
"""

K_NONE = ()


_PARAM_KEYS = ()

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

def _driver_start(domain, start):
    """The ask/tell driver evaluates ``start`` *before* the algorithm's first point.

    ``Alchemy::FunctionOptimizer::run`` (0x3ce70a, spec_core.md §6) always performs one
    cost-function call for the start point; e.g.
    ``native/harness OnePoint 2 21 10 0 10 7 3`` evaluates (7,3) first although
    OnePoint's only design point is the grid centre.  Sweep itself never reads the
    start (``Start::run`` @0x3bf71c only zero-fills ``indices`` @0x3bf764), so that call
    belongs to the driver -- it is emitted here so the evaluation sequence is
    point-for-point comparable with the real library (native/parity_check.py).
    """
    tun = [int(t) for t in domain.tunables]
    if start is None:                       # standalone default: the grid centre
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
    """yield a batch of points; receive costs via send(); return best index list"""
    tun = [int(t) for t in domain.tunables]
    n = len(tun)

    if n == 0:
        # 0x3bf7b5: makeDesignPoint(empty); throw ShortCircuits::Return
        costs = yield [domain.from_index([])]
        return []

    if tun[0] <= 0:                       # assert POINTS->tunables[0]>0  (Sweep.cpp:47)
        raise AssertionError("POINTS->tunables[0]>0")

    # driver call #0: the start point (FunctionOptimizer::run, before Start::run)
    yield [domain.from_index(_driver_start(domain, start))]


    # cartesian product with dimension 0 as the outermost Fork
    def rec(dim, cur):
        for v in range(tun[dim]):
            cur[dim] = v                  # 0x3bf963
            if dim + 1 == n:              # 0x3bf99b -> PointRun
                yield list(cur)
            else:
                if tun[dim + 1] <= 0:     # assert Sweep.cpp:54
                    raise AssertionError("POINTS->tunables[tunable_idx]>0")
                yield from rec(dim + 1, cur)   # 0x3bfac7

    grid = list(rec(0, [0] * n))
    costs = yield [domain.from_index(ix) for ix in grid]

    best_i = 0
    for i in range(1, len(grid)):
        if costs[i] < costs[best_i]:
            best_i = i
    return list(grid[best_i])


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class Quad:
        tunables = [41, 41]

        def from_index(self, ix):
            return [-2.0 + 4.0 * ix[0] / 40.0, -2.0 + 4.0 * ix[1] / 40.0]

        def to_index(self, point):
            return [self.clamp(int(round((point[0] + 2.0) * 10))),
                    self.clamp(int(round((point[1] + 2.0) * 10)))]

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

    gen = run(dom, None)
    batch = next(gen)                 # driver call #0: the start point
    nfev[0] = 0
    batch = gen.send([cost(p) for p in batch])
    npts = len(batch)                 # the Cartesian grid itself
    while True:
        try:
            batch = gen.send([cost(p) for p in batch])
        except StopIteration as stop:
            best = stop.value
            break
    print("Sweep final=%s cost=%.6e nfev=%d grid=%d" % (best, seen[tuple(best)], nfev[0], npts))
