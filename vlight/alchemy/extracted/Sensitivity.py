"""Sensitivity — extracted from libalchemy.so (Alchemy::Sensitivity, visible name "Sensitivity").

This class is *only* a window driver: it slices the tunable list into [begin_idx,end_idx)
windows and runs SensitivityOFAT on each slice.  It computes no coefficient of its own
(the three run() bodies 0x3b154a/0x3b1f32/0x3b241e contain zero FP instructions).

states:
    Init         0x3b13b4  -> Start
    Start        0x3b154a  -> Call<ContinueRun>(SensitivityOFAT::Init(params))
    ContinueRun  0x3b1f32  -> Call<ContinueRun>(SensitivityOFAT ...)  | RunDone
    RunDone      0x3b241e  -> throw ShortCircuits::Return

hyperparams (Alchemy::Sensitivity::Data 0x3b25a0):
    current_point      shared_ptr<DesignPoint>   --      base point
    design_steps       shared_ptr<vector<pair<int,int>>>  empty
    modelParamFlags    shared_ptr<vector<bool>>  empty
    allPoints          bool                      false   0x3b2d3d
    seed               shared_ptr<long>          -1       0x3b2f2e
    points             shared_ptr<unsigned>      10       0x3b3123
    var_step_size      shared_ptr<int>           -1       0x3b3317
    run2LevelFactorial bool                      false   0x3b350b
    use_OFAT           bool                      true    0x3b36fc
    use_Uniform        bool                      false   0x3b38ed

state fields used by the driver (AlgState):
    +0x44 begin_idx (int)       +0x48 end_idx (int, -1 = "done")
    +0xf0 window stride (int)   +0x100 bool          +0x110 use_OFAT (bool)
    +0x120 shared_ptr<bool> use_Uniform

Window walk (Start 0x3b154a / ContinueRun 0x3b1f32):
    Start        : begin = 0                                       0x3b158d
                   stride = (v == -1 || flag) ? n : v              0x3b15cb-0x3b1616
                   end = (n > stride) ? stride : -1                0x3b162f-0x3b1659
                   params["begin_idx"] = begin ; params["end_idx"] = end    0x3b16b6 / 0x3b1733
                   -> Call<ContinueRun>(SensitivityOFAT::Init(params))      0x3b1850
    ContinueRun  : if (end == -1) -> RunDone                        0x3b1f5b
                   begin = end                                     0x3b2016
                   end  += stride                                  0x3b2043
                   if (n <= end) end = -1                           0x3b2075
                   -> Call<ContinueRun>(SensitivityOFAT::Init(params))      0x3b2290
    RunDone      : throw ShortCircuits::Return                      0x3b244d
"""

# --- .rodata constants ------------------------------------------------------
KEY_BEGIN_IDX   = 0x57485b   # 'begin_idx'
KEY_END_IDX     = 0x574865   # 'end_idx'
KEY_SEED        = 0x574819   # 'seed'
KEY_POINTS      = 0x57481e   # 'points'
KEY_CURRENT_PT  = 0x5747e4   # 'current_point'
KEY_DESIGN_STEP = 0x5747f2   # 'design_steps'
KEY_MODELFLAGS  = 0x5747ff   # 'modelParamFlags'
KEY_ALLPOINTS   = 0x57480f   # 'allPoints'
KEY_VAR_STEP    = 0x574825   # 'var_step_size'
KEY_RUN2LEVEL   = 0x574833   # 'run2LevelFactorial'
KEY_USE_OFAT    = 0x574846   # 'use_OFAT'
KEY_USE_UNIFORM = 0x57484f   # 'use_Uniform'

TI_RETURN       = 0x6a8a78   # typeinfo ShortCircuits::Return

DEFAULT_POINTS     = 10      # 0x3b3123  mov DWORD PTR [rax],0xa
DEFAULT_SEED       = -1      # 0x3b2f2e  mov QWORD PTR [rax],-1
DEFAULT_VAR_STEP   = -1      # 0x3b3317  mov DWORD PTR [rax],-1
N_LEVELS_FALLBACK  = 3       # 0x3b6aec


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


def run(domain, goal, start=None, params=None):
    """Walk [begin_idx,end_idx) windows; return the ordinal factor ranking.

    Yielded batch: one window's SensitivityOFAT point list (one point per level).
    Return value: list of tunable indices, cheapest single-factor move first.
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)
    flags = list(P.get("modelParamFlags") or [False] * n)
    all_points = bool(P.get("allPoints", False))
    use_ofat = bool(P.get("use_OFAT", True))
    run2level = bool(P.get("run2LevelFactorial", False))
    steps = P.get("design_steps") or [(1, 1)] * n

    if start is None:
        start = [(t - 1) // 2 for t in tun]
    origin = list(start)
    if n == 0:
        return []

    # +0xf0: window stride.  Default -1 -> the whole tunable list in one window.
    stride = int(P.get("var_step_size", DEFAULT_VAR_STEP))
    if stride == -1:
        stride = n

    pop = []                                   # (index_vector, cost)
    base_cost = None
    begin = 0                                  # 0x3b158d
    end = stride if n > stride else -1         # 0x3b162f-0x3b1659
    first_window = True

    # Driver call #0: FunctionOptimizer::run (0x3ce70a) evaluates the start point before
    # Sensitivity::Start @0x3b154a builds its first window.  SensitivityOFAT's
    # WaitFirstPoint (0x3b8cc4) then re-submits that already evaluated current_point and
    # produces no cost call: `harness Sensitivity 2 21 600 0 10 0 0` = 3 evaluations.
    costs = yield [domain.from_index(_driver_start(domain, start))]
    base_cost = float(costs[0])
    pop.append((list(origin), base_cost))

    while True:                                # ContinueRun loop
        lo = begin
        hi = n if end == -1 else min(end, n)
        # ---- SensitivityOFAT restricted to [lo, hi) --------------------------
        if use_ofat:
            # Split::run @0x3b6cf2: FORKIDX == 0 (`test eax,eax` @0x3b6e0f) advances
            # tunable_idx @0x3b6e42 and recurses, the level points are children 1..L-1.
            # Fork runs children in ascending FORKIDX order (0x333777), hence the
            # deepest tunable is submitted first: t = hi-1 .. lo.
            for t in range(hi - 1, lo - 1, -1):
                L = tun[t] if (all_points or flags[t]) else N_LEVELS_FALLBACK
                pts = []
                for k in range(1, L):
                    if all_points or flags[t]:
                        level = k if k > origin[t] else k - 1
                    else:
                        # 0x3b7638 / 0x3b7692: each child is forked from the untouched
                        # state, so both levels are relative to origin[t]
                        # (native: origin 10 -> 9 AND 11).
                        level = origin[t] - steps[t][0] if k == 1 else origin[t] + steps[t][1]
                    if level != origin[t] and 0 <= level < tun[t]:
                        pt = list(origin)
                        pt[t] = level
                        pts.append(pt)
                if pts:
                    costs = yield pts
                    for pt, c in zip(pts, costs):
                        pop.append((pt, float(c)))
        else:
            # use_OFAT == false: the seed/points branch (0x3b18d6).  It only re-packages
            # params (seed/points) for the sub-run; the point set is still produced by the
            # sub-algorithm, so no extra points are generated here.  [unproven]
            pass

        first_window = False
        if end == -1:                          # 0x3b1f5b  next ContinueRun -> RunDone
            break
        begin = end                            # 0x3b2016
        end = end + stride                     # 0x3b2043
        if n <= end:                           # 0x3b2075
            end = -1

    # RunDone 0x3b241e: the driver itself ranks nothing; the ordinal ranking below is the
    # consumer-side key (OFATDone 0x357ef5 / SubmitBegin 0x367f5b) applied to the sweep.
    ranking = []
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

    for stride in (n_ := len(dom.tunables), 1):
        g = run(dom, None, start=start,
                params={"allPoints": True, "var_step_size": stride})
        batch = next(g)
        print("\nwindow stride=%d" % stride)
        while True:
            print("  batch (%d pts): %s" % (len(batch), batch[:4] + (["..."] if len(batch) > 4 else [])))
            try:
                batch = g.send([dom.cost(p) for p in batch])
            except StopIteration as e:
                rank = e.value
                break
        print("  ordinal factor ranking:", rank)
