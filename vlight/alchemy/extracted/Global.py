"""Global — extracted from libalchemy.so  (algs/Global.cpp, registered "Global Optimization" @0x566a50).

Global::Start is the *global-stage selector*: it decides between an exhaustive Sweep, PDE and a
simulated-annealing portfolio (PSA), and it rewrites a handful of hyperparameters before handing
control to the chosen stage.

state machine (VMA addresses):

    Global::Init  @0x325e40 -> Global::Start          Data(Params) @0x326cf0, Start ctor @0x3266??
    Global::Start @0x325fb2 -- exhaustive product test  @0x325fe1 .. 0x3260c8
                              -> Sweep::Init @0x32610e, params copy @0x326161,
                                 Call<Global::Done> @0x3261cc
                           -- algorithm == "PDE" @0x326311
                              -> PDE::Init @0x32641d, params @0x326470, Call<Global::Done> @0x3264db
                           -- otherwise PSA
                              -> PSA::Init @0x326768, params @0x3267b8, Call<Global::Done> @0x326817
    Global::Done  @0x326b82 -> throw ShortCircuits::Return @0x326bb1

hyperparams — `Global::Data::Data(Params)` @0x326cf0 (Data at AlgState+0x40):
    key                type                       default        AlgState off   read @
    sortingVariables   shared_ptr<bool>           false          0x40           0x326da5
    exhaustiveLimit    shared_ptr<int>            1              0x50           0x326f8f
    global_effort      shared_ptr<int>            -1             0x60           0x327180
    algorithm          shared_ptr<string>         ""             0x70           0x327371
    current_point      shared_ptr<DesignPoint>    new point      0x80           0x327567

writes performed by Start::run (into this->params, then copied into the child):
    "hammersley_points" = 0            @0x3262db   only when current_point->[+0xd0] != 0 (warm start)
    "populationSize"    = global_effort @0x3263be  only for PDE and only when global_effort > 0
    "annealers"         = n            @0x3265f5   PSA only
    "population"        = Population(10*n) @0x32668d PSA only
    "sortingVariables"  = *sortingVariables @0x326715 PSA only

.rodata: "PDE" @0x566abd, "hammersley_points" @0x566aab, "populationSize" @0x566ac1,
         "annealers" @0x566ad0, "population" @0x566ada, "sortingVariables" @0x566a64.

parity notes (native/PARITY.md §9.3), all measured against `./native/harness Global`:
  * the three stages are the *sibling modules* of the same name (the library news the state
    machines from the same DSO at 0x32610e / 0x32641d / 0x326768), so the single-point
    `_fallback_stage` that used to answer every call is now only a last resort.  That stub is
    why the extraction used to report nfev=1 where the library burns the whole budget.
  * `is_warm` reads `current_point->id` (+0xd0, 0x326265), *not* "a start was supplied":
    `DesignPointFactoryImpl::create` (0x3cef6e) always passes id = 0 (0x3cefa4), so the
    driver's refPoint never writes `hammersley_points = 0`.
  * the driver's own start evaluation (0x3ce89e enqueue / 0x3cead3 call) is supplied by
    `_stage_output` unless the stage models it (`_driver_start`, or PSA's
    `WaitForStartingPoint` 0x39d79a which skips an already-evaluated `current_point`).
  * verified: `Global == PSA` for exhaustiveLimit 1/100/440, `Global == Sweep` for 441+,
    144/144 driver-layer cells (native/variants/cmp_drivers.py) and an 18/18 exhaustive-branch
    matrix; the PSA branch's point sequence is inherited from PSA.py.

generator protocol: `batch = yield points` -> `list[float]`; returns the stage's best index list.
"""

import importlib.util
import inspect
import pathlib
import warnings
from typing import Any, Dict, List

MAX_STATE = 0x60          # `new Global::Init` @0x3260f7 / `new Global::Start` @0x3260??
CALL_SIZE = 0x50          # `new Call<Global::Done>` @0x3261b2
DEFAULT_ANNEALERS = 9     # `mov eax, 9` @0x326591
POP_FACTOR = 10           # (n<<2 + n) << 1  @0x326620..0x326625

DEFAULTS = {
    "sortingVariables": False,   # @0x326de4  byte 0
    "exhaustiveLimit": 1,        # @0x326fce  dword 1
    "global_effort": -1,         # @0x3271bf  dword -1
    "algorithm": "",             # @0x3273b6  new std::string()
}

PDE = "PDE"                  # @0x566abd


def _pget(params, key, default=None):
    if params is None:
        return default
    if isinstance(params, dict):
        return params.get(key, default)
    get = getattr(params, "get", None)
    if get is not None:
        return get(key, default)
    return default


def _flat(params) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    if params is None:
        return out
    if isinstance(params, dict):
        out.update(params)
        return out
    for k in DEFAULTS:
        marker = object()
        v = _pget(params, k, marker)
        if v is not marker:
            out[k] = v
    return out


def _as_index(domain, value) -> List[int]:
    """index vector of a start point (`shared_ptr<DesignPoint>`-like / dict / list)."""
    if value is None:
        return list(domain.nominal_index()) if hasattr(domain, "nominal_index") \
            else [t // 2 for t in domain.tunables]
    if isinstance(value, dict):
        return list(domain.to_index(value))
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    for attr in ("values", "index", "idx"):
        seq = getattr(value, attr, None)
        if isinstance(seq, (list, tuple)) and seq:
            return [int(v) for v in seq]
    return list(domain.nominal_index()) if hasattr(domain, "nominal_index") \
        else [t // 2 for t in domain.tunables]


class DriverPoint:
    """The `DesignPoint` the *driver* builds for `refPoint` and evaluates.

    `FunctionOptimizer::run` calls `factory->create()` (@0x3ce89e), copies `refPoint` into
    `values` (+0xd8, 0x3ce905..0x3ce91b), stores it as `params["current_point"]` (0x3ce958)
    and later writes its cost (`setCost` @0x3ceaff).  `id` (+0xd0) stays 0 because
    `DesignPointFactoryImpl::create` always passes 0 (0x3cefa4).  Only the surface the
    sibling stages read is modelled: `.ix` / `.values`, `.id`, `.cost`, `isEvaluated()`.
    """

    __slots__ = ("ix", "cost", "id")

    def __init__(self, ix, cost=None, ident: int = 0):
        self.ix = list(ix)
        self.cost = cost
        self.id = int(ident)

    @property
    def values(self):
        return list(self.ix)

    def isEvaluated(self):
        return self.cost is not None

    def __repr__(self):
        return "DriverPoint(%r, cost=%r, id=%r)" % (self.ix, self.cost, self.id)


class Population:
    """`Alchemy::Population<shared_ptr<DesignPoint>, less>` — best-N keeper.

    `Population(unsigned)` ctor @0x2efdf0; `insertAndDiscardExtras` @0x2f0d4c; `top` @0x2efeea.
    """

    def __init__(self, capacity: int = 1):
        self.capacity = max(1, int(capacity))
        self.items: List[tuple] = []          # [(cost, index-list)], ascending

    def insert_and_discard_extras(self, ix, cost):
        self.items.append((float(cost), list(ix)))
        self.items.sort(key=lambda t: t[0])
        del self.items[self.capacity:]

    def top(self):
        return list(self.items[0][1]) if self.items else None

    def top_cost(self):
        return self.items[0][0] if self.items else float("inf")

    def __len__(self):
        return len(self.items)


def exhaustive_product_test(domain, limit: int) -> bool:
    """@0x325fe1 .. 0x3260c3:  forall i  tunables[i] <= limit  &&  prod(tunables) <= limit."""
    total, ok = 1, True
    for t in domain.tunables:
        total *= t                                    # @0x32604b imul
        if limit < t:                                 # @0x326090 setl
            ok = False
        if total > limit:                             # @0x3260b3 setg
            ok = False
    return ok


# ---------------------------------------------------------------------------
# sub-stage plumbing: the real library constructs the sibling state machine
# (`new Sweep::Init(...)` @0x32610e, `new PDE::Init(...)` @0x32641d,
#  `new PSA::Init(...)` @0x326768) and wraps it in `Call<Global::Done>`.  Here the
# sibling *modules* are the state machines, so they are imported on demand.
# ---------------------------------------------------------------------------
_SIBLINGS: Dict[str, Any] = {}


def _sibling_module(stem: str):
    """Import algorithms/<stem>.py next to this file (cached); None when absent."""
    if stem in _SIBLINGS:
        return _SIBLINGS[stem]
    path = pathlib.Path(__file__).resolve().parent / ("%s.py" % stem)
    mod = None
    if path.exists():
        try:
            spec = importlib.util.spec_from_file_location("algo_%s" % stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:                                # noqa: BLE001 - keep extraction usable
            mod = None
    _SIBLINGS[stem] = mod
    return mod


def _sibling_stage(stem):
    """generator-factory wrapping algorithms/<stem>.py's run() (None if unavailable)."""
    mod = _sibling_module(stem)
    if mod is None or not hasattr(mod, "run"):
        return None
    try:
        wants_sub = "sub" in inspect.signature(mod.run).parameters
    except (TypeError, ValueError):          # builtin / C callable: assume the plain form
        wants_sub = False

    def factory(domain=None, goal=None, start=None, params=None, sub=None):
        if wants_sub:                        # the stage can stack its own sub-algorithms
            return mod.run(domain, goal, start=start, params=params, sub=sub)
        return mod.run(domain, goal, start=start, params=params)

    factory.__name__ = "_sibling_%s" % stem
    # does the stage emit the driver's start evaluation itself? (Sweep.py does, PSA.py does
    # not yet; see _stage_output)
    factory.models_driver_start = hasattr(mod, "_driver_start")
    return factory


def _fallback_stage(name):
    """Last-resort stage: a single evaluated point.

    Only used when the sibling module is missing (e.g. a distribution that ships just
    Global.py).  It is announced with a `warnings.warn` so the substitution is never silent.
    """
    def factory(domain=None, goal=None, start=None, params=None, sub=None):
        ix = start
        if ix is None:
            ix = domain.nominal_index() if hasattr(domain, "nominal_index") \
                else [t // 2 for t in domain.tunables]
        yield [list(ix)]
        return list(ix)
    factory.__name__ = "_fallback_" + name
    factory.models_driver_start = False
    return factory


def _stage(sub, name):
    if name in (sub or {}):
        return sub[name]
    sib = _sibling_stage(name)
    if sib is not None:
        return sib
    warnings.warn(f"Global: no sub[{name!r}] factory and no algorithms/{name}.py, "
                  f"using the single-point fallback", RuntimeWarning, stacklevel=2)
    return _fallback_stage(name)


def _invoke(factory, domain, goal, start, params, sub):
    try:
        return factory(domain=domain, goal=goal, start=start, params=params, sub=sub)
    except TypeError:
        try:
            return factory(domain, goal, start, params)
        except TypeError:
            return factory(domain, goal=start, params=params)


def _models_driver_start(factory) -> bool:
    """True when the stage itself evaluates the driver's start point first."""
    v = getattr(factory, "models_driver_start", None)
    if v is not None:
        return bool(v)
    g = getattr(factory, "__globals__", None)
    if isinstance(g, dict):
        return "_driver_start" in g
    return False


def design_point_id(value) -> int:
    """`DesignPoint::id` at +0xd0 (read @0x326265, tested @0x326272)."""
    if value is None:
        return 0
    if isinstance(value, dict):
        for k in ("id", "point_id"):
            if k in value and isinstance(value[k], int):
                return int(value[k])
        return 0
    for attr in ("id", "point_id", "dp_id"):
        v = getattr(value, attr, None)
        if isinstance(v, int):
            return int(v)
    return 0


def is_warm(params=None, start=None) -> bool:
    """@0x326252..0x326272: warm start iff `current_point` exists **and** `id != 0`.

    `DesignPointFactoryImpl::create()` (0x3cef6e) always builds `DesignPoint(nVars, 0)`
    -- the second ctor argument is the id -- and `FunctionOptimizer::run` creates the
    refPoint through that same call (@0x3ce89e), so the id of every driver-supplied point
    is 0.  `start` is therefore *not* part of the test (the earlier revision treated a
    non-None `start` as a warm start and wrongly set `hammersley_points = 0`).
    """
    return design_point_id(_pget(params, "current_point")) != 0


def _stage_output(factory, domain, goal, cur, params, sub):
    """Run one stage and give it the driver's start evaluation when it needs it.

    `FunctionOptimizer::run` creates the refPoint DesignPoint and enqueues it (@0x3ce89e..
    0x3ce958) *before* `Factory::create` (@0x3ce9cb), so the first `cost_fn` call of every
    run is the start point (pop @0x3cea95, call @0x3cead3).  Stages that model that call
    export `_driver_start` (Sweep.py); for the rest the evaluation is emitted here, once,
    exactly like the driver would, and the child is told the point is already evaluated.
    """
    child = _invoke(factory, domain, goal, cur, params, sub)
    if not _models_driver_start(factory):
        cost = (yield [list(cur)])[0]
        if isinstance(params, dict):
            # `Params["current_point"]` is a DesignPoint in the engine, never a
            # bare descriptor: a host that hands us {"id": N} is only saying
            # "this is the driver's refPoint, warm-started with id N"
            # (Global::Start::run reads current_point->[+0xd0] @0x326265).  Keep
            # the id, but publish the real, evaluated point to the child.
            prev = params.get("current_point")
            if not isinstance(prev, DriverPoint) or prev.cost is None:
                params["current_point"] = DriverPoint(cur, cost, design_point_id(prev))
                params["current_point_cost"] = cost
    return (yield from child)


def run(domain, goal, start=None, params=None, sub=None):
    """orchestrates sub-algorithms; yields their points through; returns best index list.

    sub factories used:  "Sweep", "PDE", "PSA" — by default the sibling extracted modules
    of the same name are used (`Global::Start` builds `Sweep::Init` / `PDE::Init` /
    `PSA::Init` from the same DSO), so `sub` is only needed to override one of them.
    """
    p = _flat(params)
    sub = dict(sub or {})
    limit = int(p["exhaustiveLimit"])
    effort = int(p["global_effort"])
    cur = _as_index(domain, start if start is not None else _pget(params, "current_point"))

    # ---- @0x325fe1 exhaustive product test -------------------------------
    if exhaustive_product_test(domain, limit):
        return list((yield from _stage_output(_stage(sub, "Sweep"), domain, goal, cur, p, sub)))

    # ---- @0x326252 warm start -> hammersley_points = 0 --------------------
    # (only for host points with id != 0; see is_warm -- the driver's refPoint has id 0)
    child_params = dict(p)
    if is_warm(params, start):
        child_params["hammersley_points"] = 0          # @0x3262db (int 0)

    # ---- @0x326311 algorithm == "PDE" -------------------------------------
    if p["algorithm"] == PDE:
        if effort > 0:
            child_params["populationSize"] = effort    # @0x3263be (unsigned)
        return list((yield from _stage_output(_stage(sub, "PDE"), domain, goal, cur,
                                               child_params, sub)))

    # ---- @0x326561 PSA ----------------------------------------------------
    annealers = effort if effort > 0 else DEFAULT_ANNEALERS       # @0x326578 / 0x326591
    child_params["annealers"] = annealers                         # @0x3265f5 (unsigned)
    pop = Population(POP_FACTOR * annealers)                      # @0x326635 (Population(10n), 0x38 B)
    child_params["population"] = pop                              # @0x32668d
    child_params["sortingVariables"] = bool(p["sortingVariables"])  # @0x326715
    return list((yield from _stage_output(_stage(sub, "PSA"), domain, goal, cur,
                                          child_params, sub)))


# ---------------------------------------------------------------------------
# self test: 2-D quadratic, 41 levels per dimension
# ---------------------------------------------------------------------------
LEVELS = 41


class _Domain2D:
    tunables = [LEVELS, LEVELS]
    lo, step = -10.0, 0.5

    def from_index(self, ix):
        return {f"x{i}": self.lo + self.step * ix[i] for i in range(2)}

    def to_index(self, pt):
        return [int(round((pt[f"x{i}"] - self.lo) / self.step)) for i in range(2)]

    def nominal_index(self):
        return [20, 20]


def _f(point):
    x, y = point["x0"], point["x1"]
    return (x - 1.5) ** 2 + (y + 2.5) ** 2      # optimum at index (23, 15)


def _sweep_stage(domain=None, goal=None, start=None, params=None, sub=None):
    """stand-in for Sweep::Init: full cartesian product, returns best index list."""
    import itertools
    grid = list(itertools.product(*[range(t) for t in domain.tunables]))
    best, best_c = None, float("inf")
    for i in range(0, len(grid), 64):
        batch = [list(g) for g in grid[i:i + 64]]
        costs = yield batch
        for ix, c in zip(batch, costs):
            if c < best_c:
                best, best_c = list(ix), c
    return best


def _scatter_stage(name):
    def factory(domain=None, goal=None, start=None, params=None, sub=None):
        """stand-in for PSA::Init / PDE::Init: `population`-sized deterministic scatter."""
        ps = _pget(params, "populationSize", None)          # PDE: set by Global::Start @0x3263be
        if ps is not None:
            n = int(ps)
        else:
            n = int(_pget(params, "population", 10) or 10)
            if isinstance(params, dict) and isinstance(params.get("population"), Population):
                n = params["population"].capacity
        span = [t - 1 for t in domain.tunables]
        base = list(start) if start else list(domain.nominal_index())
        pts = []
        for i in range(n):
            ix = []
            for d, s in enumerate(span):
                if i == 0:
                    ix.append(base[d])
                else:
                    ix.append((base[d] + (i * (d * 7 + 3) * 13 + 29 * (d + 1))) % (s + 1))
            pts.append(ix)
        best, best_c = None, float("inf")
        for i in range(0, len(pts), 32):
            batch = pts[i:i + 32]
            costs = yield batch
            for ix, c in zip(batch, costs):
                if c < best_c:
                    best, best_c = list(ix), c
        return best
    factory.__name__ = f"_{name.lower()}_stage"
    return factory


def _drive(gen, domain, f):
    nfev, best, best_cost = 0, None, float("inf")
    batch = next(gen)
    while True:
        costs = []
        for ix in batch:
            c = f(domain.from_index(ix))
            costs.append(c)
            nfev += 1
            if c < best_cost:
                best, best_cost = list(ix), c
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            return (stop.value or best), best_cost, nfev


if __name__ == "__main__":
    dom = _Domain2D()
    sub = {"Sweep": _sweep_stage, "PDE": _scatter_stage("PDE"), "PSA": _scatter_stage("PSA")}

    # `is_warm` is about `current_point->id` (+0xd0, 0x326265), NOT about having a start:
    # `DesignPointFactoryImpl::create` (0x3cef6e) always passes id = 0, so the driver's
    # refPoint is never "warm" and `hammersley_points = 0` (0x3262db) is not written on this
    # path.  A host-injected history point (id != 0) is the only way to reach it.
    warm_point = DriverPoint([3, 37], cost=221.0, ident=7)

    cases = [
        ("Sweep (exhaustiveLimit=2000)", {"exhaustiveLimit": 2000}, None),
        ("PDE   (global_effort=6)", {"exhaustiveLimit": 1, "algorithm": "PDE",
                                    "global_effort": 6}, [20, 20]),
        ("PSA   (global_effort=-1 -> 9)", {"exhaustiveLimit": 1, "global_effort": -1}, [3, 37]),
        ("PSA   (global_effort=4)", {"exhaustiveLimit": 1, "global_effort": 4}, [3, 37]),
        ("PSA   (warm: current_point.id=7)",
         {"exhaustiveLimit": 1, "global_effort": 4, "current_point": warm_point}, [3, 37]),
    ]
    for label, params, start in cases:
        gen = run(dom, _f, start=start, params=params, sub=sub)
        best, cost, nfev = _drive(gen, dom, _f)
        warm = is_warm(params, start)
        print(f"Global self-test  {label:32s} point={dom.from_index(best)} "
              f"index={best} cost={cost} nfev={nfev} warm={warm}")
    print("  (PSA params: annealers = effort>0 ? effort : 9 ; population = Population(10*annealers) ;"
          " hammersley_points = 0 only when current_point->id != 0 @0x326265)")
    print("  (a plain `start` is NOT a warm start: the driver's refPoint has id 0 @0x3cefa4)")
