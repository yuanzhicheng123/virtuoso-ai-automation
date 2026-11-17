"""Local — extracted from libalchemy.so  (algs/Local.cpp, registered "Local Optimization" @0x569498).

Local is a *gate ladder*: one cheap bookkeeping state (`LocalOptStart`) followed by four
`...Done` states, each of which either launches one sub-algorithm or falls through to the next.
Every launched stage receives exactly the same two objects: `current_point = population->top()`
and `population = resultPopulation`.

state machine (VMA addresses):

    Local::Init             @0x33de96 -> Local::Start          Data @0x3408cc, Start ctor
    Local::Start            @0x33e02c -- run_ofat_search (+0x100) @0x33e05d
                                    \-- run_parallel_ofat_search (+0x110) @0x33e079
                                         -> OFATSearch::Init @0x33e563 / ParallelOFATSearch::Init @0x33e402
                                            Call<Local::ExhaustiveDone> @0x33e4ad @0x33e60e
                                    -- current_point->isEvaluated() @0x33e6b5
                                         true  -> Local::LocalOptStart   @0x33e6f8
                                         false -> Local::StartStartingPoint @0x33e793
    Local::StartStartingPoint @0x33ead2 -- !isEvaluated -> self @0x33eb17
                                        -- evaluated    -> LocalOptStart @0x33eb51
    Local::LocalOptStart   @0x33ebf6 -- exhaustive product test (+0x60) @0x33ec95..0x33ed10
                                        -> Sweep::Init @0x33ed52, Call<Local::ExhaustiveDone> @0x33ee10
                                     -- population->insertAndDiscardExtras(*current_point) @0x33eec1
                                        this->[+0x40] = population->top()              @0x33eef7
                                     -- run_brent_powell (+0xd0) @0x33ef16
                                        -> Powell::Init @0x33f0e5, params @0x33f131, Call<BrentPowellDone> @0x33f190
                                     -- else Local::BrentPowellDone::run() @0x33f255
    Local::BrentPowellDone @0x33f4d8 -- run_conjugate_gradient (+0xe0) @0x33f50e
                                        -> ConjugateGradient::Init @0x33f6dd, Call<ConjugateGradientDone> @0x33f788
                                     -- else Local::ConjugateGradientDone::run() @0x33f84d
    Local::ConjugateGradientDone @0x33fa2e -- run_bfgs (+0xf0) @0x33fa64
                                        -> BFGS::Init @0x33fc33, Call<Local::BFGSDone> @0x33fcde
                                     -- else Local::BFGSDone::run() @0x33fca3
    Local::BFGSDone        @0x33ff84 -- run_hooke_jeeves (+0x120) @0x33ffba
                                        -> HookeJeeves::Init @0x340189, Call<HookeJeevesDone> @0x340234
                                     -- else Local::HookeJeevesDone::run() @0x3401f9
    Local::HookeJeevesDone @0x3404da -- run_until_no_improvement (+0x130) && top() != this->[+0x40]
                                        -> new Local::Start(AlgState(this)) @0x340579   (full restart)
                                     -- else throw ShortCircuits::Return @0x3405eb
    Local::ExhaustiveDone  @0x340642 -> throw ShortCircuits::Return @0x340671

hyperparams — `Local::Data::Data(Params)` @0x3408cc (Data at AlgState+0x40, PrivateData 0x00-0x1f):
    key                        type                  default            AlgState off   read @
    exhaustiveLimit            shared_ptr<int>       100                0x60           0x340a2a
    debug_level                shared_ptr<int>       0                  0x70           0x340c1b
    accuracy_level             shared_ptr<int>       0                  0x80           0x340e0c
    fractional_factor          shared_ptr<unsigned>  5                  0x90           0x340ffd
    current_point              shared_ptr<DP>        new DP             0xa0           0x3411ee
    resultPopulation           shared_ptr<Population> Population(1)     0xb0           0x3413ef
    stop_on_error              shared_ptr<bool>      false              0xc0           0x3415f2
    run_brent_powell           shared_ptr<bool>      false              0xd0           0x3417e3
    run_conjugate_gradient     shared_ptr<bool>      false              0xe0           0x3419d4
    run_bfgs                   shared_ptr<bool>      false              0xf0           0x341bc5
    run_ofat_search            shared_ptr<bool>      false              0x100          0x341db6
    run_parallel_ofat_search   shared_ptr<bool>      false              0x110          0x341fa7
    run_hooke_jeeves           shared_ptr<bool>      false              0x120          0x342198
    run_until_no_improvement   shared_ptr<bool>      false              0x130          0x342389
    PrivateData also holds two `shared_ptr<DesignPoint>` (@0x340874/0x340884/0x340897): +0x40 is the
    "best at last LocalOptStart" snapshot, +0x50 is [unproven] (never read in 0x33dde5-0x345a4a).

Sub-stage params (rebuilt from scratch by every ladder state, `Params::init()` first):
    Powell / ConjugateGradient / BFGS / HookeJeeves : stop_on_error @0xc0, current_point=top() @0xb0,
                                                      population=resultPopulation @0xb0
    OFATSearch                                      : + fractional_factor @0x90, current_point, population
    ParallelOFATSearch                              : + debug_level @0x70, accuracy_level @0x80
Termination: every `...Done` state throws `ShortCircuits::Return` unless its gate is open, so with
all gates false the ladder is a single pass of bookkeeping and Local returns immediately.

generator protocol: `batch = yield points` -> `list[float]`; returns the best index list.

parity note (native/PARITY.md §9.5, §12.4, §15): with every gate closed the ladder is pure
bookkeeping and the run is exactly one evaluation: the driver's own refPoint
(0x3ce89e enqueue -> 0x3cead3 call), emitted here at `StartStartingPoint` 0x33ead2.
`Local == 1/1 same point` for dim 2/3/4 x quad/rosen/valley x start 0/center.
Seven gates are `shared_ptr<bool>` and are injectable since §12 (`--param run_*=hpb:1`), which
reaches every branch below; §15 closes the last two:
  * the restart edge -- `HookeJeevesDone::run` 0x3404f8 gate && 0x34052c
    `std::operator!=<DesignPoint,DesignPoint>` (shared_ptr identity) -> 0x340579
    `new Local::Start(AlgState(this))`.  The snapshot is `private+0x40` = `population->top()`
    taken at 0x33eef7 right after 0x33eec1 re-inserts `Data::current_point`; because the
    restart keeps the same AlgState, +0xa0 is *not* rewound and the next round re-inserts the
    same object.  Modelled by `_DP` + `Population.insert_dp` + the `is not` test in `run`.
  * the exhaustive edge -- 0x33ed10 test true -> 0x33ed52 `new Sweep::Init`, 0x33eda5
    `Params::operator=` copies Local's whole Data into the child, and `Call<Local::ExhaustiveDone>`
    0x33ee10 ends Local at 0x340642 (throw Return).  `current_point` is already evaluated at
    that point, so native `Sweep::Start` 0x3bf71c never re-submits it; the extracted
    `algorithms/Sweep.py` always emits its driver-start call first, so this branch answers it
    out of band through `_prestart`.
"""

import importlib.util
import inspect
import pathlib
import warnings
from functools import cmp_to_key
from typing import Any, Dict, List, Optional

MAX_STATE = 0x140         # `new Local::{Init,Start,LocalOptStart,...}` @0x33e6e1, 0x33f243, ...
CALL_SIZE = 0x50          # `new Call<Local::*Done>` @0x33edf6
DEFAULT_EXHAUSTIVE_LIMIT = 100     # @0x340a55  dword 100
DEFAULT_FRACTIONAL_FACTOR = 5      # @0x341028  dword 5

DEFAULTS = {
    "exhaustiveLimit": DEFAULT_EXHAUSTIVE_LIMIT,
    "debug_level": 0,
    "accuracy_level": 0,
    "fractional_factor": DEFAULT_FRACTIONAL_FACTOR,
    "stop_on_error": False,
    "run_brent_powell": False,
    "run_conjugate_gradient": False,
    "run_bfgs": False,
    "run_ofat_search": False,
    "run_parallel_ofat_search": False,
    "run_hooke_jeeves": False,
    "run_until_no_improvement": False,
}

# ladder: (gate key, sub factory name, ...)  in execution order
LADDER = (
    ("run_brent_powell", "Powell"),
    ("run_conjugate_gradient", "ConjugateGradient"),
    ("run_bfgs", "BFGS"),
    ("run_hooke_jeeves", "HookeJeeves"),
)


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


class _DP:
    """One `Alchemy::DesignPoint` *object* — values (+0xd8), cost (+0x08), isCacheHit (+0x18).

    `Local`'s restart test compares two `std::shared_ptr<DesignPoint>`, i.e. object
    identity (0x34052c), so the extract has to keep one wrapper per created object
    instead of a bare `(cost, values)` pair.  See `Population` for why identity and
    values nevertheless agree here.
    """

    __slots__ = ("ix", "cost", "hit")

    def __init__(self, ix, cost, hit: bool = False):
        self.ix = tuple(int(v) for v in ix)
        self.cost = float(cost)
        self.hit = bool(hit)

    def __repr__(self):
        return "_DP(%s @%.17g)" % (list(self.ix), self.cost)


_LESS = []


def _pt_less():
    """`evil_ptr_less<DesignPoint>` — `DesignPoint::operator<` @0x3d1dda.

    The exact model (cost, then isCacheHit, then the *asymmetric* values hash that
    folds `other` with `this`'s rotation sequence) already lives in
    `algorithms/LineMinimize.py` (`_values_hash` / `_pt_less`, PARITY.md §4.6 and
    §14.2).  Reuse it rather than carry a third copy of the kernel; without that
    sibling the population still orders by cost, which is all the standalone
    self-test below needs.
    """
    if not _LESS:
        mod = _sibling_module("LineMinimize")
        fn = getattr(mod, "_pt_less", None) if mod is not None else None
        if fn is None:
            def fn(a, b):
                return a.cost < b.cost
        _LESS.append(fn)
    return _LESS[0]


def _pt_cmp(a, b):
    less = _pt_less()
    if less(a, b):
        return -1
    if less(b, a):
        return 1
    return 0


class Population:
    """`Alchemy::Population<shared_ptr<DesignPoint>, evil_ptr_less>` (`Population.h`).

    ctor 0x2f0d4c / `insertAndDiscardExtras` 0x2efdf0 / `top` 0x2efeea /
    `discardExtras` 0x2f17a0: a `std::set` ordered by `DesignPoint::operator<` plus a
    `maxsize_` trim that drops the **largest** elements.  `Local::Data::Data` builds
    `Population(1)` (0x3413ef) and hands the *same* object to every sub-stage
    (`Params["population"]`, key 0x569599: 0x33f04c Powell, 0x33f644 CG, 0x33fb9a BFGS,
    0x3400f0 HookeJeeves, 0x33e241 OFAT), so the ladder and its children share one slot.

    Two consequences the restart branch depends on:

    * `insert` of an object that compares **equivalent** to the current top is
      rejected by `std::set::insert`, so the old object survives — re-inserting the
      same design never changes `top()`'s identity.  Hence identity tracks *values*
      everywhere except the equal-cost/different-values tie, where the asymmetric
      hash decides and the new object can take over (`_pt_less`).
    * `LocalOptStart` re-inserts `Data::current_point` (0x33eec1) — the *same*
      object every round, because `Local::Start` never rewrites +0xa0.
    """

    def __init__(self, capacity: int = 1):
        self.capacity = max(1, int(capacity))
        self.items: List[_DP] = []

    def insert_dp(self, dp: _DP) -> None:
        """`set_.insert(dp); discardExtras();` (0x2efe0e / 0x2efe1a)."""
        less = _pt_less()
        for q in self.items:
            if not less(dp, q) and not less(q, dp):
                return                      # equivalent -> set::insert is a no-op
        self.items.append(dp)
        self.items.sort(key=cmp_to_key(_pt_cmp))
        del self.items[self.capacity:]      # discardExtras(): drop the largest

    def insert_and_discard_extras(self, ix, cost):
        """Insert a *newly created* DesignPoint with these values."""
        self.insert_dp(_DP(ix, cost))

    def top_dp(self):
        return self.items[0] if self.items else None

    def top(self):
        return list(self.items[0].ix) if self.items else None

    def top_cost(self):
        return self.items[0].cost if self.items else float("inf")

    def __len__(self):
        return len(self.items)


def exhaustive_product_test(domain, limit: int) -> bool:
    """@0x33ec95 .. 0x33ed02 (identical code to Global::Start, but on Local::Data::exhaustiveLimit)."""
    total, ok = 1, True
    for t in domain.tunables:
        total *= t
        if limit < t:
            ok = False
        if total > limit:
            ok = False
    return ok


def _as_index(domain, value) -> List[int]:
    if value is None:
        return list(domain.nominal_index()) if hasattr(domain, "nominal_index") \
            else [t // 2 for t in domain.tunables]
    if isinstance(value, dict):
        return list(domain.to_index(value))
    return [int(v) for v in value]


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
    except (TypeError, ValueError):
        wants_sub = False

    def factory(domain=None, goal=None, start=None, params=None, sub=None):
        if wants_sub:
            return mod.run(domain, goal, start=start, params=params, sub=sub)
        return mod.run(domain, goal, start=start, params=params)

    factory.__name__ = "_sibling_%s" % stem
    return factory


def _fallback_stage(name):
    """Last-resort stage: a single evaluated point.

    Only used when the sibling module is missing (e.g. a distribution that ships just
    Local.py).  It is announced with a `warnings.warn` so the substitution is never silent.
    """
    def factory(domain=None, goal=None, start=None, params=None, sub=None):
        ix = start
        if ix is None:
            ix = domain.nominal_index() if hasattr(domain, "nominal_index") \
                else [t // 2 for t in domain.tunables]
        yield [list(ix)]
        return list(ix)
    factory.__name__ = "_fallback_" + name
    return factory


def _pick_stage(sub, name):
    """`new Powell::Init` 0x33f0e5 / `new ConjugateGradient::Init` 0x33f6dd /
    `new BFGS::Init` 0x33fc33 / `new HookeJeeves::Init` 0x340189 / `new Sweep::Init` 0x33ed52 /
    `new OFATSearch::Init` 0x33e563 / `new ParallelOFATSearch::Init` 0x33e402 — each is built
    from the same DSO, so the sibling module of that name is the default sub-stage."""
    if name in (sub or {}):
        return sub[name]
    sib = _sibling_stage(name)
    if sib is not None:
        return sib
    warnings.warn(f"Local: no sub[{name!r}] factory and no algorithms/{name}.py, "
                  f"using the single-point fallback", RuntimeWarning, stacklevel=2)
    return _fallback_stage(name)


def _pop_insert_dp(pop, dp):
    """`population->insertAndDiscardExtras(shared_ptr<DesignPoint> const&)` (0x2efdf0)."""
    ins = getattr(pop, "insert_dp", None)
    if ins is not None:
        ins(dp)
    else:                                     # foreign Population-shaped object
        pop.insert_and_discard_extras(list(dp.ix), dp.cost)


def _pop_top_dp(pop):
    """The object `population->top()` (0x2efeea) points at, as an identity token."""
    get = getattr(pop, "top_dp", None)
    if get is not None:
        return get()
    top = pop.top()
    return _DP(top, pop.top_cost()) if top is not None else None     # value token


def _invoke(factory, domain, goal, start, params, sub):
    try:
        return factory(domain=domain, goal=goal, start=start, params=params, sub=sub)
    except TypeError:
        try:
            return factory(domain, goal, start, params)
        except TypeError:
            return factory(domain, goal=start, params=params)


def _record(gen, known, domain):
    """Proxy a child generator and cache every cost it sees.

    The C++ Population stores `shared_ptr<DesignPoint>` objects that already carry their cost
    (`DesignPoint::setCost`), and `DesignPointFactory::Cache` (0x3d7976 hit / 0x3d7b20 insert)
    deduplicates them; index lists do not carry a cost, so this proxy plays the cache's role.

    `domain` normalises whatever the child asks for into an index tuple: `HookeJeeves`
    yields value dicts (`domain.from_index(ix)`), `Powell`/`LineMinimize` yield index
    lists, and `tuple(dict)` would have been `('x0','x1',...)` — one bogus key, which is
    exactly how the ladder used to lose HJ's returned incumbent's cost (§15.2).
    """
    known = known if known is not None else {}
    try:
        batch = next(gen)
    except StopIteration as stop:
        return stop.value
    while True:
        costs = yield batch
        for ix, c in zip(batch, costs or []):
            known[_pt_key(domain, ix)] = c
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            return stop.value


def _pt_key(domain, pt):
    """Index tuple of a batch entry, which may be an index list or a value dict.

    The two spellings come from different children: `HookeJeeves` yields
    `domain.from_index(ix)` dicts (0x32fa18 batch builder), `Powell`/`LineMinimize`
    yield plain index lists.  `tuple(dict)` would key the *names*, so every caller
    into `_record` has to come through here.
    """
    if isinstance(pt, dict):
        return tuple(int(v) for v in domain.to_index(pt))
    return tuple(int(v) for v in pt)


def _prestart(gen, ix, cost, domain):
    """Answer a child's *first* ask for the already-evaluated current point.

    Every state Local builds hands its child
    ``Params["current_point"] = population->top()`` — Powell 0x33efd8,
    ConjugateGradient 0x33f5d0, BFGS 0x33fc33, HookeJeeves 0x340189,
    OFATSearch 0x33e217 — and that point is already evaluated, because the driver
    evaluated it once and Local inserted it into ``resultPopulation``:
    ``DesignPoint::setCost`` ran, so ``isEvaluated()`` (0x3d1728, the
    ``boost::optional<double>`` engaged flag at +0x18) is true and the child's
    ``Start`` does **not** submit it again (BFGS 0x2fe6xx, HookeJeeves 0x32f73a,
    Range P 0x394a84).  The extracted children cannot see that flag: they ask for
    their start point unconditionally, which shows up as one duplicated leading
    evaluation (native ``[(0,0),(1,0),...]`` vs python ``[(0,0),(0,0),(1,0),...]``).

    This proxy supplies the answer out of band.  Only the very first batch is
    filtered: the engine really does re-evaluate cache hits later on
    (HookeJeeves.md: 10202 evaluations for 10201 distinct points), so a later ask
    for the same design must still reach the objective.
    """
    key = tuple(int(v) for v in ix)
    try:
        batch = next(gen)
    except StopIteration as stop:
        return stop.value
    first = True
    while True:
        costs = [None] * len(batch)
        keep = []
        for i, pt in enumerate(batch):
            if first and _pt_key(domain, pt) == key:
                costs[i] = cost                 # already evaluated -> do not re-ask
            else:
                keep.append(i)
        first = False
        if keep:
            got = yield [batch[i] for i in keep]
            for i, c in zip(keep, got or []):
                costs[i] = c
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            return stop.value


def run(domain, goal, start=None, params=None, sub=None):
    """orchestrates sub-algorithms; yields their points through; returns best index list.

    sub factories: "OFATSearch", "ParallelOFATSearch", "Sweep", "Powell", "ConjugateGradient",
    "BFGS", "HookeJeeves" — only the ones whose gate is open (or the branch taken) are required.
    """
    p = _flat(params)
    sub = sub or {}
    known: Dict[tuple, float] = {}          # index-tuple -> cost  (DesignPointFactory::Cache)
    trace = p.get("trace")                  # optional mutable dict, host-side instrumentation
    pop = p.get("resultPopulation") or Population(1)
    cur = _as_index(domain, start if start is not None else p.get("current_point"))
    cur_cost: Optional[float] = p.get("current_point_cost")

    # ---- @0x33e02c Local::Start: OFAT branches ---------------------------
    if p["run_ofat_search"] or p["run_parallel_ofat_search"]:
        q = {
            "stop_on_error": p["stop_on_error"],                    # @0x33e0c7 / 0x33e115
            "fractional_factor": p["fractional_factor"],            # @0x33e14d / 0x33e19b
            "current_point": cur,                                   # @0x33e217
            "population": pop,                                      # @0x33e293
        }
        if p["run_parallel_ofat_search"]:
            q["debug_level"] = p["debug_level"]                     # @0x33e337
            q["accuracy_level"] = p["accuracy_level"]               # @0x33e3af
            name = "ParallelOFATSearch"                             # @0x33e402
        else:
            name = "OFATSearch"                                     # @0x33e563
        child = _invoke(_pick_stage(sub, name), domain, goal, cur, q, sub)
        # Local::Start 0x33e02c branches *before* StartStartingPoint, so on this
        # path the current point may still be unevaluated: OFATSearch::Start then
        # asks for it once and the driver answers (that is the single leading
        # (0,0) in the native trace).  Only a caller-supplied cost lets us answer
        # it here; otherwise the child's own ask must be forwarded unchanged.
        if cur_cost is None:
            return list((yield from _record(child, known, domain)))
        return list((yield from _record(_prestart(child, cur, cur_cost, domain),
                                         known, domain)))

    # ---- @0x33e6b5 / @0x33ead2 StartStartingPoint -------------------------
    if cur_cost is None:                       # !current_point->isEvaluated() -> self loop
        cur_cost = (yield [list(cur)])[0]
        known[tuple(cur)] = cur_cost

    # `Local::Data::current_point` (+0xa0) is ONE DesignPoint object for the whole
    # run: no state in 0x33dde5-0x342700 rewrites that slot (0x33e1c5 / 0x341b62 are
    # reads, 0x3411ee the ctor default), so the restart branch (0x340579) re-enters
    # `Local::Start` with the same AlgState and `LocalOptStart` re-inserts the *same*
    # object (0x33eec1) every round — an insert `std::set::insert` rejects as
    # equivalent whenever it is still there.
    cur_dp = _DP(cur, cur_cost)

    while True:
        # ---- @0x33ebf6 LocalOptStart: exhaustive test ---------------------
        if exhaustive_product_test(domain, int(p["exhaustiveLimit"])):
            # 0x33ed16..0x33ee10: `new Sweep::Init(AlgState(this))`, then
            # `Params::operator=` 0x33eda5 copies Local's **whole** Data map into the
            # child's Params (so Sweep sees `current_point` too, already evaluated),
            # and `Call<Local::ExhaustiveDone>` @0x33ee10 ends Local at 0x340642.
            #
            # `Local::LocalOptStart::run` runs this branch *after* StartStartingPoint
            # 0x33ead2, so current_point is always evaluated here — and native
            # `Sweep::Start::run` 0x3bf71c therefore never re-submits it: the
            # evaluation sequence is the driver's refPoint once, then the Cartesian
            # grid from (0,0) (with the refPoint repeated wherever the grid hits it).
            # The extracted `algorithms/Sweep.py` cannot see that flag and always
            # emits its driver-start call first (`_driver_start`, Sweep.py), which is
            # the extra leading point §12.9-4 recorded — answer it out of band.
            q = dict(p)                        # Params::operator= 0x33eda5
            q["current_point"] = list(cur_dp.ix)
            q["population"] = pop
            child = _invoke(_pick_stage(sub, "Sweep"), domain, goal,
                            list(cur_dp.ix), q, sub)
            return list((yield from _record(
                _prestart(child, cur_dp.ix, cur_dp.cost, domain), known, domain))
                or cur_dp.ix)

        # ---- @0x33eec1 / 0x33eef7 bookkeeping -----------------------------
        _pop_insert_dp(pop, cur_dp)            # insertAndDiscardExtras(*current_point)
        snapshot = _pop_top_dp(pop)            # this->[+0x40] = population->top()
        if snapshot is None:
            snapshot = cur_dp

        # ---- ladder: Powell -> ConjugateGradient -> BFGS -> HookeJeeves ----
        for gate, name in LADDER:
            if not p[gate]:                    # closed gate -> next `...Done` state
                continue
            base_dp = _pop_top_dp(pop)         # population->top(), 0x33efd8 / 0x33f5d0 / ...
            base_ix, base_cost = list(base_dp.ix), base_dp.cost
            q = {                              # params rebuilt by every ladder state
                "stop_on_error": p["stop_on_error"],
                "current_point": base_ix,      # @0x33efd8 / 0x33f5d0 / 0x33fc33 / 0x340189
                "population": pop,
            }
            child = _invoke(_pick_stage(sub, name), domain, goal, base_ix, q, sub)
            best = yield from _record(_prestart(child, base_ix, base_cost, domain),
                                      known, domain)
            if best is not None:
                # In the library the stage's incumbent is already in the shared
                # population — the nested line search is the writer
                # (Min_Exhaustive_Wait 0x33539f / Min_Brent_WaitPoint 0x336258), and
                # `Local` itself never re-inserts it.  Model that as the insertion of
                # a *freshly created* DesignPoint carrying the engine's cost.
                bkey = _pt_key(domain, best)
                _pop_insert_dp(pop, _DP(bkey, known.get(bkey, base_cost)))

        # ---- @0x3404da HookeJeevesDone -----------------------------------
        # 0x3404f8 gate (+0x130) && 0x34052c `std::operator!=<DesignPoint,DesignPoint>`
        # — shared_ptr **identity**, not value — -> 0x340579 `new Local::Start(AlgState)`.
        top = _pop_top_dp(pop)
        if not (p["run_until_no_improvement"] and top is not None and top is not snapshot):
            return list(top.ix) if top is not None else list(cur_dp.ix)
        # restart: new Local::Start(AlgState(this)) @0x340579 -> same ladder again
        if isinstance(trace, dict):
            trace["restarts"] = trace.get("restarts", 0) + 1


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


def _stage(width=2):
    """stand-in for Powell / ConjugateGradient / BFGS / HookeJeeves (bounded line search)."""
    def factory(domain=None, goal=None, start=None, params=None, sub=None):
        cur = list(start) if start else list(domain.nominal_index())
        cbest = (yield [cur])[0]
        for _ in range(24):
            improved = False
            for d in range(len(domain.tunables)):
                for delta in (-width, -1, 1, width):
                    cand = list(cur)
                    cand[d] = min(max(cand[d] + delta, 0), domain.tunables[d] - 1)
                    if cand[d] == cur[d]:
                        continue
                    c = (yield [cand])[0]
                    if c < cbest:
                        cbest, cur, improved = c, cand, True
                        break
            if not improved:
                break
        return cur
    return factory


def _sweep_stage(domain=None, goal=None, start=None, params=None, sub=None):
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
    search = _stage()
    sub = {"Powell": search, "ConjugateGradient": _stage(1), "BFGS": _stage(3),
           "HookeJeeves": _stage(2), "Sweep": _sweep_stage,
           "OFATSearch": _stage(1), "ParallelOFATSearch": _stage(1)}

    cases = [
        ("ladder all closed", {}),
        ("Powell->CG->BFGS->HJ", {"run_brent_powell": True, "run_conjugate_gradient": True,
                                  "run_bfgs": True, "run_hooke_jeeves": True}),
        ("Powell+HJ+restart", {"run_brent_powell": True, "run_hooke_jeeves": True,
                               "run_until_no_improvement": True}),
        ("OFAT", {"run_ofat_search": True}),
        ("ParallelOFAT", {"run_parallel_ofat_search": True}),
        ("Sweep (limit=2000)", {"exhaustiveLimit": 2000}),
    ]
    print("Local self-test: 2-D quadratic, 41 levels/dim, start [3, 37]")
    for label, gates in cases:
        box = {}
        params = dict(gates, trace=box)
        best, cost, nfev = _drive(run(dom, _f, start=[3, 37], params=params, sub=sub), dom, _f)
        print(f"  {label:22s} point={str(dom.from_index(best)):28s} index={best} "
              f"cost={cost:<6} nfev={nfev:<5} restarts={box.get('restarts', 0)}")
