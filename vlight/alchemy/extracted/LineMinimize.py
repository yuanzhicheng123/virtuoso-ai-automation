"""LineMinimize -- extracted from libalchemy.so (Cadence, IC23.1-64b.43).

Reference: Alchemy::LineMinimize, algs/LineMinimize.cpp.
  states      : Init -> Start -> {Min_Exhaustive -> Min_Exhaustive_Wait}   (len(line) < minimum_steps_for_brent)
              -> {Min_Brent -> Min_Brent_PickPoint <-> Min_Brent_WaitPoint}
  hyperparams : current_point, population, vector,
                spec_index(-1 @0x33703d), directional(false @0x337231),
                fail_count_limit(0 @0x337422), minimum_steps_for_brent(30 @0x337616)
  constants   : CGOLD @0x5691f0 (symbol @0x568a20), 0.5 @0x5691c8, 1.0 @0x5691d0,
                1.5 @0x5691d8, -0.0 @0x5691e0, -1.0 @0x5691f8

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
#  constants (addresses are file == VMA addresses inside libalchemy.so)
# ---------------------------------------------------------------------------
ONE = 1.0                     # 0x563e80  00 00 00 00 00 00 f0 3f
NEG_ZERO = -0.0               # 0x563ea0  00 00 00 00 00 00 00 80
FABS_MASK = 0x7FFFFFFFFFFFFFFF  # 0x563e90  ff..7f  (andpd mask)
BFGS_GUARD = 1e-10            # 0x563eb0  bb bd d7 d9 df 7c db 3d
TWO = 2.0                     # 0x564b30
CGOLD = 0.381966   # .rodata 0x568a20/0x5691f0（十进制字面量，不是黄金比）    # 0x568a20 (symbol) / 0x5691f0 (copy)
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
    """DesignPointFactory + Cache (one evaluated table, keyed by values)."""

    def __init__(self):
        self.cache = {}

    def create(self, ix):
        key = tuple(int(v) for v in ix)
        if key in self.cache:
            return _Pt(key, self.cache[key], True)     # isCacheHit = true
        return _Pt(key, None, False)


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
#  LineMinimize -- exhaustive scan or Brent + golden section
#  (algs/LineMinimize.cpp); the sub-machine shared by BFGS / CG / QN
# ---------------------------------------------------------------------------
def run(domain, goal=None, start=None, params=None):
    """Ask/tell generator. Yields a batch of index vectors, receives their costs.

    params (LineMinimize::Data::Data @0x3368e8):
      vector                   list[float], direction.  Empty/absent => |d| == 0 => the
                               machine returns the base point immediately (0x3345dc).
      directional              bool,  default False   (0x337231)
      spec_index               int,   default -1      (0x33703d)  [single-cost model: ignored]
      fail_count_limit         int,   default 0       (0x337422; 0 disables the test)
      minimum_steps_for_brent  int,   default 30      (0x337616)
      population               Population, default a private Population(1).
                               `population` is a real Data key: the callers pass their
                               own population (`Powell::Loop::run` writes it into the
                               nested Params @0x392de6; BFGS/CG/QN build it in
                               `Data::Data` @0x300f4f and forward it).  The line
                               search is the *only* writer of that population:
                               Start seeds the base, Min_Exhaustive_Wait inserts
                               every scanned point (0x33539f), Min_Brent_WaitPoint
                               inserts the probe at 0x336258 -- i.e. **after** the
                               fail-count test that `throw`s Return at 0x33622a.
                               A probe rejected by that test is evaluated but never
                               inserted, so a caller-side "insert everything I
                               evaluated" loop diverges from the library.
    """
    prm = _params_dict(params)
    nval = [int(t) for t in domain.tunables]
    dim = len(nval)
    directional = bool(prm.get("directional", False))
    fail_limit = int(prm.get("fail_count_limit", 0))
    minb = int(prm.get("minimum_steps_for_brent", 30))
    _spec_index = int(prm.get("spec_index", -1))     # accepted, single-cost model

    fac = _Factory()
    pop = prm.get("population")
    if pop is None:
        pop = _Pop(1)                                # standalone / unconverted callers
    ref = [None]

    def _ask(points):
        need = [p for p in points if p.cost is None]
        if not need:
            return
        got = yield [list(p.ix) for p in need]
        for p, c in zip(need, _costs(got, need, goal, domain)):
            p.cost = float(c)
            fac.cache[p.ix] = p.cost

    def _line_minimize(dvec, directional, fail_cnt, minb):
        """Generator; returns the final base point (ShortCircuits::Return path)."""
        base = pop.top()                                   # 0x334162 population->top()
        line = list(base.ix)                               # line(+0x40) = base->parameters
        d = [float(x) for x in dvec]
        if len(d) != dim:
            raise ValueError("vector has %d entries, dim is %d" % (len(d), dim))
        if directional:                                    # 0x3343b9
            for i in range(dim):
                if line[i] == 0:
                    if 0.0 > d[i]:
                        d[i] = 0.0
                elif line[i] + 1 == nval[i] and d[i] > 0.0:
                    d[i] = 0.0
        sumsq = 0.0
        for x in d:
            sumsq += x * x
        norm = _sqrt(sumsq)                                # 0x3345a3
        if norm == 0.0:
            return base                                    # 0x3345dc throw Return
        d = [_div(x, norm) for x in d]                     # 0x33463c

        if not directional:                                # 0x3346a1
            min_step = -1
            bind = 0
            for i in range(dim):
                if d[i] > 0.0:
                    st = _cvtt(_div(line[i] + HALF, d[i]))          # 0x33474d..0x3347a0
                    if _u32(st) < _u32(min_step):
                        min_step, bind = st, i
                elif 0.0 > d[i]:
                    q = _div(line[i] - nval[i] + HALF, d[i])        # 0x334850..0x3348b4
                    st = _cvtt(math.ceil(q) - 1.0)
                    assert st != -1, "step != unsigned(-1)"
                    if _u32(st) < _u32(min_step):
                        min_step, bind = st, i
            start = -min_step                                       # 0x33490f
            assert float(min_step) == -float(start), "double(min_step) == -starting_step"
            for i in range(dim):
                pi = _cvtt(line[i] + start * d[i] + HALF)            # 0x334a1f
                assert nval[i] > pi, "n_i > pi"
                if i == bind:
                    pi2 = _cvtt(line[i] + (start - 1.5) * d[i] + 1.5) - 1   # 0x334afc
                    assert not (_u32(pi2) < _u32(nval[i])), \
                        "!(unsigned(pi2) < unsigned(n_i))"
        else:
            start = 0                                                # 0x334b8f

        # ---- generate the whole integer line (0x334bc4..0x334dfa)
        pts_line = []
        prev = None
        step = start
        while True:
            p = [_cvtt(line[j] + step * d[j] + HALF) for j in range(dim)]
            ok = True
            for j in range(dim):
                if not (p[j] < nval[j] and p[j] >= 0):
                    ok = False
                    break
            if not ok:
                break
            if step == start or p != prev:
                pts_line.append(p)
                prev = p
            step += 1

        if len(pts_line) < minb:                       # 0x334dfa -> Min_Exhaustive
            plist = [fac.create(p) for p in pts_line]
            yield from _ask(plist)
            best = base.cost
            bestp = base
            for p in plist:                            # Min_Exhaustive_Wait 0x3351bc
                pop.insert(p)                          # 0x33539f
                if p.err:
                    pop.set_error(True)
                if p.cost < best:                      # 0x33541d
                    best, bestp = p.cost, p
            ref[0] = bestp                             # 0x3354c6 setReferencePoint
            return bestp                               # throw ShortCircuits::Return

        # ---- Brent (0x335520 .. 0x336510)
        # three distinct state fields: +0xf0 = bracket lower (u), +0xf4 = bracket upper (N),
        # +0xf8 = index of the last probe.  PickPoint indexes the line with +0xf8, not with u.
        lo = -1                                                 # 0x335538  (+0xf0)
        N = len(pts_line)                                       # 0x33555a  (+0xf4)
        idx = None
        for i, p in enumerate(pts_line):                        # 0x3355fd..0x335672
            if p == list(base.ix):
                idx = i
                break
        if idx is None:
            idx = N                                             # find() returned end()
        if directional:
            assert idx == 0, "!*directional || i == line.begin()"
        fbase = base.cost
        xwv = [(fbase, idx), (fbase, idx), (fbase, idx)]        # 3 inserts of the same pair
        costmap = {}                                            # +0xc0 (write-only)
        improved = False                                        # +0x60
        fails = 0                                               # +0x5c
        while True:
            s = N - lo                                          # 0x335886  sub edx,eax (+0xf4 - +0xf0)
            if s == 0 or s == 1:
                raise AssertionError("0")                       # algs/LineMinimize.cpp:0xf1 / 0xf2
            if s == 2:
                return base                                     # 0x33591f throw Return
            if s == 3:
                pu = lo + (2 if xwv[0][1] == lo + 1 else 1)     # 0x33596a..0x335985 -> +0xf8
            else:
                assert len(xwv) == 3, "xwv.size() == 3"
                fx, xi = xwv[0]                                 # begin() = cheapest
                fw, wi = xwv[1]
                fv, vi = xwv[2]
                r = (fx - fv) * (xi - wi)                       # 0x335acd
                q = (fx - fw) * (xi - vi)                       # 0x335b40
                p = (xi - vi) * q - (xi - wi) * r               # 0x335bb3
                q = TWO * (q - r)                               # 0x335bca
                if q < 0.0:
                    q = -q                                      # 0x335bef
                else:
                    p = -p                                      # 0x335bfb
                if p > (lo + 1 - xi) * q and (N - 1 - xi) * q > p:   # 0x335c41 / 0x335c73
                    dd = _div(p, q)                             # 0x335c8f
                else:
                    dd = CGOLD * ((lo - xi) if xi >= HALF * (lo + N) else (N - xi))
                un = _floori(xi + dd + HALF)                    # 0x335d66 floor@plt
                if un == xi:                                    # 0x335d80
                    dd = NEG_ONE if dd < 0.0 else ONE           # 0x5691f8 / 0x5691d0
                pu = _floori(xi + dd + HALF)                    # 0x335dd1 -> +0xf8 (@0x335de1)
            assert 0 <= pu < N, "u >= 0 && u < (int)line.size()"    # 0x335df5 / 0x335e1d
            probe = fac.create(pts_line[pu])                    # 0x335e93
            yield from _ask([probe])
            # ---- Min_Brent_WaitPoint 0x335fb4
            f_best = base.cost                                  # 0x33602e
            f_u = probe.cost                                    # 0x3360af
            costmap[pu] = f_u                                   # 0x33614b (key = +0xf8)
            if (not improved) and f_best > f_u:                 # 0x33616c
                improved = True
            if improved and f_u >= f_best:                      # 0x336196
                fails += 1
            if fail_cnt != 0 and fails > fail_cnt:              # 0x3361d2
                return base                                     # 0x33622a throw Return
            pop.insert(probe)                                   # 0x336258
            if f_best > f_u:                                    # 0x33626b
                base, f_best = probe, f_u
            if probe.err:
                pop.set_error(True)
            ref[0] = base                                       # 0x336322
            nb = pu                                             # 0x33633e  (read +0xf8)
            if xwv[0][0] > f_u:                                 # 0x33636f seta
                nb = xwv[0][1]
            xwv.append((f_u, pu))                               # multimap::insert
            xwv.sort(key=lambda t: t[0])                        # stable => upper_bound for ties
            xwv.pop()                                           # erase(--end())
            assert nb != xwv[0][1], "new_bracket != xwv.begin()->second"
            if nb > xwv[0][1]:                                  # 0x336498 setg
                N = nb                                          # +0xf4
            else:
                lo = nb                                         # +0xf0   (0x3364bb)

    # ---------------- Start 0x3340c0 ---------------------------------------
    cur = fac.create(_start_index(domain, start))
    if "start_cost" in prm:
        cur.cost = float(prm["start_cost"])
        fac.cache[cur.ix] = cur.cost
    if cur.cost is None:                 # current_point is not evaluated yet
        yield from _ask([cur])
    pop.insert(cur)                      # population is seeded with the start point
    ref[0] = pop.top()

    dvec = [float(x) for x in (prm.get("vector") or [])]
    if not dvec:                         # default: empty vector -> norm == 0
        return list(cur.ix)              # 0x3345dc throw Return
    if len(dvec) != dim:
        raise ValueError("vector has %d entries, dim is %d" % (len(dvec), dim))
    base = yield from _line_minimize(dvec, directional, fail_limit, minb)
    return list(base.ix)
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
    # the line through the lattice centre (20,20) with direction (4,-7) contains the exact
    # lattice optimum (24,13); directional=False scans the whole feasible line.
    prm = {"vector": [4.0, -7.0]}
    best, nfev, batches = _drive(run(dom, _quad_cost, params=prm), _quad_cost, dom)
    phys = dom.from_index(best)
    print("final point :", best, phys)
    print("cost        :", _quad_cost(phys))
    print("nfev        :", nfev, "in", batches, "batches (literal defaults: min_steps=30 -> Brent)")
    prm2 = {"vector": [4.0, -7.0], "minimum_steps_for_brent": 1000}
    best2, nfev2, batches2 = _drive(run(dom, _quad_cost, params=prm2), _quad_cost, dom)
    phys2 = dom.from_index(best2)
    print("exhaustive  :", best2, phys2, "cost", _quad_cost(phys2),
          "nfev", nfev2, "(%d batches, min_steps=1000)" % batches2)

