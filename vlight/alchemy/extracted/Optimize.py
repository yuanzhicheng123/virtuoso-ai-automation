"""Optimize — extracted from libalchemy.so  (algs/Optimize.cpp, registered "Optimization" @0x56da98).

This is the two-way *dispatcher*: it owns no search of its own, it picks Global or Local and
wraps the choice in `Call<Optimize::Done>`.

state machine (VMA addresses):

    Optimize::Init   @0x364b2e -> Optimize::Start      Data(Params) @0x365256, Start ctor @0x364bb4
    Optimize::Start  @0x364ca0 -- algorithm == "neocircuitGlobal" @0x364cd4
                              \-- algorithm == "PDE"             @0x364cf7
                                   -> new Global::Init(State(this))          @0x364d40
                                      child.params = this->params            @0x364d7d/0x364d87
                                      return Call<Optimize::Done>(...)->run() @0x364de0
                               -- otherwise
                                   -> new Local::Init(State(this))           @0x364e84
                                      child.params = this->params            @0x364ec1/0x364ecb
                                      return Call<Optimize::Done>(...)->run() @0x364f24
    Optimize::Done   @0x3650c8 -> throw ShortCircuits::Return   @0x3650f7  (Call continuation)

hyperparams — `Optimize::Data::Data(Params)` @0x365256 (Data starts at AlgState+0x40):
    key               type                                                    default        read @
    current_point     shared_ptr<DesignPoint>                                new point      0x3652f8
    algorithm         shared_ptr<string>                                     "" (new string) 0x3654f9
    resultPopulation  shared_ptr<Population<shared_ptr<DesignPoint>,less>>    -             0x36564b

  Field offsets inside AlgState: current_point +0x60, algorithm +0x70, resultPopulation +0x80
  (`Start::run` reads `[this+0x70]` for the algorithm string @0x364cb7/0x364cda).

.rodata: "neocircuitGlobal" @0x56dace, "PDE" @0x56dadf.

generator protocol: `batch = yield points` -> `list[float]`; returns the best index list of
whichever stage ran (`Call<Optimize::Done>` simply re-raises `Return` after running `Done`).

parity note (native/PARITY.md §9.5): Optimize owns no search -- it only news `Global::Init`
(0x364d40) or `Local::Init` (0x364e84) from the same DSO, so the stages are the sibling modules
and the driver's start evaluation is emitted by the *stage*, not here (the refPoint is already
queued by `FunctionOptimizer::run` @0x3ce89e before the first step).  Default
`algorithm == ""` -> Local -> `nfev=1` with the start vector, which is what the library reports.
The `algorithm` hyperparameter is `shared_ptr<string>` and cannot be injected through
`harness_manual`, so only the Local route is natively verified.
"""

import importlib.util
import inspect
import pathlib
import warnings

MAX_STATE = 0x90          # `new Optimize::Start` @0x364b9a
CALL_SIZE = 0x50          # `new Call<Optimize::Done>` @0x364dc6
ALGORITHM_OFF = 0x70      # shared_ptr<string> algorithm inside AlgState  @0x364cb7
GLOBAL_NAMES = ("neocircuitGlobal", "PDE")   # @0x56dace / @0x56dadf


def _pget(params, key, default=None):
    if params is None:
        return default
    if isinstance(params, dict):
        return params.get(key, default)
    get = getattr(params, "get", None)
    if get is not None:
        return get(key, default)
    return default


_SIBLINGS = {}


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
    Optimize.py).  It is announced with a `warnings.warn` so the substitution is never silent.
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


def _stage(sub, name):
    if name in (sub or {}):
        return sub[name]
    sib = _sibling_stage(name)
    if sib is not None:
        return sib
    warnings.warn(f"Optimize: no sub[{name!r}] factory and no algorithms/{name}.py, "
                  f"using the single-point fallback", RuntimeWarning, stacklevel=2)
    return _fallback_stage(name)


def _invoke(factory, domain, goal, start, params, sub):
    """sub[name] is a generator-factory; tolerate plain generator functions too."""
    try:
        return factory(domain=domain, goal=goal, start=start, params=params, sub=sub)
    except TypeError:
        try:
            return factory(domain, goal, start, params)
        except TypeError:
            return factory(domain, goal=start, params=params)


def pick_stage(algorithm) -> str:
    """@0x364ccd..0x364d00: strcmp against the two global algorithm names."""
    return "Global" if algorithm in GLOBAL_NAMES else "Local"


def run(domain, goal, start=None, params=None, sub=None):
    """orchestrates sub-algorithms; yields their points through; returns best index list."""
    algorithm = _pget(params, "algorithm", "") or ""
    stage = pick_stage(str(algorithm))

    sub = sub or {}

    # @0x364d20/0x364d40 (Global)  /  @0x364e64/0x364e84 (Local)
    #   intrusive_ptr<State>(this) is handed to the child Init; params are copied over
    #   (@0x364d87 / @0x364ecb), then Call<Optimize::Done>::run() drives it.
    child = _invoke(_stage(sub, stage), domain, goal, start, params, sub)
    best = yield from child

    # @0x3650c8 Optimize::Done::run -> throw ShortCircuits::Return  (the Call continuation)
    if best is None:
        best = start
    return list(best) if best is not None else None


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


def _local_stage(domain=None, goal=None, start=None, params=None, sub=None):
    """stand-in for Local::Init (coordinate descent, returns best index list)."""
    cur = list(start) if start else list(domain.nominal_index())
    cbest = (yield [cur])[0]
    improved = True
    while improved:
        improved = False
        for d in range(len(domain.tunables)):
            for delta in (-2, -1, 1, 2):
                cand = list(cur)
                cand[d] = min(max(cand[d] + delta, 0), domain.tunables[d] - 1)
                if cand[d] == cur[d]:
                    continue
                c = (yield [cand])[0]
                if c < cbest:
                    cbest, cur, improved = c, cand, True
                    break
    return cur


def _global_stage(domain=None, goal=None, start=None, params=None, sub=None):
    """stand-in for Global::Init (deterministic scatter of `population` samples)."""
    n = int(_pget(params, "population", 20) or 20)
    span = [t - 1 for t in domain.tunables]
    pts = []
    for i in range(n):
        ix = [0] * len(span)
        for d, s in enumerate(span):
            ix[d] = (i * (d * 7 + 3) * 11 + 17 * (d + 1)) % (s + 1)
        pts.append(ix)
    batch = pts
    best, best_c = None, float("inf")
    while batch:
        costs = yield batch
        for ix, c in zip(batch, costs):
            if c < best_c:
                best, best_c = list(ix), c
        batch = []
    if start is not None:
        c = (yield [list(start)])[0]
        if c < best_c:
            best, best_c = list(start), c
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
    sub = {"Local": _local_stage, "Global": _global_stage}

    for algo in ("", "neocircuitGlobal", "PDE"):
        stage = pick_stage(algo)
        params = {"algorithm": algo, "population": 24, "global_effort": 6}
        best, cost, nfev = _drive(run(dom, _f, start=[3, 37], params=params, sub=sub), dom, _f)
        tag = algo if algo else '"" (default -> Local)'
        print(f"Optimize self-test  algorithm={tag:26s} -> {stage:6s}"
              f" point={dom.from_index(best)} index={best} cost={cost} nfev={nfev}")
