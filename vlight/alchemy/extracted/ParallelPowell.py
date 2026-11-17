"""ParallelPowell — extracted from libalchemy.so (algs/ParallelPowell.cpp).

Registered as "Parallel Powell Loop" (.rodata 0x56f5a0, registration object
0x37f8d8); code range 0x37d1cb - 0x37f99a.

states:
    Init -> Start -> {Start_WaitForStartingPoint, Start_HaveStartingPoint}
    Start_HaveStartingPoint -> LoopBegin                        @0x37d6d2
    LoopBegin -> Loop                                           @0x37d7e2
    Loop -> Fork<AlgState, StartBrent>(min(node_count, n-dim))  @0x37da78 / @0x37db4f
    StartBrent -> Call<..., LineMinimize::Init>                 @0x37dc16
    BrentDone -> LineMinimizeDone                               @0x37e0a8
    LineMinimizeDone -> {Loop, LoopDone}                        @0x37e276
    LoopDone -> {LoopBegin, Return}                             @0x37e482

hyperparams (Data::Data(Params) @0x37ec68):
    node_count     shared_ptr<unsigned>    default 2                 0x37ed4b
    current_point  shared_ptr<DesignPoint> default empty DesignPoint 0x37ef32
    population     shared_ptr<Population>  default Population(1)     0x37f136
    (StartBrent injects exactly two keys into the nested LineMinimize:
     `population` @0x56f601 and `vector` @0x56f6e2 — nothing else, so the
     line search keeps the framework defaults: minimum_steps_for_brent = 30,
     directional = false, no current_point.)

Recovered control flow (every rule below was read out of the binaries that
this file's selftest diffs against; the addresses are VMA in libalchemy.so):

 1. Start_HaveStartingPoint 0x37d6d2: `globalBest->insertAndDiscardExtras(c0)`
    (0x37d708).  c0 (AlgState+0xc0) is the starting DesignPoint; the driver has
    already evaluated it (FunctionOptimizer::run calls cost_fn for `start`
    before the algorithm's first point, docs/spec_core.md §6), so the module
    emits exactly one point for it and no extra Start evaluation exists.

 2. LoopBegin 0x37d7e2: if `directions` is empty -> n unit vectors (1.0 @
    0x56ffa0) and one `Population(1)` per direction (`best_points`, 0x37d8dd /
    0x37d907).  The cost map (AlgState+0x50) is cleared here (0x37d943).

 3. Loop::run 0x37da78: `state->48 = globalBest->top()->cost()` (0x37dabc),
    `parallel_dims = min(*node_count, directions.size() - dim)` (0x37dafc /
    0x37db0a), assert(parallel_dims != 0) "parallel_dims>0" (0x37db32), then
    `Fork<AlgState, StartBrent>(parallel_dims)` (0x37db4f).

 4. StartBrent 0x37dc16, child k = dim + FORKIDX (0x37dc71):
    `best_points[k]->insertAndDiscardExtras(globalBest->top())` (0x37dd09),
    Params["population"] = best_points[k] (0x37dd86), Params["vector"] =
    directions[k] (0x37ddf5) -> nested LineMinimize.  The line search is the only
    writer of that worker population (Start's seed, Min_Exhaustive_Wait 0x33539f,
    Min_Brent_WaitPoint 0x336258 -- the last one after the fail-count test), so
    the worker result is `best_points[k]->top()` and the driver inserts nothing.

    Is that population per-fork?  Yes: `StartBrent` constructs its own
    `AlgState` copy (`AlgState::AlgState(intrusive_ptr<AlgState>)` @0x37f6c4,
    called from `StartBrent::StartBrent` @0x37fb10) and the copy's
    `best_points` vector holds the *same* shared_ptrs, so child k writes
    `best_points[dim+FORKIDX]` -- one `Population(1)` per direction, persistent
    across cycles -- while `globalBest` (AlgState+0xd0) stays shared.  Measured
    with `variants/sec16/pop_insert_log.gdb` on
    `ParallelPowell 3 101 1000 valley`: four distinct `Population` objects, all
    `maxsize_ == 1` (globalBest + three workers).  PARITY.md §8.3 draws the same
    line for `cost_map` (per-fork copy of the map, §16.4 for this round).

 5. Fork scheduling: the fork is entered once per batch and every child is
    advanced one `run()` per sweep, so the point queue is FIFO over sweeps.
    With min_steps = 30 the line search is Min_Exhaustive for every line of a
    21-level lattice (<= 21 < 30 points) and each worker contributes its whole
    line contiguously — that is what `native/harness ParallelPowell 2 21 ...`
    prints (ev1..ev21 = line of direction 0, ev22..ev42 = line of direction 1).
    With a longer line (levels 101) Min_Brent yields one point per run() and
    the two workers alternate (ev1 (39,0), ev2 (0,39), ev3 (63,0), ...); the
    sweep scheduler below reproduces both.

 6. BrentDone 0x37e0a8 (per child, in fork order):
    `globalBest->insertAndDiscardExtras(best_points[k]->top())` (0x37e10b),
    `delta = state->48 - best_points[k]->top()->cost()` (0x37e16a) and
    `cost_map[delta] = k` (0x37e19f, std::map::operator[] -> equal keys keep
    the later direction).

 7. LineMinimizeDone 0x37e276: JOIN, `dim += parallel_dims` (0x37e28e), then
    `dim < factory->tunables.size()` -> Loop (0x37e2cf) else LoopDone.

 8. LoopDone 0x37e482 (this is the part the previous port got wrong):
      * 0x37e4cd  `globalBest->top() == state->c0` **by shared_ptr identity**
        -> throw ShortCircuits::Return.  (Not a value comparison.)
      * 0x37e54e  assert(top()->values != c0->values)            line 108
      * 0x37e582  assert(!cost_map.empty())                      line 109
      * 0x37e5c9  assert(c0->cost() > 0.0)                       line 110
      * 0x37e64a  `cost_map.begin()->first / c0->cost() > 0.999`
        (StagnantCostImprovement @0x56ffa8) -> `directions.clear()` (0x37e66b)
        and LoopBegin; c0 is **not** refreshed on this path.
      * else 0x37e702: `v = top()->values - c0->values` (0x37e757),
        `k = cost_map.rbegin()->second` (0x37e782), shift
        `directions[k] = directions[k-1] ... directions[1] = directions[0]`
        (0x37e822..0x37e88d), `directions[0] = (double)v[i]` **unnormalised**
        (0x37e8c6 / 0x37e904), `c0 = top()` (0x37e942), -> LoopBegin.

 9. The cost map is *per fork child*: Fork<AlgState, StartBrent> hands every
    child a copy of the AlgState, so child k starts from the parent map and
    adds one entry, and the state that survives the join (the last child) is
    the one LoopDone reads.  Consequence, verified in the binary: after a
    two-child batch the map holds only the last child's delta, and after a
    cycle made of several batches it holds one entry per batch (each batch's
    last child).  This is exactly why the native run for
    `dim=2 levels=21 start=(0,0) target=10` keeps going for four cycles:

        cycle 1  dirs [(1,0),(0,1)]  from (0,0)   -> lines y=0, x=0
                 map {(100,1)}        min/c0 = 0.5    -> v=(0,10)
        cycle 2  dirs [(0,10),(1,0)] from (0,10)  -> lines x=0, y=10
                 map {(100,1)}        min/c0 = 1.0 > 0.999 -> *stagnant*:
                 unit directions are rebuilt, c0 stays (0,10)
        cycle 3  dirs [(1,0),(0,1)]  from (10,10) -> lines y=10, x=10
                 map {(0,1)}          min/c0 = 0.0    -> v=(10,0)
        cycle 4  dirs [(10,0),(1,0)] from (10,10) -> lines y=10, y=10 (!)
                 top() == c0 by identity                  -> Return

    nfev = 1 (driver start) + 8 lines x 21 points = 169.

Line search: delegated to the canonical LineMinimize extraction
(algorithms/LineMinimize.py, already 100% parity through Powell.py), driven
with the same Params Powell's Loop::run builds plus the per-worker population:
    population  = best_points[k]     (0x37dd86)
    vector      = directions[k]      (0x37ddf5)
    minimum_steps_for_brent, directional omitted -> framework defaults 30/false
"""

# ---------------------------------------------------------------------------
#  canonical line search: algorithms/LineMinimize.py, loaded by path
# ---------------------------------------------------------------------------
import importlib.util
import os


def _load_line_minimize():
    """Load the canonical `Alchemy::LineMinimize` extraction that sits next to
    this file (path-relative, stdlib only -> `python3 ParallelPowell.py` works)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LineMinimize.py")
    spec = importlib.util.spec_from_file_location(
        "AlchemyLineMinimize_pp_%s_%d" % (__name__, id(object())), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LineMinimize = _load_line_minimize()


class _NoCachePointFactory(LineMinimize._Factory):
    """`DesignPointFactoryImpl::create` always allocates a fresh, unevaluated
    point and enqueues it (spec_core §8.2 @0x3cef6e / 0x3cf016).  The line
    search really re-evaluates its base point — every native block below starts
    with the incumbent — so the extraction must not serve it from a cache.
    `run()` resolves `_Factory` from the module globals."""

    def create(self, ix):
        return LineMinimize._Pt(tuple(int(v) for v in ix), None, False)


LineMinimize._Factory = _NoCachePointFactory

# ---------------------------------------------------------------------------
#  constants (VMA inside libalchemy.so)
# ---------------------------------------------------------------------------
STAGNANT = 0.999                    # ParallelPowell::StagnantCostImprovement 0x56f5e0,
                                    # second copy read by LoopDone 0x56ffa8
UNIT = 1.0                          # 0x56ffa0

K_NODE_COUNT = "node_count"         # 0x56f5e8
K_CURRENT_POINT = "current_point"   # 0x56f5f3
K_POPULATION = "population"         # 0x56f601

DEFAULTS = {K_NODE_COUNT: 2}        # 0x37ed4b `movl $0x2`


# ---------------------------------------------------------------------------
#  DesignPoint::operator< @0x3d1dda -- the population ordering
#  (identical to Powell.py / LineMinimize.py; verified against the real symbol
#   with native/probe_cmp.cpp: both hashes rotate with r taken from the LEFT
#   operand's values, so (70,30) and (30,70) compare as equal)
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


class _P:
    """A DesignPoint object.  Identity matters: LoopDone's termination test is
    a shared_ptr comparison (@0x37e4cd), and std::set::insert rejects an
    element that is equivalent to the one already stored (so re-probing the
    incumbent keeps the *old* object)."""

    __slots__ = ("ix", "cost")

    def __init__(self, ix, cost):
        self.ix = tuple(int(v) for v in ix)
        self.cost = float(cost)

    def __repr__(self):
        return "_P(%s, %g)" % (list(self.ix), self.cost)


class _Pop1:
    """`Population<DesignPoint>` with maxsize_ == 1 (0x37d8dd / 0x37f136):
    insertAndDiscardExtras() keeps the minimum, rejects equivalent elements.

    This object *is* the `population` hyper-parameter of the nested line search
    (StartBrent 0x37dd86), so it has to answer the LineMinimize protocol:
    `top()`, `insert(dp)` and `set_error(flag)`.  Both `_P` (the driver's
    DesignPoints) and `LineMinimize._Pt` (the line search's) carry `.ix`/`.cost`,
    and the ordering below reads nothing else -- mixing the two is harmless and is
    exactly what the library does (one `std::set<shared_ptr<DesignPoint>>`).
    """

    __slots__ = ("top_pt",)

    def __init__(self):
        self.top_pt = None

    def top(self):
        assert self.top_pt is not None, "!set_.empty()"
        return self.top_pt

    def insert(self, p):
        cur = self.top_pt
        if cur is None:
            self.top_pt = p
            return
        if not _dp_less(p.ix, p.cost, cur.ix, cur.cost) and \
           not _dp_less(cur.ix, cur.cost, p.ix, p.cost):
            return                                   # equivalent -> insert rejected
        if _dp_less(p.ix, p.cost, cur.ix, cur.cost):
            self.top_pt = p                          # new minimum
        # else: inserted, then dropped again as the discarded extra

    def set_error(self, flag):
        """`Population::setError`; unreachable in the single-cost model (a probe's
        `hasErrorCost` is never set), kept so the protocol is complete."""
        pass


def _unit(n, i):
    e = [0.0] * n
    e[i] = UNIT
    return e


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


_PARAM_KEYS = (K_NODE_COUNT, K_CURRENT_POINT, K_POPULATION)


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


# ---------------------------------------------------------------------------
#  one forked worker: StartBrent @0x37dc16
# ---------------------------------------------------------------------------
class _Worker:
    """StartBrent for one direction: seed best_points[k] with the global best,
    then run the nested LineMinimize(vector = directions[k],
    population = best_points[k]) and keep the worker population's top."""

    def __init__(self, idx, pop, dvec, domain, goal):
        self.idx = idx
        self.pop = pop
        self.dvec = dvec
        self.domain = domain
        self.goal = goal
        self.result = None
        self.gen = self._line()

    def _line(self):
        base = self.pop.top()
        # StartBrent injects exactly two keys into the nested LineMinimize:
        #   Params["population"] = best_points[dim+FORKIDX]   (0x37dd86, key 0x56f601)
        #   Params["vector"]     = directions[dim+FORKIDX]    (0x37ddf5, key 0x56f6e2)
        # -- see 0x37dc16..0x37de60.  Nothing else is set, so the line search keeps
        # the framework defaults (minimum_steps_for_brent 30, directional false,
        # fail_count_limit 0: the private `shared_ptr<unsigned>(0)` built in
        # LineMinimize::Data::Data at 0x337422).
        #
        # The worker population is therefore written by the line search alone:
        # Start seeds it, Min_Exhaustive_Wait inserts every scanned point (0x33539f)
        # and Min_Brent_WaitPoint inserts its probe at 0x336258 -- *after* the
        # fail-count test at 0x3361d2 that throws `ShortCircuits::Return` at
        # 0x33622a.  A probe killed by that test is evaluated and never stored, so
        # the worker must not "insert everything it evaluated" (PARITY.md §14.4/§16).
        lm = LineMinimize.run(self.domain, self.goal, start=list(base.ix),
                              params={"vector": [float(x) for x in self.dvec],
                                      "directional": False,
                                      "fail_count_limit": 0,
                                      "start_cost": base.cost,
                                      "population": self.pop})
        try:
            batch = next(lm)
        except StopIteration:
            return self.pop.top()
        while True:
            got = yield batch
            try:
                batch = lm.send([float(c) for c in got])
            except StopIteration:
                break
        return self.pop.top()

    def next_batch(self):
        try:
            return next(self.gen)
        except StopIteration as stop:
            self.result = stop.value
            return None

    def send(self, costs):
        try:
            return self.gen.send(costs)
        except StopIteration as stop:
            self.result = stop.value
            return None


def _run_fork(workers, domain, on_done):
    """Fork<AlgState, StartBrent>.

    Every child is started once (StartBrent @0x37dc16) and enqueues its first
    batch; the queue is then drained in creation order, and only the child that
    owns the batch being evaluated is advanced (`Fork::run` runs one child per
    step).  Consequences, both observed on the native binary:

      * Min_Exhaustive lines (<= 21 points here, min_steps = 30) are enqueued in
        one go, so each worker's whole line comes out contiguously
        (`harness ParallelPowell 2 21 ...`: ev1..ev21 = direction 0, ev22..ev42
        = direction 1);
      * Min_Brent lines enqueue one point per step, so the workers alternate
        (`harness ParallelPowell 2 101 ...`: ev1 (39,0), ev2 (0,39), ev3 (63,0)).

    `on_done(w)` is BrentDone @0x37e0a8 and fires the moment a child's line
    search returns — i.e. in *completion* order, not fork order, which is what
    decides which child's copy of the cost map survives the join.
    """
    from collections import deque

    queue = deque()
    for w in workers:
        b = w.next_batch()
        if b is not None:
            queue.append((w, b))
        else:                       # degenerate line (e.g. zero vector): BrentDone at once
            on_done(w)
    while queue:
        w, batch = queue.popleft()
        got = yield [domain.from_index(ix) for ix in batch]
        nb = w.send(got)
        if nb is not None:
            queue.append((w, nb))
        else:
            on_done(w)
    return [w.result for w in workers]


# ---------------------------------------------------------------------------
def run(domain, goal, start=None, params=None):
    """yield a batch of points; receive costs via send(); return best index list"""
    params = _as_params(params, _PARAM_KEYS)
    node_count = int(params.get(K_NODE_COUNT, DEFAULTS[K_NODE_COUNT]))

    tun = [int(t) for t in domain.tunables]
    n = len(tun)

    start_ix = list(domain.clamp_index(
        _resolve_start(domain, start, params.get(K_CURRENT_POINT))))

    # ---- driver call #0 (FunctionOptimizer::run) + Start_HaveStartingPoint --
    # `state->c0` is the starting DesignPoint; Start_HaveStartingPoint inserts it
    # into globalBest with insertAndDiscardExtras (0x37d708).
    (c0_cost,) = yield [domain.from_index(start_ix)]
    c0 = _P(start_ix, float(c0_cost))
    gbest = _Pop1()
    gbest.insert(c0)

    dirs = []
    best_points = []

    cycles = 0
    while True:
        cycles += 1
        if cycles > 500:                 # Python-side safety cap (not in the binary)
            return list(gbest.top().ix)

        # ---- LoopBegin::run @0x37d7e2 -------------------------------------
        if not dirs:
            dirs = [_unit(n, i) for i in range(n)]          # 1.0 @0x56ffa0
            best_points = [_Pop1() for _ in range(n)]       # Population(1) 0x37d8dd
        fmap = {}                                           # 0x37d943 map.clear()
        dim = 0

        # ---- Loop::run @0x37da78 (one Fork per batch) ----------------------
        while True:
            batch_cost = gbest.top().cost                    # 0x37da9e / 0x37dabc
            parallel_dims = min(node_count, len(dirs) - dim)  # 0x37dafc / 0x37db0a
            if parallel_dims <= 0:                            # 0x37db14
                raise AssertionError("parallel_dims>0")       # 0x37db32

            workers = []
            for k in range(parallel_dims):
                idx = dim + k                                 # dim + FORKIDX 0x37dc71
                pop = best_points[idx]                        # 0x37dc7b
                pop.insert(gbest.top())                       # 0x37dd09
                workers.append(_Worker(idx, pop, dirs[idx], domain, None))

            parent_map = fmap                  # every child clones the map as it
                                               # was *before* the fork ...
            finished = []

            def _brent_done(w):
                """BrentDone::run @0x37e0a8 for one worker, at the moment its
                nested LineMinimize returns (completion order, see _run_fork)."""
                res = w.result
                gbest.insert(res)                             # 0x37e10b
                delta = batch_cost - res.cost                 # 0x37e16a
                m = dict(parent_map)                          # ... plus its own entry
                m[delta] = w.idx                              # 0x37e19f
                finished.append((w, m))

            yield from _run_fork(workers, domain, _brent_done)

            # the state that survives the join is the last child that finished
            fmap = finished[-1][1]

            dim += parallel_dims                              # 0x37e28e
            if dim >= n:                                      # 0x37e2cf
                break

        # ---- LoopDone::run @0x37e482 --------------------------------------
        top = gbest.top()
        if top is c0:                                         # 0x37e4cd identity
            return list(top.ix)

        assert top.ix != c0.ix, "top()->values() != state->c0->values()"   # line 108
        assert fmap, "!cost_map.empty()"                                   # line 109
        assert c0.cost > 0.0, "state->c0->cost() > 0.0"                    # line 110

        if min(fmap) / c0.cost > STAGNANT:                    # 0x37e64a / 0x56ffa8
            dirs = []                                         # 0x37e66b
            continue                                          # c0 NOT refreshed

        v = [top.ix[i] - c0.ix[i] for i in range(n)]          # 0x37e757
        k = fmap[max(fmap)]                                   # 0x37e782 rbegin()
        assert k < len(dirs), "i < directions.size()"         # line 117
        while k != 0:                                         # 0x37e7da..0x37e88d
            dirs[k] = list(dirs[k - 1])
            k -= 1
        dirs[0] = [float(x) for x in v]                       # 0x37e8c6 unnormalised
        c0 = top                                              # 0x37e942
                                                              # -> LoopBegin


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 41x41 quadratic, identity lattice (point vector == level index, min at
    # (20,20)); the module yields `domain.from_index(ix)` so the driver just
    # evaluates what it gets back.
    class Quad:
        tunables = [41, 41]

        def from_index(self, ix):
            return [int(v) for v in ix]

        def to_index(self, point):
            return [self.clamp(int(v)) for v in point]

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
        nfev[0] += 1
        key = tuple(int(v) for v in ix)
        seen[key] = float(sum((v - 20) * (v - 20) for v in key))
        return seen[key]

    for nodes in (2, 4):
        nfev[0] = 0
        seen.clear()
        gen = run(dom, None, start=[0, 20], params={"node_count": nodes})
        batch = next(gen)
        while True:
            try:
                batch = gen.send([cost(ix) for ix in batch])
            except StopIteration as stop:
                best = stop.value
                break
        print("ParallelPowell(node_count=%d) final=%s cost=%.6e nfev=%d"
              % (nodes, best, seen[tuple(best)], nfev[0]))
