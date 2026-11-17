"""WorstCaseCorners — extracted from libalchemy.so (Alchemy::WorstCaseCorners,
visible name "WorstCaseCorners").

WorstCaseCorners is a *strategy selector*, not a corner enumerator.  Start::run (0x3c4022)
tests four boolean Data fields in a fixed order and hands control to the corresponding DOE
engine, each wrapped in Call<WorstCaseCorners::RunDone>:

    Data+0x00 (state+0x40) use_OFAT            default TRUE   read 0x3c4054
        -> SensitivityOFAT::Init               call 0x3c40c2   wrap 0x3c4174
    Data+0x10 (state+0x50) use_CompositeDesign default false  read 0x3c420a
        -> DOE::Init                           call 0x3c4248   wrap 0x3c42f7
    Data+0x30 (state+0x70) use_2KFactorial     default false  read 0x3c438d
        -> TwoKFactorial::Init                 call 0x3c43cb   wrap 0x3c447a
    Data+0x20 (state+0x60) use_FullFactorial   default false  read 0x3c4510
        -> Sweep::Init                         call 0x3c456c   wrap 0x3c4617

use_OFAT2KFactorial (Data+0x40, default false, 0x3c53f2) is written by the Data ctor but
never read in Start::run -> unused here.

states:
    Init    0x3c3eb0 -> Start
    Start   0x3c4022 -> Call<RunDone>( <engine>::Init(params) )
    RunDone 0x3c4a14 -> throw ShortCircuits::Return

hyperparams (Alchemy::WorstCaseCorners::Data 0x3c4b4a):
    use_OFAT              bool  true    0x3c4c3e
    use_CompositeDesign   bool  false   0x3c4e28
    use_FullFactorial     bool  false   0x3c5016
    use_2KFactorial       bool  false   0x3c5204
    use_OFAT2KFactorial   bool  false   0x3c53f2   (never read)
  WorstCaseCorners adds no keys of its own: every branch just copies the incoming Params
  (Params::operator= at 0x3c4115 / 0x3c4298 / 0x3c441b / 0x3c45b8) before calling <engine>::Init.

"Corner" here = an extreme index of a tunable: 0 = low bound, tunables[i]-1 = high bound.
"""

# --- .rodata constants (none: Start::run references no .rodata string) ------
KEY_USE_OFAT        = 0x576ea9   # 'use_OFAT'
KEY_USE_COMPOSITE   = 0x576eb2   # 'use_CompositeDesign'
KEY_USE_FULLFACT    = 0x576ec6   # 'use_FullFactorial'
KEY_USE_2K          = 0x576ed8   # 'use_2KFactorial'
KEY_USE_OFAT2K      = 0x576ee8   # 'use_OFAT2KFactorial'

KEY_INTERACTION     = 0x564eb2   # 'interaction_level' (DOE, default 2)
TI_RETURN           = 0x6a8a78   # typeinfo ShortCircuits::Return

DEFAULT_USE_OFAT      = True     # 0x3c4c3e  mov BYTE PTR [rax],0x1
DEFAULT_USE_COMPOSITE = False    # 0x3c4e28
DEFAULT_USE_FULLFACT  = False    # 0x3c5016
DEFAULT_USE_2K        = False    # 0x3c5204
DEFAULT_USE_OFAT2K    = False    # 0x3c53f2


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
    return a > b if mx else b > a


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
    return [p for p, _ in sorted(pop, key=lambda t: t[1],
                                 reverse=bool(getattr(goal, "maximize", False)))]


# --------------------------------------------------------------------------
# engine 1: SensitivityOFAT (0x3b62f8) -- one factor at a time, all levels
# --------------------------------------------------------------------------
def _engine_ofat(domain, goal, origin, tun, flags, all_points, steps, pop):
    n = len(tun)
    # Split::run @0x3b6cf2: FORKIDX == 0 (`test eax,eax` @0x3b6e0f) advances tunable_idx
    # (+0x40, `lea edx,[rax+1]` @0x3b6e42) and recurses; children 1..L-1 are the level
    # points.  Fork runs children in ascending FORKIDX order (std::set<int> actives,
    # 0x333777), so the deepest tunable is visited first: t = n-1 .. 0.
    for t in range(n - 1, -1, -1):
        L = tun[t] if (all_points or flags[t]) else 3
        pts = []
        for k in range(1, L):
            if all_points or flags[t]:
                level = k if k > origin[t] else k - 1
            else:
                # 0x3b7638 / 0x3b7692 -- both children fork from the untouched state
                level = origin[t] - steps[t][0] if k == 1 else origin[t] + steps[t][1]
            if level != origin[t] and 0 <= level < tun[t]:
                pt = list(origin)
                pt[t] = level
                pts.append(pt)
        if pts:
            costs = yield pts
            for pt, c in zip(pts, costs):
                pop.append((pt, float(c)))
    # WaitFirstPoint 0x3b8cc4 submits current_point, already evaluated by the driver
    # (run() call #0) -> no cost call, therefore nothing to emit here.


# --------------------------------------------------------------------------
# engine 2: DOE (0x30ba10) -- interaction subsets, upper/lower extremes
# --------------------------------------------------------------------------
def _engine_doe(domain, goal, origin, tun, k):
    n = len(tun)
    k = min(k, n)
    queue = [[i] for i in range(n)]
    pts = []
    while queue:
        S = queue.pop(0)
        hi, lo = list(origin), list(origin)
        up = low = True
        for i in S:
            if origin[i] == tun[i] - 1:
                up = False
            else:
                hi[i] = tun[i] - 1
            if origin[i] == 0:
                low = False
            else:
                lo[i] = 0
        if up:
            pts.append(hi)
        if low:
            pts.append(lo)
        if len(S) < k:
            for j in range(S[-1] + 1, n):
                queue.append(S + [j])
    costs = yield pts
    pop = []
    for pt, c in zip(pts, costs):
        pop.append((pt, float(c)))
    return pop


# --------------------------------------------------------------------------
# engine 3: TwoKFactorial (0x3c1702) -- 2^n corners (L^n when flags set)
# --------------------------------------------------------------------------
def _engine_2k(domain, goal, tun, flags):
    n = len(tun)
    corners = []
    cur = [0] * n

    def rec(t):
        if t == n:
            corners.append(list(cur))
            return
        L = tun[t] if flags[t] else 2
        for k in range(L):
            cur[t] = k if flags[t] else (tun[t] - 1 if k else 0)
            rec(t + 1)

    rec(0)
    costs = yield corners
    return [(pt, float(c)) for pt, c in zip(corners, costs)]


# --------------------------------------------------------------------------
# engine 4: Sweep (0x3bf71c) -- full lattice, bounded by exhaustiveLimit
# --------------------------------------------------------------------------
def _engine_sweep(domain, goal, tun, limit):
    n = len(tun)
    total = 1
    for t in tun:
        total *= t
        if limit and total > limit:
            total = limit
            break
    pts = []
    cur = [0] * n

    def rec(t):
        if len(pts) >= total:
            return
        if t == n:
            pts.append(list(cur))
            return
        for k in range(tun[t]):
            cur[t] = k
            rec(t + 1)
            if len(pts) >= total:
                return

    if n:
        rec(0)
    costs = yield pts
    return [(pt, float(c)) for pt, c in zip(pts, costs)]


def run(domain, goal, start=None, params=None):
    """Dispatch to the selected engine; return the ranking of the points it produced.

    Return value: list of index-vectors, best first.
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)
    if start is None:
        start = [(t - 1) // 2 for t in tun]
    origin = list(start)
    flags = list(P.get("modelParamFlags") or [False] * n)
    if n == 0:
        return []

    use_ofat = bool(P.get("use_OFAT", DEFAULT_USE_OFAT))                 # 0x3c4054
    use_comp = bool(P.get("use_CompositeDesign", DEFAULT_USE_COMPOSITE)) # 0x3c420a
    use_2k = bool(P.get("use_2KFactorial", DEFAULT_USE_2K))              # 0x3c438d
    use_full = bool(P.get("use_FullFactorial", DEFAULT_USE_FULLFACT))    # 0x3c4510

    # Driver call #0 (FunctionOptimizer::run 0x3ce70a): the start point is evaluated
    # before Start::run @0x3c4022 dispatches.  SensitivityOFAT's WaitFirstPoint
    # (0x3b8cc4) later re-submits that same, already evaluated point -> no extra call.
    yield [domain.from_index(_driver_start(domain, start))]

    pop = []
    if use_ofat:
        # WorstCaseCorners does NOT inject allPoints (only OFATSearch does, 0x357a13),
        # so the sub-run keeps SensitivityOFAT's own default `allPoints = false`
        # (0x3b957c writes nothing -> Data default false, cf. SensitivityOFAT.py):
        # measured `harness WorstCaseCorners 2 21 600 0 10 0 0` = start + 2 points,
        # i.e. the 3-level path, not the 21-level sweep.
        yield from _engine_ofat(domain, goal, origin, tun, flags,
                                bool(P.get("allPoints", False)),
                                P.get("design_steps") or [(1, 1)] * n, pop)
    elif use_comp:
        pop = yield from _engine_doe(domain, goal, origin, tun,
                                     int(P.get("interaction_level", 2)))
    elif use_2k:
        pop = yield from _engine_2k(domain, goal, tun, flags)
    elif use_full:
        pop = yield from _engine_sweep(domain, goal, tun,
                                       int(P.get("exhaustiveLimit", 0)))
    else:
        return []                                    # every flag false -> nothing runs
    # RunDone 0x3c4a14 -> Return
    return _rank(pop, goal)


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

    for flag, note in (("use_OFAT", "default: SensitivityOFAT"),
                       ("use_CompositeDesign", "DOE, interaction_level=2"),
                       ("use_2KFactorial", "2^2 corners"),
                       ("use_FullFactorial", "Sweep lattice")):
        P = {k: False for k in ("use_OFAT", "use_CompositeDesign",
                                "use_2KFactorial", "use_FullFactorial")}
        P[flag] = True
        if flag == "use_OFAT":
            P["allPoints"] = True
        print("\n--- %s (%s) ---" % (flag, note))
        g = run(dom, None, start=start, params=P)
        batch = next(g)
        total = 0
        shown = 0
        while True:
            total += len(batch)
            if shown < 2:
                print("batch %2d pts: %s" % (len(batch), batch[:8]))
                shown += 1
            try:
                batch = g.send([dom.cost(p) for p in batch])
            except StopIteration as e:
                rank = e.value
                break
        print("total points: %d" % total)
        print("ranking (top 4):", rank[:4])
        print("best:", rank[0], dom.from_index(rank[0]), "cost=%.6f" % dom.cost(rank[0]))
