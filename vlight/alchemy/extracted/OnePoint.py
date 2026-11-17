"""OnePoint — extracted from libalchemy.so  (algs/OnePoint.cpp, registered "Single Point" @0x56d794).

state machine (VMA addresses inside libalchemy.so):

    OnePoint::Init       @0x363e36  -> OnePoint::Start        builds Data(Params) then Start
    OnePoint::Start      @0x363f82  -> OnePoint::PointWait    factory->create(), point kept at this+0x40
    OnePoint::PointWait  @0x3640b2  -> PointWait              while !point->isEvaluated()   (self loop)
                                    -> ShortCircuits::Return  once  point->isEvaluated()

hyperparams: NONE.
  `OnePoint::Data::Data(Params)` @0x3641ee only runs `PrivateData::PrivateData()` @0x3641b6,
  which default-constructs one `shared_ptr<DesignPoint>`; the Params argument is dropped on
  the floor.  There is no key lookup anywhere in the OnePoint code range 0x363dc1-0x364a5d.

Two evaluations in every native run — this is the off-by-one the first port had:

    ev 0   FunctionOptimizer::run evaluates the start point before the state machine
           is entered (native/disasm_run.txt, docs/spec_core.md §6).  `harness OnePoint
           2 21 300 0 10 7 3` prints (7,3) here even though OnePoint never looks at it.
    ev 1   `Start::run` 0x363fb7 calls `DesignPointFactory::create()` (vtable slot +0x10)
           with no arguments; the factory hands back the all-zero value vector, so the
           emitted point is (0,...,0) whatever the start was.  Verified:
               ./native/harness OnePoint 2 21 300 0 10 7 3    -> ev1 [0,0]
               ./native/harness OnePoint 2 101 300 0 10 50 50  -> ev1 [0,0]
               ./native/harness OnePoint 3 21 300 0 10 7 3 5   -> ev1 [0,0,0]

    Is that "lattice index 0" or "value 0"?  **Value 0**, and it cannot be index 0:

      * `DesignPointFactoryImpl` never sees the lattice values.  Its ctor (0x3cee4a)
        stores one `v.size()` per tunable (`sizes.push_back`, 0x3ceef9..0x3cef33,
        vector at +0x8) plus the dimension (`mov 0x18(%rax?)`-style store at +0xb0,
        0x3cee8b) -- no values anywhere -- and `create()` @0x3cef6e is
        `new DesignPoint(dim (+0xb0), 0u)`;
      * `DesignPoint::DesignPoint(unsigned long n, unsigned fill)` @0x3d1464 builds
        `vector<int>(n)` (0x3d1550 `_ZNSt6vectorIiSaIiEEC1EmRKS0_`, i.e. n value-
        initialised ints = zeros) and parks `fill` at +0xd0 (0x3d1525).  So the
        emitted values are literal zeros; there is no clamping or projection onto
        the lattice (0x363fb7..0x363fcb only stores the shared_ptr at +0x40).

    Measured separation, which the harness cannot express (its lattice is built as
    `lround(100*i/(levels-1))`, so `lat[0] == 0` always): `native/variants/lattice_probe.cpp`
    calls `FunctionOptimizer::run` directly with a non-zero-based lattice and a start
    outside it.  The factory copies the sizes only, and the point still comes back as
    zeros -- outside the lattice, unprojected:

        ./native/variants/lattice_probe OnePoint 10,10 "10,20,...,100;10,20,...,100"
            {"ev":1,"values":[0,0],...}          <- lattice[0] is 10
        ./native/variants/lattice_probe OnePoint 7,3,9 "7,13,19,25,31;3,9,15,21,27;9,17,25,33,41"
            {"ev":1,"values":[0,0,0],...}        <- 0 is not even in the lattice

    Inside the harness / pydrive lattice, index 0 and value 0 are the same point, so
    every comparable trace is unaffected; the model keeps emitting index 0 because the
    generator protocol speaks indices (see native/PARITY.md §13, branch 2).

generator protocol (shared with the other extracted drivers):
    batch = yield points        # points: list of index-lists (int per tunable)
    # driver evaluates every point, sends back list[float]
    return best_index_list
"""

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
STATE_SIZE = 0x50      # `new OnePoint::Start` / `new OnePoint::PointWait`  @0x363e93 / @0x363ff8
POINT_OFF = 0x40       # shared_ptr<DesignPoint> inside AlgState            @0x363fbd / @0x3640c6


def _pget(params, key, default=None):
    """Params may be a dict, a dict-like (vlight.alchemy.Params) or None."""
    if params is None:
        return default
    if isinstance(params, dict):
        return params.get(key, default)
    get = getattr(params, "get", None)
    if get is not None:
        return get(key, default)
    return default


def _driver_start(domain, start):
    """The point `FunctionOptimizer::run` evaluates before OnePoint starts.

    FunctionOptimizer::run always performs one cost-function call for the start
    point (docs/spec_core.md §6); `harness OnePoint 2 21 300 0 10 7 3` evaluates
    (7,3) first although OnePoint's own design point is (0,0).  Emitting it here
    is what makes the evaluation sequence point-for-point comparable with the
    real library (native/parity_check.py, native/PARITY.md).
    """
    tun = [int(t) for t in domain.tunables]
    if start is None:                       # standalone default: the grid centre
        return [(t - 1) // 2 for t in tun]
    if isinstance(start, dict):
        if hasattr(domain, "to_index"):
            return list(domain.to_index(start))
        return [min(max(int(start["x%d" % i]), 0), t - 1) for i, t in enumerate(tun)]
    ix = [int(v) for v in start]
    if hasattr(domain, "clamp_index"):
        return list(domain.clamp_index(ix))
    return [min(max(v, 0), t - 1) for v, t in zip(ix, tun)]


def run(domain, goal, start=None, params=None, sub=None):
    """orchestrates sub-algorithms; yields their points through; returns best index list.

    OnePoint has no sub-algorithm and no knobs: one `create()`, one wait, `Return`.
    `sub` is accepted and ignored so this stage can sit anywhere in a stack.
    """
    n = len(domain.tunables)
    driver_ix = _driver_start(domain, start if start is not None else _pget(params, "current_point"))

    # ---- driver call #0 (FunctionOptimizer::run, before the state machine) ----
    costs = yield [domain.from_index(driver_ix)]
    best_ix, best_cost = list(driver_ix), float(costs[0])

    # @0x363f82  OnePoint::Start::run
    #   p = factory->create();                 @0x363fb7 (vtable slot +0x10)
    #   this->[+0x40] = p;                     @0x363fcb
    #   return new PointWait(AlgState(this))->run();   @0x36400c .. @0x36404e
    #   create() takes no argument: the design point is the all-zero value vector.
    ix = [0] * n
    costs = yield [domain.from_index(ix)]

    # @0x3640b2  OnePoint::PointWait::run
    #   if (!point->isEvaluated()) return intrusive_ptr<State>(this);  @0x3640f4
    #   throw ShortCircuits::Return();                                @0x36411a
    while costs and costs[0] is None:          # "not evaluated yet" -> eternal self loop
        costs = yield [domain.from_index(ix)]

    # the engine returns its best evaluated point (the driver's start can still
    # beat the design point, e.g. `harness OnePoint 2 21 300 0 10 7 3` -> (7,3))
    if float(costs[0]) < best_cost:
        best_ix, best_cost = list(ix), float(costs[0])
    return best_ix


# ---------------------------------------------------------------------------
# self test: 2-D quadratic, 41 levels per dimension
# ---------------------------------------------------------------------------
LEVELS = 41


class _Domain2D:
    tunables = [LEVELS, LEVELS]
    lo, step = -10.0, 0.5          # 41 levels <=> [-10, 10]

    def from_index(self, ix):
        return {f"x{i}": self.lo + self.step * ix[i] for i in range(2)}

    def to_index(self, pt):
        return [int(round((pt[f"x{i}"] - self.lo) / self.step)) for i in range(2)]

    def clamp_index(self, ix):
        return [min(LEVELS - 1, max(0, int(v))) for v in ix]

    def nominal_index(self):
        return [0, 0]


def _f(point):
    x, y = point["x0"], point["x1"]
    return (x - 1.5) ** 2 + (y + 2.5) ** 2      # optimum at index (23, 15)


def _drive(gen, domain, f):
    """evaluate every point the generator yields; the batch items are the
    `domain.from_index()` results (the ask/tell driver hands index lists to the
    module and gets points back)."""
    nfev, best, best_cost = 0, None, float("inf")
    batch = next(gen)
    while True:
        costs = []
        for pt in batch:
            c = f(pt)
            costs.append(c)
            nfev += 1
            if c < best_cost:
                best, best_cost = pt, c
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            return (stop.value or domain.to_index(best)), best_cost, nfev


if __name__ == "__main__":
    dom = _Domain2D()
    start = [3, 37]
    best, cost, nfev = _drive(run(dom, _f, start=start), dom, _f)
    print("OnePoint self-test: 2-D quadratic, 41 levels/dim, start", start)
    print("  batches : 2 x 1 point  (driver start -> Start::run 0x363f82 -> "
          "PointWait 0x3640b2 -> Return)")
    print("  point   :", dom.from_index(best), "index", best)
    print("  cost    :", cost)
    print("  nfev    :", nfev)
