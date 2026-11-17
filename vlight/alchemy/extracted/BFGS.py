"""BFGS -- extracted from libalchemy.so (Cadence, IC23.1-64b.43).

Reference: Alchemy::BFGS, algs/BFGS.cpp.
  states       : Init -> Start -> {Start_HaveStartingPoint | Start_WaitForStartingPoint}
                 -> ComputeGradient -> WaitForGradientPoints
                 -> Call<LineSearchDone>(LineMinimize::Init) -> LineSearchDone -> ...
  hyperparams  : current_point      (shared_ptr<DesignPoint>, default: new empty point)
                 double_side_gradient (shared_ptr<bool>,     default: true, write @0x300d51)
                 population         (shared_ptr<Population>, default: new Population(1))
                 caller-hardcoded   : directional=true  @0x3000ae
                                      fail_count_limit=3 @0x30012d
  constants    : 1.0 @0x563e80, -0.0 @0x563ea0, fabs mask @0x563e90, 1e-10 @0x563eb0
                 (+ LineMinimize: CGOLD @0x568a20/@0x5691f0, 0.5 @0x5691c8, 1.0 @0x5691d0,
                  1.5 @0x5691d8, -0.0 @0x5691e0, -1.0 @0x5691f8)

Ask/tell protocol (identical in all four extracted files):
    g = run(domain, goal, start=None, params=None)
    batch = next(g)              # list of index vectors (list[int]) to evaluate
    batch = g.send([c, ...])     # costs, same order/length; returns the next batch
    ...                          # StopIteration.value == best index vector
If send() is given None (i.e. plain next()), the cost of each point is computed
from `goal(domain.from_index(ix))`, so the module runs without a driver.

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
#  BFGS
# ---------------------------------------------------------------------------
def run(domain, goal=None, start=None, params=None):
    """Ask/tell generator. Yields a batch of index vectors, receives their costs."""
    prm = _params_dict(params)
    nval = [int(t) for t in domain.tunables]
    dim = len(nval)
    double_side = bool(prm.get("double_side_gradient", True))   # 0x300d51 default 1
    fail_limit = int(prm.get("fail_count_limit", 3))            # 0x30012d (hard-coded)
    min_steps = int(prm.get("minimum_steps_for_brent", 30))     # LineMinimize default 0x337616

    fac = _Factory()
    pop = _Pop(1)
    ref = [None]                 # factory->setReferencePoint(...)  (write-only here)

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
    if "start_cost" in prm:                       # pre-evaluated current_point
        cur.cost = float(prm["start_cost"])
        fac.cache[cur.ix] = cur.cost
    if cur.cost is None:                          # Start_WaitForStartingPoint 0x2fe660
        yield from _ask([cur])
    pop.insert(cur)                               # 0x2fe7f6
    ref[0] = pop.top()                            # 0x2fe854

    # ---------------- Start_HaveStartingPoint 0x2fe784 ----------------------
    pts = [None] * (dim * 2)                      # +0x60 resize(2*dim)
    g = [0.0] * dim                               # +0xa0
    gold = [0.0] * dim                            # +0x88  (shared with y)
    dvec = [0.0] * dim                            # +0xb8  (shared with s)
    H = [[ONE if i == j else 0.0 for j in range(dim)] for i in range(dim)]  # +0x48
    no_hess_yet = True                            # +0xd0 = 1 @0x3009a4
    any_nonzero = False                           # +0xd1 = 0 @0x3009ab
    coord_mode = False                            # +0xd2 = 0 @0x3009b6
    prev = None                                   # +0x78 prev_point

    while True:
        # ---------------- ComputeGradient 0x2feb1e --------------------------
        need = [0] * (dim * 2)
        for side in (-1, 1):
            b = dim if side > 0 else 0
            for j in range(dim):
                pj = cur.ix[j] + side
                if pj != nval[j] and pj != -1:          # 0x2fec65 / 0x2fec80
                    need[b + j] = 1
        if not double_side:                              # 0x2fed04
            for j in range(dim):
                if need[j] and need[dim + j]:
                    need[j] = 0                          # keep the forward side only
        pts = [None] * (dim * 2)
        for side in (-1, 1):
            b = dim if side > 0 else 0
            for j in range(dim):
                if need[b + j]:
                    q = list(cur.ix)
                    q[j] = cur.ix[j] + side
                    pts[b + j] = fac.create(q)           # 0x2fee7d
        yield from _ask([p for p in pts if p is not None])

        # ---------------- WaitForGradientPoints 0x2ff044 --------------------
        n = len(pts) // 2
        assert n == dim, "half == dim"                   # algs/BFGS.cpp:99
        any_nonzero = False
        gmax = 0.0
        imax = 0
        for j in range(n):
            dl = 0.0
            dr = 0.0
            cnt = 0
            if pts[j] is not None:                       # left  (-1): points[j]
                dl = cur.cost - pts[j].cost
                cnt += 1
            if pts[n + j] is not None:                   # right (+1): points[n+j]
                dr = pts[n + j].cost - cur.cost
                cnt += 1
            if cnt == 0 or (dl <= 0.0 and dr >= 0.0):    # discrete local minimum
                g[j] = 0.0                               # 0x2ff324
            else:
                any_nonzero = True                       # 0x2ff334
                if dr < 0.0:
                    g[j] = dr                            # 0x2ff378
                elif dl > 0.0:
                    g[j] = dl                            # 0x2ff3c2
                else:
                    g[j] = _div(dl + dr, float(cnt))     # 0x2ff3d7
            if abs(g[j]) > gmax:                         # 0x2ff42d andpd mask
                imax, gmax = j, abs(g[j])
        if coord_mode:                                   # 0x2ff48f
            for j in range(dim):
                if j != imax:
                    g[j] = 0.0

        # ---- direction update (0x2ff4f0 .. 0x2fff6b)
        if no_hess_yet:                                  # 0x2ff4f0
            H = [[ONE if i == j else 0.0 for j in range(dim)] for i in range(dim)]
            dvec = [-x for x in g]                       # 0x2ff63f
        else:
            s = [float(cur.ix[j] - prev.ix[j]) for j in range(dim)]   # into +0xb8
            y = [g[j] - gold[j] for j in range(dim)]                  # 0x2ff74b
            gold = y[:]                                  # +0x88 is overwritten by y
            Hy = [sum(H[i][j] * y[j] for j in range(dim)) for i in range(dim)]
            a1 = sum(s[j] * y[j] for j in range(dim))            # +-0x88
            a2 = sum(y[j] * Hy[j] for j in range(dim))           # -0x90
            a3 = sum(y[j] * y[j] for j in range(dim))            # -0x98
            a4 = sum(s[j] * s[j] for j in range(dim))            # -0xa0
            if a1 > _sqrt(BFGS_GUARD * a3 * a4):                 # 0x2ffac6..0x2ffaf8
                rho = _div(ONE, a1)                              # 0x2ffb06
                inv = _div(ONE, a2)                              # 0x2ffb1e
                uu = [s[j] * rho - Hy[j] * inv for j in range(dim)]
                for i in range(dim):
                    # 0x2ffc1c `mov [rbp-0xc0],rax` seeds the inner loop with j = i:
                    # the update walks the upper triangle and mirrors H[j][i] = H[i][j]
                    # (0x2ffe3f).  Starting j at 0 double-adds every off-diagonal term.
                    for j in range(i, dim):
                        H[i][j] += (s[i] * s[j] * rho
                                    - Hy[i] * Hy[j] * inv
                                    + a2 * uu[i] * uu[j])    # 0x2ffdc3
                        H[j][i] = H[i][j]                    # 0x2ffe3f mirror
            dvec = [0.0] * dim
            for i in range(dim):
                acc = 0.0
                for j in range(dim):
                    acc -= H[i][j] * g[j]                        # 0x2fff6b
                dvec[i] = acc

        # ---- Call<LineSearchDone>(LineMinimize::Init)
        yield from _line_minimize(dvec, True, fail_limit, min_steps)

        # ---- LineSearchDone 0x3004c6
        f = cur.cost
        top = pop.top().cost
        if f == top:                                     # 0x30053a
            if not any_nonzero:
                return list(pop.top().ix)                # 0x30058b throw Return
            if no_hess_yet and coord_mode:
                return list(pop.top().ix)
            if no_hess_yet:
                coord_mode = True                        # 0x3005a3
            no_hess_yet = True                           # 0x3005ae
        else:
            coord_mode = False                           # 0x3005bb
            no_hess_yet = False                          # 0x3005c6
            gold = g[:]                                  # 0x3005d5..0x30062a
        prev = cur                                       # 0x30064a
        cur = pop.top()                                  # 0x30067c


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
