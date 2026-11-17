"""SensitivityOFAT — extracted from libalchemy.so (Alchemy::SensitivityOFAT,
algs/SensitivityOFAT.cpp, visible name "OFATSensitivity").

states:
    Init          0x3b6162  -> Start
    Start         0x3b62f8  -> Fork<Split>(L_begin)                 L_i = (allPoints||flags[i]) ? tunables[i] : 3
    Split         0x3b6cf2  -> Fork<Split>(L_next) | PointRun | WaitFirstPoint | ThreadsWait | Fork<FactorialStart>(2)
    PointRun      0x3b8dc8  -> PointDone
    PointDone     0x3b8f00  -> self (not evaluated) | ThreadsWait (after population insert)
    WaitFirstPoint 0x3b8cc4 -> PointDone          (submits the base point once)
    ThreadsWait   0x3b90be  -> JOIN loop, then throw ShortCircuits::Return
    SplitFactorial 0x3b7b5a / FactorialStart 0x3b85b6   (run2LevelFactorial branch)

hyperparams (Alchemy::SensitivityOFAT::Data 0x3b921e):
    current_point       shared_ptr<DesignPoint>   --            start point / base
    allPoints           bool                      false  0x3b957c
    modelParamFlags     shared_ptr<vector<bool>>  empty     (no literal default)
    population          shared_ptr<Population>    --        cost-ordered bounded set
    design_steps        shared_ptr<vector<pair<int,int>>>  empty
    run2LevelFactorial  bool                      false  0x3b9d9a
    stop_on_error       bool                      false  0x3b9f8b
    begin_idx           int                       0      0x3ba17c
    end_idx             int                       -1     0x3ba370   (-1 -> tunables.size())

Ordinal, NOT numeric: across all ten run() bodies (0x3b62f8..0x3b910c) there is not one
floating-point instruction (no addsd/subsd/mulsd/divsd/cvtsi2sd/cvttsd2si) -- there is no
sensitivity coefficient, only a cost-ranked sweep.

This algorithm therefore produces **no** `dimOrder`; the ranking lives in its two
*consumers*, and they use two different keys (both measured, native/PARITY.md §13):

    PSA::InitialOFAT  @0x39de3c   rank[d] = SUM over the archived probes of that dim of
                                 (cost(p) - cost(base))           0x39e115 `subsd`,
                                 0x39e137/0x39e13b `addsd`        (signed, accumulates)
                                 multimap walked begin()..end()  0x39e254 -> 0x39e27a
                                 => most-improving factor FIRST, ties by ascending dim.
                                 Measured (scratch harness with `sortingVariables=true`,
                                 target (8,11), levels 21): dimOrder [1,0] -- d1 improves
                                 by -1, d0 worsens by +5.

    PDE::FirstOFAT    @0x3852dc   rank[d] = |cost - base| of the LAST differing point in
                                 ascending-cost order         0x3855c3 `subsd` 0x3855d5
                                 `andpd` (fabs), assignment 0x385614
                                 multimap walked rbegin()..rend() 0x38570a
                                 => worst |delta| FIRST, ties by descending dim.
                                 Measured (dim 4 levels 17 target 12,11,10,12):
                                 dimOrder [3,1,2,0]; the signed rule would give [0,3,2,1].

The `[unproven]` note that used to sit on 0x39e290 ("|Δcost| descending? level index?")
conflated the two; neither site ranks by level index.

Point generation (the algorithm body):
    L(t)  = (allPoints || modelParamFlags[t]) ? tunables[t] : 3      0x3b6af3 / 0x3b6b14
    for t in [begin_idx, end_idx):                                   0x3b6e4c
        for k in 1 .. L(t)-1:                                        0x3b6e05 (fork 0 is the recursion)
            allPoints path: level = (k > original[t]) ? k : k-1      0x3b7560 / 0x3b7573  (skips the base level)
            3-level path  : level = k==1 ? values[t]-ds[t].first
                                          : values[t]+ds[t].second   0x3b7638 / 0x3b7692
            if level != original[t] and 0 <= level < tunables[t]: submit
    then (only when begin_idx == 0) the base point itself is inserted once  (WaitFirstPoint)
"""

# --- .rodata constants ------------------------------------------------------
STR_START_RUN        = 0x574f28   # 'virtual Alchemy::State::Ptr Alchemy::SensitivityOFAT::Start::run()'
STR_SRC              = 0x574f6b   # 'algs/SensitivityOFAT.cpp'
STR_FLAGS_SIZE       = 0x574f88   # 'modelParamFlags->size()==POINTS->tunables.size()'
STR_BEGIN0           = 0x574fb9   # '*begin_idx == 0'
STR_ENDN             = 0x574fd0   # '*end_idx == static_cast<int>(POINTS->tunables.size())'
STR_VALUES_SIZE      = 0x575008   # 'values.size() == POINTS->tunables.size()'
STR_ORIG_SIZE        = 0x575038   # 'original_values.size() == POINTS->tunables.size()'
STR_DESIGN_NOTNULL   = 0x57506a   # '!!design_steps'
STR_DESIGN_EMPTY     = 0x575080   # 'design_steps->empty() || design_steps->size() == POINTS->tunables.size()'
STR_DESIGN_SIZE      = 0x5750d0   # 'design_steps->size() == POINTS->tunables.size()'
STR_LEVELS           = 0x575100   # '((*allPoints || (*modelParamFlags)[tunable_idx]) ? POINTS->tunables[tunable_idx] : 3)>0'
STR_SPLIT_RUN        = 0x575158   # 'virtual Alchemy::State::Ptr Alchemy::SensitivityOFAT::Split::run()'
STR_FORK_LT_TUN      = 0x5751a0   # 'FORKIDX < POINTS->tunables[tunable_idx]'
STR_FORK_LT_3        = 0x5751c8   # 'FORKIDX < 3'
STR_TUN_GT0          = 0x5751d8   # 'POINTS->tunables[tunable_idx]>0'
STR_IDX_LT_STEPS     = 0x5751f8   # 'tunable_idx < design_steps->size()'
STR_FORK_RANGE       = 0x575220   # 'FORKIDX > 0 && FORKIDX < POINTS->tunables[tunable_idx]'
STR_SPLITFACT_RUN    = 0x575258   # 'virtual Alchemy::State::Ptr Alchemy::SensitivityOFAT::SplitFactorial::run()'
STR_FORK_GE0         = 0x5752a4   # 'FORKIDX >= 0'
STR_FACTSTART_RUN    = 0x5752b8   # 'virtual Alchemy::State::Ptr Alchemy::SensitivityOFAT::FactorialStart::run()'
STR_FORK_2           = 0x575304   # 'FORKIDX >= 0 && FORKIDX < 2'

KEY_CURRENT_POINT    = 0x574eb0   # 'current_point'
KEY_ALLPOINTS        = 0x574ebe   # 'allPoints'
KEY_MODELFLAGS       = 0x574ec8   # 'modelParamFlags'
KEY_POPULATION       = 0x574ed8   # 'population'
KEY_DESIGN_STEPS     = 0x574ee3   # 'design_steps'
KEY_RUN2LEVEL        = 0x574ef0   # 'run2LevelFactorial'
KEY_STOP_ON_ERROR    = 0x574f03   # 'stop_on_error'
KEY_BEGIN_IDX        = 0x574f11   # 'begin_idx'
KEY_END_IDX          = 0x574f1b   # 'end_idx'

TI_RETURN            = 0x6a8a78   # typeinfo ShortCircuits::Return
TI_DONE              = 0x6a7eb0   # typeinfo ShortCircuits::Done
TI_JOIN              = 0x6a8070   # typeinfo ShortCircuits::Join

N_LEVELS_FALLBACK    = 3          # 0x3b6aec / 0x3b6bcb  immediate 0x3


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


def _rank(pop, goal):
    """Population order: cost ascending (evil_ptr_less)."""
    return sorted(pop, key=lambda t: t[1], reverse=bool(getattr(goal, "maximize", False)))


def _driver_start(domain, start):
    """Driver call #0 -- the start point, evaluated before the algorithm is built."""
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
    """Sweep one factor at a time; return the ordinal factor ranking.

    Yielded batch: one point per step (the engine forks one child per level, so a *round*
    of the fork scheduler submits one point per live level; here each batch is one point).
    Return value: list of tunable indices ordered by their best attainable cost (cheapest
    single-factor move first) -- the same key Sensitivity/OFATSearch use downstream.
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)

    flags = P.get("modelParamFlags")
    flags = list(flags) if flags else [False] * n
    if len(flags) != n:                                   # 0x3b6419
        raise AssertionError("modelParamFlags->size()==POINTS->tunables.size()")

    all_points = bool(P.get("allPoints", False))
    run2level = bool(P.get("run2LevelFactorial", False))
    stop_on_error = bool(P.get("stop_on_error", False))
    begin = int(P.get("begin_idx", 0))                    # 0x3b644f
    end = int(P.get("end_idx", -1))
    if end == -1:                                         # 0x3b6474
        end = n
    steps = P.get("design_steps") or [(1, 1)] * n         # 0x3b6960 make_pair(1,1)

    if start is None:
        start = [(t - 1) // 2 for t in tun]
    origin = list(start)                                  # original_values (+0x70)
    values = list(start)                                  # values          (+0x58)
    if len(values) != n:
        raise AssertionError("values.size() == POINTS->tunables.size()")
    if n == 0:
        return []

    pop = []                                              # (index_vector, cost)
    base_cost = None

    # Driver call #0: FunctionOptimizer::run (0x3ce70a) evaluates the start point before
    # SensitivityOFAT::Init/Start (0x3b6162/0x3b62f8) is even constructed.  The
    # WaitFirstPoint state (0x3b8cc4) later submits that same, already evaluated
    # DesignPoint (state+0x88 -> current_point) and therefore produces NO cost call --
    # measured: `harness SensitivityOFAT 2 21 600 0 10 0 0` has exactly 3 evaluations
    # (start + 2 factor points), not 4.
    costs = yield [domain.from_index(_driver_start(domain, start))]
    base_cost = float(costs[0])
    pop.append((list(origin), base_cost))

    # Fork order: Split::run @0x3b6cf2 branches on FORKIDX == 0 (`test eax,eax` @0x3b6e0f):
    # child 0 advances tunable_idx (+0x40, `lea edx,[rax+1]` @0x3b6e42) and recurses,
    # children 1..L-1 build the level points.  Fork runs its children in ascending
    # FORKIDX order (`std::set<int> actives`, 0x333777), so the DEEPEST tunable is
    # submitted first: tunable order is n-1 down to begin_idx.
    for t in range(end - 1, begin - 1, -1):               # Split fork-0 recursion
        L = tun[t] if (all_points or flags[t]) else N_LEVELS_FALLBACK
        if not (L > 0):                                   # 0x3b6af3
            raise AssertionError(STR_LEVELS)
        # fork children k = 1 .. L-1 (k == 0 is the recursion itself, no point)
        pts = []
        for k in range(1, L):
            if all_points or flags[t]:
                if not (0 < k < tun[t]):
                    raise AssertionError(STR_FORK_RANGE)
                level = k if k > origin[t] else k - 1     # 0x3b7560 / 0x3b7573
            else:
                if k >= 3:
                    raise AssertionError(STR_FORK_LT_3)
                if t >= len(steps):
                    raise AssertionError(STR_IDX_LT_STEPS)
                # 0x3b7638 / 0x3b7692: both children are forked from the untouched
                # state, so the level is relative to original_values, not to the level
                # written by the sibling child (native: origin 10 -> 9 AND 11).
                level = origin[t] - steps[t][0] if k == 1 else origin[t] + steps[t][1]
            if level != origin[t] and 0 <= level < tun[t]:   # 0x3b7706-0x3b778c
                pt = list(origin)
                pt[t] = level
                pts.append(pt)
        if pts:
            costs = yield pts
            for pt, c in zip(pts, costs):
                pop.append((pt, float(c)))
                if stop_on_error and c != c:              # hasErrorCost(): NaN stands in for the flag
                    return _rank(pop, goal)

    # WaitFirstPoint 0x3b8cc4: submits current_point, already evaluated -> no cost call
    # (modelled by driver call #0 above; `begin == 0` only decides *whether* it runs).

    # Ordinal ranking: for each tunable, the cheapest cost reachable by moving it alone.
    ranking = []
    idx = list(origin)
    for i in range(n):
        best = base_cost if base_cost is not None else float("inf")
        for pt, c in pop:
            if pt[i] != origin[i] and _better(goal, c, best):
                best = c
        ranking.append((best, i))
    ranking.sort(key=lambda x: x[0], reverse=bool(getattr(goal, "maximize", False)))
    return [i for _, i in ranking]


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

    # (a) allPoints=true  -> full 21-level sweep of each tunable (the OFATSearch mode)
    g = run(dom, None, start=start, params={"allPoints": True})
    batch = next(g)
    print("\nallPoints=True, generated points (in order):")
    n_total = 0
    while True:
        print("   ", batch, "cost=%s" % ["%.4f" % dom.cost(p) for p in batch])
        n_total += len(batch)
        try:
            batch = g.send([dom.cost(p) for p in batch])
        except StopIteration as e:
            rank = e.value
            break
    print("points:", n_total)
    print("ordinal factor ranking (best single move first):", rank)

    # (b) allPoints=false -> 3 levels per tunable ({-step, base, +step})
    g = run(dom, None, start=start, params={"allPoints": False,
                                            "design_steps": [(4, 4), (4, 4)]})
    batch = next(g)
    print("\nallPoints=False (design_steps=+/-4), generated points:")
    while True:
        print("   ", batch)
        try:
            batch = g.send([dom.cost(p) for p in batch])
        except StopIteration as e:
            rank = e.value
            break
    print("ordinal factor ranking:", rank)
