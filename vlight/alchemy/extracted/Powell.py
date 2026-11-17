"""Powell — extracted from libalchemy.so (algs/Powell.cpp).

states:
    Init -> Start -> {Start_WaitForStartingPoint, Start_HaveStartingPoint}
    Start_HaveStartingPoint -> LoopBegin                        @0x3924e4
    LoopBegin -> Loop                                           @0x392a3c
    Loop -> Call<Powell::LineMinimizeDone>(LineMinimize::Init)  @0x392c56
    LineMinimizeDone -> {Loop, LoopDone}                        @0x39370b / @0x3937e3
    LoopDone -> {LoopBegin, Return}                             @0x393934
    Done -> Return

hyperparams (Data::Data(Params) @0x394a84):
    current_point            shared_ptr<DesignPoint>  default empty DesignPoint 0x394bb1
    minimum_steps_for_brent  shared_ptr<unsigned>     default 30                 0x394dbc
    spec_index               shared_ptr<int>          default -1                 0x394fad
    stop_on_error            shared_ptr<bool>         default false              0x3951a1
    accurate_mode            shared_ptr<bool>         default false              0x395392
    debug_level              shared_ptr<int>          default 0                  0x395583
    population               shared_ptr<Population>   default Population(1)      0x39576d
    (Loop::run additionally injects `population` @0x571619 and `vector` @0x57167c
     into the nested LineMinimize Params.)

The line search is delegated to Alchemy::LineMinimize:
    ray  : integer points along `vector` from the incumbent, clipped to bounds
    mode : len(line) < minimum_steps_for_brent -> Min_Exhaustive (whole line in one
           batch) else Min_Brent (integer Brent on the line index)
"""

import math


# ---------------------------------------------------------------------------
#  canonical line search: algorithms/LineMinimize.py, loaded by path
# ---------------------------------------------------------------------------
import importlib.util
import os


def _load_line_minimize():
    """Load the canonical `Alchemy::LineMinimize` extraction that sits next to
    this file (path-relative, stdlib only -> `python3 Powell.py` still works)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LineMinimize.py")
    spec = importlib.util.spec_from_file_location(
        "AlchemyLineMinimize_%s_%d" % (__name__, id(object())), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LineMinimize = _load_line_minimize()


class _NoCachePointFactory(LineMinimize._Factory):
    """`DesignPointFactoryImpl::create` always allocates a fresh, unevaluated
    point and enqueues it (spec_core §8.2 @0x3cef6e / 0x3cf016); the cache lookup
    modelled by LineMinimize.py's `_Factory.create` has no caller inside
    libalchemy.so (§8.5), so it would suppress evaluations the native line
    search really performs (e.g. re-evaluating the base point of a
    Min_Exhaustive scan).  `run()` resolves `_Factory` from the module globals."""

    def create(self, ix):
        return LineMinimize._Pt(tuple(int(v) for v in ix), None, False)


LineMinimize._Factory = _NoCachePointFactory

# ---------------------------------------------------------------------------
#  DesignPoint::operator< @0x3d1dda -- the population ordering.
#  Note the asymmetry (verified by calling the real symbol, see
#  native/probe_cmp.cpp): BOTH hashes rotate with r taken from the LEFT
#  operand's values -> (70,30) and (30,70) are equivalent, not ordered.
# ---------------------------------------------------------------------------
_HASH_MUL = 0xEF32F8C9
_HASH_MUL_STEP = 0x5C6B7


def _hash_fold(values, rot_src):
    h = 0
    mul = _HASH_MUL
    for i, v in enumerate(values):
        v = int(v)
        h = (h * mul + (v & 0xFFFFFFFF)) & 0xFFFFFFFF
        rv = int(rot_src[i])
        r = ((rv % 16) + 16) % 16
        h = ((h << r) + (h >> (16 - r))) & 0xFFFFFFFF
        mul = (mul * _HASH_MUL_STEP) & 0xFFFFFFFF
    return h


def _dp_less(ia, ca, ib, cb):
    if ca < cb:
        return True
    if cb < ca:
        return False
    if len(ia) != len(ib):
        return False
    h1 = _hash_fold(ia, ia)
    h2 = _hash_fold(ib, ia)
    if h1 < h2:
        return True
    if h2 < h1:
        return False
    for x, y in zip(ia, ib):
        if x < y:
            return True
        if y < x:
            return False
    return False

CGOLD = 0.381966            # Alchemy::LineMinimize::CGOLD 0x568a20 (decimal literal,
                            # NOT (3-sqrt(5))/2 = 0.3819660112501051)
STAGNANT_COST_IMPROVEMENT = 0.999  # Alchemy::Powell::StagnantCostImprovement 0x5715b8
                               # (a second copy at 0x571d78 is the one LoopDone uses)
UNIT = 1.0                     # 0x571d70

K_CURRENT_POINT = "current_point"          # 0x5715c0
K_MIN_STEPS = "minimum_steps_for_brent"    # 0x5715ce
K_SPEC_INDEX = "spec_index"                # 0x5715e6
K_STOP_ON_ERROR = "stop_on_error"          # 0x5715f1
K_ACCURATE_MODE = "accurate_mode"          # 0x5715ff
K_DEBUG_LEVEL = "debug_level"              # 0x57160d
K_POPULATION = "population"                # 0x571619

DEFAULTS = {
    K_MIN_STEPS: 30,
    K_SPEC_INDEX: -1,
    K_STOP_ON_ERROR: False,
    K_ACCURATE_MODE: False,
    K_DEBUG_LEVEL: 0,
}


def _unit(n, i):
    e = [0.0] * n
    e[i] = UNIT
    return e


def _u32(v):
    return v & 0xFFFFFFFF


def _resolve_start(domain, start, param_value):
    """start may be a point (dict / coordinate sequence) or an index list.
    The engine's own default is an *empty* DesignPoint, so a standalone run
    falls back to the nominal level (or the grid centre)."""
    s = start if start is not None else param_value
    n = len(domain.tunables)
    if s is None:
        if hasattr(domain, "nominal_index"):
            return list(domain.nominal_index())
        return list(domain.center_index())
    if isinstance(s, dict):
        return list(domain.to_index(s))
    if (isinstance(s, (list, tuple)) and len(s) == n
            and all(isinstance(v, int) for v in s)):
        return list(s)
    return list(domain.to_index(s))


_PARAM_KEYS = (K_CURRENT_POINT, K_MIN_STEPS, K_SPEC_INDEX, K_STOP_ON_ERROR, K_ACCURATE_MODE, K_DEBUG_LEVEL, K_POPULATION)

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

def run(domain, goal, start=None, params=None):
    """yield a batch of points; receive costs via send(); return best index list"""
    params = _as_params(params, _PARAM_KEYS)
    min_steps = int(params.get(K_MIN_STEPS, DEFAULTS[K_MIN_STEPS]))
    spec_index = int(params.get(K_SPEC_INDEX, DEFAULTS[K_SPEC_INDEX]))
    accurate_mode = bool(params.get(K_ACCURATE_MODE, DEFAULTS[K_ACCURATE_MODE]))
    debug_level = int(params.get(K_DEBUG_LEVEL, DEFAULTS[K_DEBUG_LEVEL]))
    max_stationary = int(params.get("max_stationary", 0))   # [unproven] LineMinimize key

    tun = [int(t) for t in domain.tunables]
    n = len(tun)

    start_ix = list(domain.clamp_index(
        _resolve_start(domain, start, params.get(K_CURRENT_POINT))))

    # ---- Start / Start_HaveStartingPoint -----------------------------------
    # The driver evaluates the start point once (ev 0) and Powell's Start inserts
    # it into the population (0x394a84..: `population` is a hyper-parameter).
    (c0,) = yield [domain.from_index(start_ix)]
    c0 = float(c0)
    cost_of = {tuple(start_ix): c0}

    # Powell's `population` hyper-parameter is a real Population<..., evil_ptr_less>
    # with maxsize_ == 1; Start seeds it with the (already evaluated) start point
    # (`insertAndDiscardExtras` @0x3925b3).  The nested line search is the only
    # writer after that: Min_Exhaustive_Wait 0x33539f / Min_Brent_WaitPoint
    # 0x336258 (the latter *after* the fail-count test at 0x3361d2), so the
    # caller must not insert the probes it merely evaluated.
    pop = LineMinimize._Pop(1)
    pop.insert(LineMinimize._Pt(tuple(start_ix), c0, False))

    def _record(ix, cost):
        """Cost bookkeeping for the cycle-start / per-direction delta map."""
        cost_of[tuple(int(v) for v in ix)] = float(cost)

    def _best():
        t = pop.top()
        return list(t.ix), t.cost

    def _line_minimize(dvec):
        """Nested Alchemy::LineMinimize (algorithms/LineMinimize.py), driven with
        the same Params Powell's Loop::run builds (0x392c56..0x392e4d):

            population               = ours      (base = population->top())
            vector                   = direction (key @0x57167c)
            minimum_steps_for_brent  = forwarded (default 30)
            directional              = absent -> false

        Every probe the line search really accepts is inserted into the shared
        population (Min_Brent_WaitPoint 0x336258 / Min_Exhaustive 0x33539f), so
        the returned incumbent is the *population top*, not just the best of the
        current line.
        """
        base_ix, base_cost = _best()
        lm = LineMinimize.run(domain, goal, start=base_ix,
                              params={"vector": [float(x) for x in dvec],
                                      "directional": False,
                                      "fail_count_limit": 0,
                                      "minimum_steps_for_brent": min_steps,
                                      "start_cost": base_cost,
                                      "population": pop})
        try:
            batch = next(lm)
        except StopIteration:
            return _best()
        while True:
            got = yield batch
            for ix, c in zip(batch, got):
                _record(ix, c)
            try:
                batch = lm.send([float(c) for c in got])
            except StopIteration:
                break
        return _best()

    # ---- LoopBegin::run @0x392a3c ------------------------------------------
    dirs = []
    cur = list(start_ix)
    cycle_start = list(cur)
    fmapped = {}

    cycles = 0
    while True:
        cycles += 1
        if cycles > 500:          # Python-side safety cap (not in the binary)
            return _best()[0]
        if not dirs:                                # 0x392a57
            dirs = [_unit(n, i) for i in range(n)]
        cursor = 0                                  # 0x392b36
        fmapped = {}                                # 0x392b48 map.clear()
        cycle_start = list(cur)                     # x0 of this Powell cycle
        start_cost = cost_of.get(tuple(cycle_start), _best()[1])

        # ---- Loop::run @0x392c56 (one nested LineMinimize per direction) ----
        # The map is keyed by the *per-direction* cost decrease delta_i = f(x_i-1)
        # - f(x_i) (classic Powell), not by the decrease w.r.t. the cycle start:
        # native Powell/rosen keeps dirs[1] = e1 in cycle 1 because delta_1 =
        # 0.771136-0.707536 = 0.0636 < delta_0 = 0.2289, while the cycle-start
        # form would pick delta_1 = 1-0.707536 and shift (which is what the
        # extracted version did).  std::map::operator[] overwrites, so equal
        # deltas keep the *later* direction index (native Powell/quad).
        prev_cost = start_cost
        while cursor < n:
            top_ix, top_cost = yield from _line_minimize(dirs[cursor])
            # ---- Call<LineMinimizeDone>::run 0x39327a, spec_index < 0 ---------
            # 0x393448 `population->top()`  ->  [rbp-0x70]
            # 0x39346e top->cost()          ->  [rbp-0x98]
            # 0x393491 current_point->cost() -> xmm0
            # 0x393496 `comisd xmm0,[rbp-0x98]` / 0x39349e `seta` / 0x3934a3 `je`
            # current_point is replaced by the population top **only on a
            # strictly lower cost**.  An equal-cost top leaves Powell's current
            # point alone; the next nested line search still starts from
            # population->top(), so the two do drift apart (native
            # Powell/valley cycle 4 opens with a line through (17,15) while
            # current_point is still (17,16)).
            if cost_of.get(tuple(cur), top_cost) > top_cost:
                cur = list(top_ix)
            fval = cost_of.get(tuple(cur), top_cost)    # state+0x48 @0x392ceb
            fmapped[prev_cost - fval] = cursor          # @0x39357d
            prev_cost = fval
            cursor += 1                                 # 0x39370b

        # ---- LoopDone::run @0x393934 --------------------------------------
        # LoopDone 0x39396d compares state->50 (current) with state->a8 (x0 of the
        # cycle) by pointer *and* by values; a cycle that ends on the same *cost*
        # it started with is the same test once equal-cost ties are involved
        # (native Powell/valley stops exactly here: (76,75) vs (75,76), both 7.1).
        if cur == cycle_start or fval == start_cost:          # 0x39396d / by cost
            if accurate_mode:                                # 0x393bf3
                dirs = []
                continue
            if debug_level:
                print("[Powell] no progress at %s" % (cur,))
            return _best()[0]

        if spec_index < 0 and start_cost > 0.0:              # 0x3941c8 / 0x3941d9
            if min(fmapped) / start_cost > STAGNANT_COST_IMPROVEMENT:  # 0x39422b
                if debug_level:
                    print("[Powell] stagnant %.6g" % (min(fmapped) / start_cost))
                dirs = []                                    # 0x3942d1
                continue

        v = [cur[i] - cycle_start[i] for i in range(n)]      # 0x3943c0
        s = sum(t * t for t in v)                            # 0x394430
        if s == 0.0:                                         # 0x394451
            dirs = []
            continue

        k = max(fmapped, key=lambda f: f)                    # map.rbegin()->second
        k = fmapped[k]                                       # 0x3945ce
        while k != 0:                                        # 0x3945df
            dirs[k] = list(dirs[k - 1])                      # 0x394698 dirs[k] = dirs[k-1]
            k -= 1
        norm = math.sqrt(s)
        dirs[0] = [t / norm for t in v]                      # 0x394750


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

    def cost(ix):
        """batches are *index* vectors (same contract as pydrive / every other
        extracted module: `value = domain.from_index(ix)`)."""
        nfev[0] += 1
        p = dom.from_index(ix)
        seen[tuple(int(v) for v in ix)] = p[0] * p[0] + p[1] * p[1]
        return seen[tuple(int(v) for v in ix)]

    gen = run(dom, None, start=[0, 0])
    batch = next(gen)
    while True:
        try:
            batch = gen.send([cost(ix) for ix in batch])
        except StopIteration as stop:
            best = stop.value
            break
    print("Powell final=%s cost=%.6e nfev=%d"
          % (best, seen[tuple(int(v) for v in best)], nfev[0]))
