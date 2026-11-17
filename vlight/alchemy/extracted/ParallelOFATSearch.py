"""ParallelOFATSearch — extracted from libalchemy.so (Alchemy::ParallelOFATSearch,
algs/ParallelOFATSearch.cpp, visible name "ParallelOFATSearch").

states:
    Init            0x36685c -> Start
    Start           0x3669f2 -> OFATDone
    OFATDone        0x367250 -> SubmitBegin
    SubmitBegin     0x367b20 -> Fork<ForkedSubmitDone>(my_pts.size()) | PointReduction | SubmitDone
    ForkedSubmitDone 0x36c108 -> (one fork per point) insert + JOIN -> BrentPowellBegin
    SubmitDone      0x36c376 -> BrentPowellBegin
    PointReduction  0x36b36c -> Fork<ForkedSubmitDone>(my_pts.size())       (spec merge)
    BrentPowellBegin 0x36c85c -> Fork<BrentPowellSubmit>(num_specs())
    BrentPowellSubmit 0x36cbc8 -> Call<BrentPowellDone>( Powell::Init )
    BrentPowellDone 0x36d7be -> Fork<BrentPowellSubmit>(n_to_rerun) | end ("No spec needs to be rerun.")

hyperparams (Alchemy::ParallelOFATSearch::Data 0x36ef16):
    current_point      shared_ptr<DesignPoint>   --      base point
    fractional_factor  int                       5       0x36f239
    stop_on_error      bool                      false   0x36f42d
    debug_level        int                       0       0x36f61e
    accuracy_level     int                       0       0x36f812
    population         shared_ptr<Population>    --

Batching rule (the whole point of this class):
  * design phase  : one fork per point   (Fork<ForkedSubmitDone>(my_pts.size())  0x36ad41 / 0x36bf0e)
  * merged specs  : specs whose dependency dim set and (best,worst) bound pairs agree
                    -- straight or REVERSED -- contribute no new points (0x36baa7 / 0x36bbab)
  * refinement    : one fork per spec     (Fork<BrentPowellSubmit>(num_specs())   0x36cb18)
                    and a fixpoint loop re-running every spec that another spec's
                    improved point can help (0x36e452), until "No spec needs to be rerun."

.rodata doubles used by this class: DBL_MAX 0x56efa0, LLONG_MAX 0x56efb0,
1e-6 0x56efc0, -1e-6 0x56efc8.
"""

# --- .rodata constants ------------------------------------------------------
KEY_CURRENT_POINT  = 0x56de93   # 'current_point'
KEY_FRACTIONAL     = 0x56dea1   # 'fractional_factor'
KEY_STOP_ON_ERROR  = 0x56deb3   # 'stop_on_error'
KEY_DEBUG_LEVEL    = 0x56dec1   # 'debug_level'
KEY_ACCURACY_LEVEL = 0x56decd   # 'accuracy_level'
KEY_POPULATION     = 0x56dedc   # 'population'
KEY_SPEC_INDEX     = 0x56e391   # 'spec_index'
KEY_ACCURATE_MODE  = 0x56e39c   # 'accurate_mode'

STR_START          = 0x56dee8   # 'Starting parallel OFAT search algorithm.  '
STR_ACCURACY       = 0x56df13   # 'Accuracy level:'
STR_NUM_TUNABLES   = 0x56df23   # 'Num tunables:'
STR_NUM_VALUES     = 0x56df31   # ', num values:'
STR_FF_FACTOR      = 0x56df3f   # 'Fractional factor:'
STR_FF_DESIGN      = 0x56dfdb   # 'FF design:'
STR_DIMORDER       = 0x56dfe6   # 'dimOrder '
STR_COST           = 0x56dff9   # 'cost:'
STR_NUMVALS_FACT   = 0x56e001   # 'num values for factorial: '
STR_FF_POINTS      = 0x56e01c   # 'fractional factorial points:'
STR_SWEEPCOST      = 0x56e039   # 'sweepCost:'
STR_BESTVALUES     = 0x56e044   # 'bestValues '
STR_WORSTVALUES    = 0x56e050   # 'worstValues '
STR_ISCONSTANT     = 0x56e05d   # 'isConstant '
STR_ISCOMPLEX      = 0x56e069   # 'isComplex '
STR_SUBMITTING     = 0x56e074   # 'Submitting points:'
STR_FFIDX          = 0x56e126   # 'Index:'
STR_ADD3           = 0x56e16c   # 'Additional pts for level 3:'
STR_TOTAL          = 0x56e1ac   # 'Total points:'
STR_MY_PTS         = 0x56e1bd   # 'my_pts.size()>0'                (line 599)
STR_SPEC_SORTED    = 0x56e1cd   # 'Spec sorted by complexity:'
STR_SPEC_MERGE     = 0x56e22b   # ' can be merged with spec '
STR_FINISH_POINT   = 0x56e2d7   # 'Finishing point '
STR_ALL_FINISHED   = 0x56e301   # 'All points for index:'
STR_PERFORM_BP     = 0x56e3b0   # 'Performing Brent-Powell for spec '
STR_FINISH_BP      = 0x56e3f0   # 'Finishing Brent-Powell for spec '
STR_CHECK_RERUN    = 0x56e418   # 'Optimization for all specs finished, checking if any optimization needs to be rerun...'
STR_FOUND_BETTER   = 0x56e47b   # ', found better point id:'
STR_RERUN_LIST     = 0x56e498   # 'Rerunning optimization for the following specs:'
STR_NO_RERUN       = 0x56e518   # 'No spec needs to be rerun.'

DBL_MAX_C          = 0x56efa0   # 1.7976931348623157e+308
LLONG_MAX_C        = 0x56efb0   # 0x7fffffffffffffff
EPS_C              = 0x56efc0   # 1e-06
NEG_EPS_C          = 0x56efc8   # -1e-06

TI_RETURN          = 0x6a8a78
DEFAULT_FF         = 5          # 0x36f239


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
    """Driver call #0 -- the start point, evaluated before the algorithm runs."""
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


def _rank(pop, goal):
    """cost ordering (evil_ptr_less), best first."""
    return sorted(pop, key=lambda t: t[1],
                  reverse=bool(getattr(goal, "maximize", False)))


def get_num_values_for_mixed_factorial(tun, complex_dims):
    """PointSet::getNumValuesForMixedFactorial 0x3cbf0c.

    out.assign(n, 2)  0x3cbf38
    for i: if isComplexSweep(dim i) out[i] = dim.size()   0x3cbf7f / 0x3cbfbc
    """
    return [tun[i] if complex_dims[i] else 2 for i in range(len(tun))]


def mixed_fractional_factorial_designs(n_factors, n_extra, levels):
    """PointSet::mixedFractionalFactorialDesigns 0x3cb7a9.

    assert(nFactors > nExtra)                        0x3cb7d7
    k = nFactors - nExtra ; assert(k <= levels.size) 0x3cb81a
    N = prod(levels[0..k-1])                         0x3cb871  (imul loop)
    rows: mixed-radix digits of the row index in levels[0..k-1] for columns < k,
          generator columns k..n-1 built from the base digits (same pair walk as
          fractionalFactorialDesigns).  [unproven: the exact pair walk of the
          generator columns for the mixed radix case]
    """
    assert n_factors > n_extra
    k = n_factors - n_extra
    assert k <= len(levels)
    N = 1
    for i in range(k):
        N *= levels[i]
    rows = []
    for row in range(N):
        d = [0] * n_factors
        r = row
        for i in range(k):
            d[i] = r % levels[i]
            r //= levels[i]
        for i in range(k, n_factors):
            d[i] = (d[0] + d[i - k + 1]) % levels[i] if levels[i] else 0    # [unproven]
        rows.append(d)
    return rows


def run(domain, goal, start=None, params=None):
    """Parallel OFAT search; return the final ranking (best first).

    Yielded batch: the OFAT screen (one batch per tunable), then -- only when the caller
    supplies a spec table -- the whole design (one fork per point), the merged-spec batch
    and one batch per refinement round.
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)
    if start is None:
        start = [(t - 1) // 2 for t in tun]
    origin = list(start)
    ff = int(P.get("fractional_factor", DEFAULT_FF))
    stop_on_error = bool(P.get("stop_on_error", False))
    debug_level = int(P.get("debug_level", 0))
    accuracy_level = int(P.get("accuracy_level", 0))
    # The spec table is NOT part of the ask/tell driver interface (FunctionOptimizer::run
    # only forwards name / cost_fn / start / tunable_vals), so a bare run has no specs.
    specs = P.get("specs") or []
    if debug_level:
        print(STR_START)
        print(STR_ACCURACY, accuracy_level, STR_NUM_TUNABLES, n, STR_NUM_VALUES, tun)
        print(STR_FF_FACTOR, ff)

    pop = []                                                    # (index_vector, cost)

    # Driver call #0 (FunctionOptimizer::run 0x3ce70a): the start point is evaluated
    # before Start::run @0x3669f2 builds the SensitivityOFAT sub-run.
    probe = yield [domain.from_index(_driver_start(domain, start))]
    base_cost = float(probe[0])
    pop.append((list(origin), base_cost))

    # ---- OFAT screen: Start @0x3669f2 wraps SensitivityOFAT::Init (call 0x2c8b70 at
    # 0x366f8e) after injecting `use_OFAT` = 1 (key 0x56df52) and `allPoints` = 1
    # (key 0x56df5b), exactly like the serial OFATSearch (0x35799a / 0x357a13).
    # Split::run @0x3b6cf2 recurses as FORKIDX 0 (0x3b6e0f / 0x3b6e42), Fork runs its
    # children ascending (0x333777) -> tunable order n-1 .. 0.
    all_points = bool(P.get("allPoints", True))
    flags = list(P.get("modelParamFlags") or [False] * n)
    steps = P.get("design_steps") or [(1, 1)] * n
    for t in range(n - 1, -1, -1):
        L = tun[t] if (all_points or flags[t]) else 3
        pts = []
        for k in range(1, L):
            if all_points or flags[t]:
                level = k if k > origin[t] else k - 1            # 0x3b7560 / 0x3b7573
            else:
                level = origin[t] - steps[t][0] if k == 1 else origin[t] + steps[t][1]
            if level != origin[t] and 0 <= level < tun[t]:
                pt = list(origin)
                pt[t] = level
                pts.append(pt)
        if pts:
            costs = yield pts
            for pt, c in zip(pts, costs):
                pop.append((pt, float(c)))
            if stop_on_error and any(c != c for c in costs):
                return [p for p, _ in _rank(pop, goal)]

    n_specs = len(specs)
    if n_specs <= 0:
        # No spec table -> SubmitBegin has nothing to rank and BrentPowellBegin would
        # assert num_specs()>0 (0x36cac3).  The run therefore ends after the screen:
        # measured `harness ParallelOFATSearch 4 21 600 0 10 0 0 0 0` = 81 evaluations
        # (start + 4x20), `... 2 21 ...` = 41 (start + 2x20).
        return [p for p, _ in _rank(pop, goal)]

    # ---- SubmitBegin 0x367b20 : rank factors (multimap<double,int>) ---------
    keys = []
    for i in range(n):                                          # 0x367f5b / 0x368424
        best = base_cost
        for pt, c in pop:
            if pt[i] != origin[i] and _better(goal, c, best):
                best = c
        keys.append((best, i))
    keys.sort()
    order = [i for _, i in keys]                                # 0x3686b0 walk

    complex_dims = [any(s["is_complex"][i] for s in specs) for i in range(n)]
    levels = get_num_values_for_mixed_factorial(tun, complex_dims)   # 0x3688e0
    n_extra = n - ff if n > ff else 0
    rows = mixed_fractional_factorial_designs(n, n_extra, levels)    # 0x368a4b
    if debug_level:
        print(STR_FF_DESIGN)
        print(STR_NUMVALS_FACT, levels)
        print(STR_FF_POINTS, len(rows))

    # per-spec value table: ff_values()[spec][point][tunable]
    ff_values = []
    for s in specs:
        table = []
        for row in rows:
            v = [0] * n
            for i in range(n):
                fi = order[i]
                lv = row[i]
                # each spec orients its own grid: level 0 -> that spec's best bound,
                # the last level -> its worst bound (bestValues()/worstValues(), 0x36eb20/0x36eb3e)
                v[fi] = s["worst"][fi] if (lv == levels[i] - 1 and levels[i] > 1) else s["best"][fi]
            table.append(v)
        ff_values.append(table)

    my_pts = []
    for spec in range(n_specs):
        for v in ff_values[spec]:
            my_pts.append(list(v))
    if debug_level:
        print(STR_SUBMITTING, len(my_pts))

    # ---- level-3 supplement (the CCD-like pair logic, assert upperBool||lowerBool 0x36a622)
    lvl3 = []
    for i in range(n):
        if tun[i] <= 2:
            continue
        up = list(origin)
        lo = list(origin)
        if not (origin[i] == tun[i] - 1):
            up[i] = tun[i] - 1
        else:
            up = None
        if not (origin[i] == 0):
            lo[i] = 0
        else:
            lo = None
        if up is None and lo is None:
            raise AssertionError("upperBool||lowerBool")
        if up:
            lvl3.append(up)
        if lo:
            lvl3.append(lo)
    if debug_level:
        print(STR_ADD3, len(lvl3))
    my_pts.extend(lvl3)
    if not my_pts:                                              # 0x36ace0
        raise AssertionError("my_pts.size()>0")

    # ---- design batch: one fork per point (0x36ad41) ------------------------
    costs = yield my_pts
    for pt, c in zip(my_pts, costs):
        pop.append((pt, float(c)))
    if stop_on_error and any(c != c for c in costs):
        return [p for p, _ in sorted(pop, key=lambda t: t[1])]

    # ---- PointReduction 0x36b36c : spec merging ----------------------------
    if accuracy_level == 2:                                     # 0x367c71 -> PointReduction
        spec_order = []
        for si, s in enumerate(specs):                          # 0x36b412
            c = 0
            for d in range(n):
                if s["is_complex"][d]:
                    c -= 1                                      # 0x36b4c8
            spec_order.append((c, si))
        spec_order.sort()                                       # 0x36b576 "Spec sorted by complexity:"
        dims = []
        for si in range(n_specs):
            if n > ff and specs[si]["dim_order"]:
                dims.append(set(specs[si]["dim_order"][:ff]))   # 0x36b877
            else:
                dims.append(set())
        reduced = []
        for i, (_, si) in enumerate(spec_order):
            mergeable = (i > 0) and (n > ff) and bool(specs[si]["dim_order"])
            merged = False
            if mergeable:
                for j in range(i - 1, -1, -1):
                    pj = spec_order[j][1]
                    if dims[si] != dims[pj]:                    # 0x36b9ae
                        continue
                    ok = True
                    for d in range(n):
                        if specs[pj]["is_complex"][d]:          # 0x36ba26 break out
                            break
                        b1, w1 = specs[pj]["best"][d], specs[pj]["worst"][d]
                        b2, w2 = specs[si]["best"][d], specs[si]["worst"][d]
                        if b1 == b2 and w1 == w2:               # 0x36baa7
                            continue
                        if b1 == w2 and w1 == b2:               # 0x36bbab  REVERSED pair
                            continue
                        ok = False
                        break
                    if ok:
                        if debug_level:
                            print("Spec %d can be merged with spec %d" % (si, pj))
                        mergeable = False
                        merged = True
                        break
            if not merged:
                for v in ff_values[si]:                         # 0x36bd9e
                    reduced.append(list(v))
        if reduced:
            costs = yield reduced                               # one fork per surviving point
            for pt, c in zip(reduced, costs):
                pop.append((pt, float(c)))

    # ---- refinement: one fork per spec, fixpoint re-run ---------------------
    def best_of(points):
        return min(points, key=lambda t: t[1]) if points else None

    rerun = list(range(n_specs))
    while rerun:                                                # 0x36cb18 / 0x36e452
        improved = []
        for si in rerun:
            if debug_level:
                print(STR_PERFORM_BP, si)
            base = best_of(pop)[0]
            bpoint, bcost = yield from _powell(base, tun, iters=int(P.get("powell_iterations", 3)))
            pop.append((bpoint, bcost))
            if debug_level:
                print(STR_FINISH_BP, si)
            # does another spec's new point beat this spec's best?
            best_here = best_of(pop)[1]
            if _better(goal, bcost, best_here):
                improved.append(si)
        if debug_level:
            print(STR_CHECK_RERUN)
        if not improved:
            if debug_level:
                print(STR_NO_RERUN)
            break
        rerun = improved
    return [p for p, _ in sorted(pop, key=lambda t: t[1])]


def _line_minimize(base, direction, tun):
    n = len(base)
    tlo, thi = -10 ** 9, 10 ** 9
    for i in range(n):
        if direction[i] == 0:
            continue
        if direction[i] > 0:
            tlo = max(tlo, -base[i])
            thi = min(thi, tun[i] - 1 - base[i])
        else:
            tlo = max(tlo, tun[i] - 1 - base[i])
            thi = min(thi, -base[i])
    if tlo > thi:
        return list(base), None
    if thi - tlo < 1:
        pt = [max(0, min(tun[i] - 1, base[i] + tlo * direction[i])) for i in range(n)]
        return pt, None
    g = 0.6180339887498949
    a, b = float(tlo), float(thi)
    x1 = b - g * (b - a)
    x2 = a + g * (b - a)
    i1, i2 = int(round(x1)), int(round(x2))
    p1 = [max(0, min(tun[i] - 1, base[i] + i1 * direction[i])) for i in range(n)]
    p2 = [max(0, min(tun[i] - 1, base[i] + i2 * direction[i])) for i in range(n)]
    f1 = (yield [p1])[0]
    f2 = (yield [p2])[0]
    best = (f1, list(p1))
    if f2 < best[0]:
        best = (f2, list(p2))
    for _ in range(12):
        if abs(b - a) < 1.5:
            break
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = b - g * (b - a)
            i1 = int(round(x1))
            p1 = [max(0, min(tun[i] - 1, base[i] + i1 * direction[i])) for i in range(n)]
            f1 = (yield [p1])[0]
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + g * (b - a)
            i2 = int(round(x2))
            p2 = [max(0, min(tun[i] - 1, base[i] + i2 * direction[i])) for i in range(n)]
            f2 = (yield [p2])[0]
        if f1 < best[0]:
            best = (f1, list(p1))
        if f2 < best[0]:
            best = (f2, list(p2))
    return best[1], best[0]


def _powell(base, tun, iters=3):
    n = len(base)
    dirs = [[1 if i == j else 0 for i in range(n)] for j in range(n)]
    cur = list(base)
    fc = (yield [list(cur)])[0]
    for _ in range(iters):
        start, fstart = list(cur), fc
        for d in dirs:
            nxt, fn = yield from _line_minimize(cur, d, tun)
            if fn is not None and fn < fc:
                cur, fc = nxt, fn
        delta = [cur[i] - start[i] for i in range(n)]
        if all(v == 0 for v in delta):
            break
        if fc < fstart:
            dirs.pop(0)
            dirs.append(delta)
    return cur, fc


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
    start = dom.to_index((0.0, 0.0))
    print("start:", start, dom.from_index(start), "cost=%.6f" % dom.cost(start))

    specs = [
        {"name": "s0", "dim_order": [0, 1], "best": [0, 0], "worst": [20, 20],
         "is_complex": [False, False]},
        {"name": "s1", "dim_order": [0, 1], "best": [20, 20], "worst": [0, 0],
         "is_complex": [False, False]},
    ]

    for ff in (5, 1):
        print("\n--- fractional_factor=%d (spec merge %s) ---"
              % (ff, "active" if 2 > ff else "inactive: n <= fractional_factor"))
        g = run(dom, None, start=start,
                params={"fractional_factor": ff, "accuracy_level": 2, "specs": specs})
        batch = next(g)
        k = 0
        while True:
            k += 1
            print("batch %d %2d pts: %s" % (k, len(batch), batch[:8]))
            try:
                batch = g.send([dom.cost(p) for p in batch])
            except StopIteration as e:
                rank = e.value
                break
        print("final ranking (top 5):")
        for p in rank[:5]:
            print("   ", p, dom.from_index(p), "cost=%.6f" % dom.cost(p))
        print("best:", rank[0], dom.from_index(rank[0]), "cost=%.6f" % dom.cost(rank[0]))
