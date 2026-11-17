"""ConjugateGradient -- extracted from libalchemy.so (Cadence, IC23.1-64b.43).

Reference: Alchemy::ConjugateGradient, algs/ConjugateGradient.cpp.
  states      : Init -> Start -> {Start_HaveStartingPoint | Start_WaitForStartingPoint}
              -> ComputeGradient -> WaitForGradientPoints
              -> Call<LineMinimizeDone>(LineMinimize::Init{d,+}) -> LineMinimizeDone
              -> {stop | Call<LineMinimizeDone>(LM{d, whole line}) | ComputeGradient}
  hyperparams : max_iter (shared_ptr<unsigned int>, default 200 @0x309963, never read),
                current_point, population; caller-hard-coded directional=true @0x30886e,
                minimum_steps_for_brent=30 @0x3088ed, directional=false @0x308f67 for the re-scan
  constants   : 2.0 @0x564b30, 1.0 @0x564b38 (dead mask), -0.0 @0x564b40
                (+ LineMinimize: CGOLD @0x5691f0, 0.5 @0x5691c8, 1.0 @0x5691d0, 1.5 @0x5691d8,
                 -0.0 @0x5691e0, -1.0 @0x5691f8)

Ask/tell protocol (identical in all four extracted files):
    g = run(domain, goal, start=None, params=None)
    batch = next(g)              # list of index vectors (list[int]) to evaluate
    batch = g.send([c, ...])     # costs in the same order/length -> next batch
    ...                          # StopIteration.value == best index vector
If send() receives None (plain next()), each cost is computed from
`goal(domain.from_index(ix))`, so the module also runs without a driver.

Domain interface: .tunables (levels per dimension), .from_index(ix), .to_index(point),
.center_index(), .clamp_index(ix).
"""
import math
from dataclasses import dataclass
from functools import cmp_to_key


# ---------------------------------------------------------------------------
#  canonical line search: algorithms/LineMinimize.py, loaded by path
# ---------------------------------------------------------------------------
import importlib.util
import os


def _load_line_minimize():
    """Load the canonical `Alchemy::LineMinimize` extraction that sits next to
    this file.  Path-relative + stdlib only, so `python3 <Name>.py` still runs
    standalone and no site-packages state is involved."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LineMinimize.py")
    spec = importlib.util.spec_from_file_location(
        "AlchemyLineMinimize_%s_%d" % (__name__, id(object())), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LineMinimize = _load_line_minimize()


class _NoCachePointFactory(LineMinimize._Factory):
    """`DesignPointFactoryImpl::create` always allocates a fresh, unevaluated
    DesignPoint and enqueues it (spec_core §8.2, 0x3cef6e / 0x3cf016);
    `Cache::hit` / `Cache::insert` have no caller inside libalchemy.so
    (spec_core §8.5).  LineMinimize.py's `_Factory.create` models the dead cache
    lookup, which would suppress exactly the evaluations the native line search
    really performs -- e.g. the base point of a Min_Exhaustive scan is evaluated
    again (native BFGS/rosen ev 103 re-evaluates (88,76)).  The generator
    resolves `_Factory` through the module globals on every run(), so the
    override is picked up without touching LineMinimize.py.
    """

    def create(self, ix):
        return LineMinimize._Pt(tuple(int(v) for v in ix), None, False)


LineMinimize._Factory = _NoCachePointFactory

# ---------------------------------------------------------------------------
#  constants (addresses are file == VMA addresses inside libalchemy.so)
# ---------------------------------------------------------------------------
ONE = 1.0                     # 0x563e80  00 00 00 00 00 00 f0 3f
NEG_ZERO = -0.0               # 0x563ea0  00 00 00 00 00 00 00 80
FABS_MASK = 0x7FFFFFFFFFFFFFFF  # 0x563e90  ff..7f  (andpd mask)
BFGS_GUARD = 1e-10            # 0x563eb0  bb bd d7 d9 df 7c db 3d
TWO = 2.0                     # 0x564b30
CGOLD = 0.3819660112501051    # 0x568a20 (symbol) / 0x5691f0 (copy)
HALF = 0.5                    # 0x5691c8
ONE_PT5 = 1.5                 # 0x5691d8
NEG_ONE = -1.0                # 0x5691f8

DP_MUL0 = 0xEF32F8C9          # DesignPoint::operator< hash seed     0x3d1ed0
DP_MULSTEP = 0x5C6B7          # DesignPoint::operator< hash step     0x3d2036

INT_MIN = -2147483648         # cvttsd2si result for NaN/+inf


# ---------------------------------------------------------------------------
#  C-semantics scalar helpers (the DSO is plain IEEE double arithmetic)
# ---------------------------------------------------------------------------
def _inf(sign):
    return float("inf") if sign > 0 else float("-inf")


def _div(a, b):
    """double division with C semantics (x/0 -> +-inf, 0/0 -> nan)."""
    if b == 0.0:
        if a != a or a == 0.0:
            return float("nan")
        return math.copysign(float("inf"), a) * math.copysign(1.0, b)
    return a / b


def _sqrt(x):
    if x != x or x < 0.0:
        return float("nan")
    if x == float("inf"):
        return float("inf")
    return math.sqrt(x)


def _cvtt(x):
    """(int)double  ==  cvttsd2si: truncate toward zero."""
    if x != x or x in (float("inf"), float("-inf")):
        return INT_MIN
    return int(x)


def _floori(x):
    """(int)floor(double): explicit floor@plt then cvttsd2si (0x335d66, 0x335dd1)."""
    if x != x or x in (float("inf"), float("-inf")):
        return INT_MIN
    return int(math.floor(x))


def _u32(x):
    """(unsigned)int comparison helper, e.g. 0x3347ab / 0x3348e8."""
    return int(x) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
#  DesignPoint ordering (spec_core 7.3): cost, isCacheHit, value hash, values
# ---------------------------------------------------------------------------
@dataclass
class _Pt:
    ix: tuple          # DesignPoint::values        (+0xd8)
    cost: float = None  # boost::optional<double>   (+0x08)
    hit: bool = False   # isCacheHit                (+0x18)
    err: bool = False   # hasErrorCost              (+0x1b)


def _values_hash(v, rot_src=None):
    """DesignPoint::operator< 0x3d1ec2..0x3d2044 (verified against the real
    `operator<` @0x3d1dda with native/probe_cmp.cpp, see native/PARITY.md 4.6).

    The loop folds TWO running hashes at once -- `this`'s (rbp-0x14) and
    `other`'s (rbp-0x18) -- and the 16-bit rotate amount is re-read from
    **this->values[i]** for both (0x3d1fdc: `mov rax,[rbp-0x38]` = `this`).
    So the comparison is NOT a per-point key: `less(a,b)` hashes `b` with `a`'s
    rotation sequence.  With equal costs this decides which of two tied points
    is `population->top()`, i.e. the base of the next line search / the next
    gradient centre (BFGS 0x30067c, CG 0x30aa4a, QN 0x3a8fa7)."""
    if rot_src is None:
        rot_src = v
    h = 0
    mul = DP_MUL0
    for i, x in enumerate(v):
        h = (h * mul + (int(x) & 0xFFFFFFFF)) & 0xFFFFFFFF
        r = ((int(rot_src[i]) % 16) + 16) % 16
        h = ((h << r) + (h >> (16 - r))) & 0xFFFFFFFF
        mul = (mul * DP_MULSTEP) & 0xFFFFFFFF
    return h


def _pt_less(a, b):
    if a.cost < b.cost:
        return True
    if b.cost < a.cost:
        return False
    if (not a.hit) and b.hit:
        return True
    if a.hit and (not b.hit):
        return False
    if len(a.ix) != len(b.ix):
        return False
    ha, hb = _values_hash(a.ix, a.ix), _values_hash(b.ix, a.ix)
    if ha < hb:
        return True
    if hb < ha:
        return False
    for x, y in zip(a.ix, b.ix):
        if x < y:
            return True
        if y < x:
            return False
    return False


def _pt_cmp(a, b):
    if _pt_less(a, b):
        return -1
    if _pt_less(b, a):
        return 1
    return 0


_PT_KEY = cmp_to_key(_pt_cmp)


class _Pop:
    """Population<DesignPoint, evil_ptr_less> with maxsize_ == 1 (0x300f4f: new Population(1))."""

    def __init__(self, maxsize=1):
        self.maxsize = maxsize
        self.items = []
        self.error = False

    def top(self):
        assert self.items, "!set_.empty()"
        return self.items[0]

    def insert(self, p):
        # std::set::insert: equivalent elements are rejected as duplicates
        for q in self.items:
            if not _pt_less(p, q) and not _pt_less(q, p):
                return
        self.items.append(p)
        self.items.sort(key=_PT_KEY)
        while len(self.items) > self.maxsize:
            self.items.pop()          # discardExtras(): drop the largest

    def set_error(self, flag):
        self.error = self.error or bool(flag)


class _Factory:
    """DesignPointFactory (spec_core §8.2).

    `create` ALWAYS allocates a fresh, unevaluated point and enqueues it for the
    driver (0x3cef6e / 0x3cf016), so the same `values` vector gets evaluated
    again every time it is created.  `Cache::hit` / `Cache::insert` have **no
    caller inside libalchemy.so** (spec_core §8.5), therefore a cache lookup
    here would suppress evaluations the native driver really performs (native
    BFGS rosen re-evaluates (15,0)/(17,0) as gradient probes, ev 9/10).
    """

    def __init__(self):
        self.cache = {}          # kept only so `fac.cache[...]` writes stay harmless

    def create(self, ix):
        # isCacheHit is always false in the native library: nothing calls hit()
        return _Pt(tuple(int(v) for v in ix), None, False)


def _params_dict(params):
    """Accept a plain dict or the framework's Alchemy::Params wrapper."""
    if params is None:
        return {}
    if isinstance(params, dict):
        return dict(params)
    for attr in ("as_dict", "to_dict"):
        if hasattr(params, attr):
            return dict(getattr(params, attr)())
    if hasattr(params, "items"):
        return dict(params.items())
    return {}


def _start_index(domain, start):
    n = [int(t) for t in domain.tunables]
    if start is None:
        ix = list(domain.center_index())
    elif isinstance(start, dict):
        ix = list(domain.to_index(start))
    else:
        ix = list(start)
    ix = [int(v) for v in ix]
    if len(ix) != len(n):
        raise ValueError("start has %d coordinates, domain has %d" % (len(ix), len(n)))
    if any(v < 0 or v >= n[i] for i, v in enumerate(ix)):
        ix = [int(v) for v in domain.clamp_index(ix)]   # boundary normalisation
    return tuple(ix)


def _costs(got, need, goal, domain):
    if got is None:
        if goal is None:
            raise RuntimeError("driver sent no costs and no goal was given")
        got = [float(goal(domain.from_index(list(p.ix)))) for p in need]
    got = list(got)
    if len(got) != len(need):
        raise ValueError("expected %d costs, got %d" % (len(need), len(got)))
    return got


# ---------------------------------------------------------------------------
#  ConjugateGradient -- Polak-Ribiere (algs/ConjugateGradient.cpp)
# ---------------------------------------------------------------------------
def run(domain, goal=None, start=None, params=None):
    """Ask/tell generator. Yields a batch of index vectors, receives their costs."""
    prm = _params_dict(params)
    nval = [int(t) for t in domain.tunables]
    dim = len(nval)
    max_iter = int(prm.get("max_iter", 200))                    # 0x309963 default 0xc8
    min_steps = int(prm.get("minimum_steps_for_brent", 30))     # 0x3088ed / 0x308fe6
    fail_limit = int(prm.get("fail_count_limit", 0))            # LineMinimize default 0x337422

    fac = _Factory()
    pop = _Pop(1)
    ref = [None]

    def _ask(points):
        need = [p for p in points if p.cost is None]
        if not need:
            return
        got = yield [list(p.ix) for p in need]
        for p, c in zip(need, _costs(got, need, goal, domain)):
            p.cost = float(c)
            fac.cache[p.ix] = p.cost

    # ---------------- LineMinimize (algs/LineMinimize.cpp) -----------------
    # Not re-implemented here any more: the canonical extraction
    # algorithms/LineMinimize.py is loaded from disk and driven as a nested
    # ask/tell generator.  The engine shares one Population and one
    # DesignPointFactory between the caller and the line search -- the caller
    # injects `population` (key @0x571619) and `vector` (@0x57167c) into the
    # nested LineMinimize Params -- so:
    #   * the base point is the population top   (LineMinimize::Start 0x3340c0)
    #   * every probe the line search evaluates is inserted into that shared
    #     population (Min_Brent_WaitPoint 0x336258 / Min_Exhaustive 0x33539f).
    def _line_minimize(dvec, directional, fail_cnt, minb):
        """Generator; returns the final base point (ShortCircuits::Return path)."""
        base = pop.top()                                   # 0x334162 population->top()
        prm = {"vector": [float(x) for x in dvec],
               "directional": bool(directional),
               "fail_count_limit": int(fail_cnt),
               "minimum_steps_for_brent": int(minb),
               # current_point is already evaluated -> Start does not re-ask it
               "start_cost": base.cost,
               # the population is a real LineMinimize hyper-parameter (the
               # callers own it; the line search inserts into it at the exact
               # instruction where the library does)
               "population": pop}
        lm = LineMinimize.run(domain, goal, start=list(base.ix), params=prm)
        try:
            batch = next(lm)
        except StopIteration as stop:                      # |vector| == 0 -> base
            return list(stop.value)
        while True:
            got = yield batch                 # driven by the caller, same order
            # No caller-side insert: Min_Exhaustive_Wait 0x33539f and
            # Min_Brent_WaitPoint 0x336258 are the only writers of the shared
            # population, and the Brent one sits *after* the fail-count test
            # (0x3361d2 / 0x33622a) -- a probe that trips it is evaluated and
            # then discarded without ever reaching the set.
            try:
                batch = lm.send([float(c) for c in got])
            except StopIteration as stop:
                return list(stop.value)

    # ---------------- Start / Start_WaitForStartingPoint -------------------
    cur = fac.create(_start_index(domain, start))
    if "start_cost" in prm:
        cur.cost = float(prm["start_cost"])
        fac.cache[cur.ix] = cur.cost
    if cur.cost is None:
        yield from _ask([cur])
    pop.insert(cur)
    ref[0] = pop.top()

    # ---------------- Start_HaveStartingPoint 0x3076d4 ----------------------
    pts = [None] * (dim * 2)
    g = [0.0] * dim                     # +0x88
    gold = [0.0] * dim                  # +0xa0
    dvec = [0.0] * dim                  # +0xb8
    # +0xd8: size dim (Start_HaveStartingPoint resizes it to numValues.size(),
    # 0x307857).  It is the *one-hot coordinate* vector that the directional=false
    # re-scan uses as its direction (LineMinimizeDone -> p["vector"] = state+0xd8,
    # 0x308ef2).  It is NOT dead code (spec_bfgs §5.5 is wrong on that point).
    mask = [0.0] * dim
    d0 = True                           # +0xd0 = 1
    it = 0                              # +0xd4 = 0
    f0 = False                          # +0xf0 = 0

    while True:
        # ---------------- ComputeGradient 0x30793c --------------------------
        need = [0] * (dim * 2)
        for side in (-1, 1):
            b = dim if side > 0 else 0
            for j in range(dim):
                pj = cur.ix[j] + side
                if pj != nval[j] and pj != -1:          # 0x307a3c / 0x307a54
                    need[b + j] = 1
        pts = [None] * (dim * 2)
        for side in (-1, 1):
            b = dim if side > 0 else 0
            for j in range(dim):
                if need[b + j]:
                    q = list(cur.ix)
                    q[j] = cur.ix[j] + side
                    pts[b + j] = fac.create(q)
        yield from _ask([p for p in pts if p is not None])

        # ---------------- WaitForGradientPoints 0x307c10 --------------------
        n = len(pts) // 2
        assert n == dim, "half == POINTS->tunables.size()"   # line 0x56
        best = float("inf")
        best_idx = 0
        f0 = False
        for j in range(n):
            dl = 0.0
            dr = 0.0
            if pts[j] is not None:
                dl = cur.cost - pts[j].cost
                if best > pts[j].cost:
                    # 0x307ea4: best_idx = j  (dimension, NOT n+j)
                    best, best_idx, f0 = pts[j].cost, j, True
            if pts[n + j] is not None:
                dr = pts[n + j].cost - cur.cost
                if best > pts[n + j].cost:
                    # 0x307fdd: best_idx = j as well (the sign is dropped)
                    best, best_idx, f0 = pts[n + j].cost, j, True
            g[j] = _div(dl + dr, TWO) * float(nval[j])           # 0x307ffd / 0x308087
            if dl < 0.0 and dr > 0.0:                            # 0x308093 / 0x3080a3
                g[j] = 0.0
            mask[j] = 0.0                                        # 0x30814e..0x308168
        if f0:
            assert best_idx < len(mask), "best_index<mask.size()"   # 0x3081a1
            mask[best_idx] = 1.0                                 # 0x3081df

        # ---- restart test + Polak-Ribiere direction (0x30821a .. 0x308734)
        if (it % dim) == 0 or d0:
            gold = g[:]                                          # 0x3082bb
            dvec = [-x for x in g]                               # 0x3082f5
        else:
            a = 0.0
            bsum = 0.0
            c = 0.0
            e_hs = 0.0
            for j in range(dim):
                a += g[j] * (g[j] - gold[j])                     # 0x308407
                bsum += g[j] * g[j]                              # 0x308467 (unused)
                c += gold[j] * gold[j]                           # 0x3084c7
                e_hs += dvec[j] * (g[j] - gold[j])               # 0x308560 (unused)
            if c == 0.0:                                         # 0x308584
                print("Finished! old gradient squared is equal to 0.")
                return list(pop.top().ix)                        # 0x3085e0 throw Return
            beta = _div(a, c)                                    # 0x3085ea
            if beta < 0.0:                                       # 0x3085f8
                beta = 0.0
                it = 1                                           # 0x30860f
            gold = g[:]                                          # 0x30868b
            dvec = [dvec[j] * beta - g[j] for j in range(dim)]   # 0x3086ee
        d0 = False                                               # 0x308734
        it += 1                                                  # 0x308752

        yield from _line_minimize(dvec, True, fail_limit, min_steps)

        # ---------------- LineMinimizeDone 0x308c6a -------------------------
        while True:
            top_before = pop.top().cost
            if f0:                                               # 0x308cc3
                for p in pts:
                    if p is not None:
                        pop.insert(p)                            # 0x308d56
            if cur.cost == pop.top().cost:                       # 0x308df5
                return list(pop.top().ix)
            if top_before > pop.top().cost and f0:               # 0x308e27
                f0 = False                                       # 0x309066
                d0 = True                                        # 0x309074
                # 0x308ef2 `lea rbx,[rax+0xd8]`: the re-scan direction is the
                # one-hot `mask` vector, NOT d -- the 0xd8 vector is read here
                # even though nothing else consumes it.
                yield from _line_minimize(mask, False, fail_limit, min_steps)
                continue                                         # re-enters LineMinimizeDone
            sumprev = 0.0
            newd = [0.0] * dim
            for i in range(dim):
                sumprev += dvec[i] * dvec[i]                     # 0x30928a
                newd[i] = float(pop.top().ix[i] - cur.ix[i])     # 0x3092fd
            sumnew = 0.0
            for i in range(dim):
                sumnew += newd[i] * newd[i]                      # 0x30938d
            assert 0.0 < sumnew, "0.0 < sum"                     # 0x3093c9
            k = _sqrt(_div(sumprev, sumnew))                     # 0x30940a
            dvec = [x * k for x in newd]                         # 0x309438
            cur = pop.top()                                      # 0x30947e
            break
# ---------------------------------------------------------------------------
#  self-verification: 2-D quadratic on a 41-level lattice
# ---------------------------------------------------------------------------
class _Quad2D:
    """41 levels per axis; physical x = -2.0 + 0.1*i  (i = 0..40)."""

    tunables = [41, 41]

    def from_index(self, ix):
        return {"x": -2.0 + 0.1 * int(ix[0]), "y": -2.0 + 0.1 * int(ix[1])}

    def to_index(self, point):
        return [int(round((point["x"] + 2.0) / 0.1)),
                int(round((point["y"] + 2.0) / 0.1))]

    def center_index(self):
        return [20, 20]

    def clamp_index(self, ix):
        return [min(40, max(0, int(v))) for v in ix]


def _quad_cost(point):
    return (point["x"] - 0.4) ** 2 + 2.0 * (point["y"] + 0.7) ** 2


def _drive(gen, goal, domain, limit=20000):
    nfev = 0
    batches = 0
    try:
        batch = next(gen)
    except StopIteration as stop:
        return stop.value, nfev, batches
    while True:
        costs = [goal(domain.from_index(ix)) for ix in batch]
        nfev += len(batch)
        batches += 1
        if batches > limit:
            raise RuntimeError("batch limit exceeded")
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            return stop.value, nfev, batches




if __name__ == "__main__":
    dom = _Quad2D()
    best, nfev, batches = _drive(run(dom, _quad_cost), _quad_cost, dom)
    phys = dom.from_index(best)
    print("final point :", best, phys)
    print("cost        :", _quad_cost(phys))
    print("nfev        :", nfev, "in", batches, "batches")

