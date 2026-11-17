"""Manual — extracted from libalchemy.so  (algs/Manual.cpp, registered "Manual Optimization" @0x569efc).

state machine (VMA addresses):

    Manual::Init        @0x345b5c  -> Manual::Start        Data is *empty*: `Manual::Data::Data(Params)` @0x345f52
                                                           is a 15-byte no-op, it drops Params completely
    Manual::Start       @0x345c92  -> Manual::SubmitPoint  new SubmitPoint(AlgState(this))
    Manual::SubmitPoint @0x345d78  -> Manual::Wait         new Wait(AlgState(this))
    Manual::Wait        @0x345e5e  -> Manual::Wait         literally `return intrusive_ptr<State>(this)`  @0x345e81
    Manual::Done        @0x345e8c  -> throw ShortCircuits::Return  @0x345ebb   ** unreachable **

Consequences (all disassembly-proven):
  * `Manual::Wait::run` contains exactly one path: build `intrusive_ptr<State>(this)` and return it.
    No predicate, no factory call, no next state -> the state machine is an eternal self loop.
  * Nothing in the whole DSO constructs `Manual::Done` (no reference to `_ZN7Alchemy6Manual4DoneC1*`
    outside its own vtable), so `Done`/`Return` can never be reached from inside libalchemy.so.
  * `SubmitPoint::run` @0x345d78 does *not* create a DesignPoint either: it only constructs `Wait`.
    The `DesignPointFactory` is never touched in the Manual code range 0x345ae7-0x346a05.

Who injects points then?  Somebody outside these libraries: the host application (ADE / SKILL /
the Cadence GUI "Manual Optimization" panel) pushes user points straight into the shared
`DesignPointFactory` queue that `FunctionOptimizer::run` drains.  In this extraction that
out-of-band channel is modelled by `params["inject"]`, a zero-argument callable returning a
batch of index lists (or point dicts).

Why the real run stops after ONE evaluation (measured: nfev=1, returned = the start vector)
--------------------------------------------------------------------------------------------
The termination is *driver side*, not algorithm side.  `FunctionOptimizer::run` 0x3ce70a
enqueues the reference point first and only then enters its drain loop:

    0x3ce89e  dp = fac->create();                       // queue += the refPoint
    0x3ce8c4..0x3ce91b  dp->values[i] = refPoint[i]     // +0xd8
    0x3ce958  params["current_point"] = dp
    0x3ce9cb  state = Factory::create(name, fac, params)
    0x3cea1b  state = state->run();                     // first step: Manual::Init -> Start
    0x3cea54  while (!fac->isPointQueueEmpty()) {       // <-- loop condition
    0x3cea95      dp = fac->popFrontFromPointQueue();
    0x3cead3      c  = cost_fn(dp->values, fac->refVec);
    0x3ceaff      dp->setCost(c);
    0x3ceb61      state = state->run();                 // Manual::Wait -> `return this`
    0x3ceb94  }                                         // 0x3cea70 je 0x3ceb99 -> loop exit
    0x3ceb99  (epilogue, no ShortCircuits::Done was thrown)

Manual never adds a point (no `DesignPointFactory::create` call anywhere in
0x345ae7..0x346a05), so after the single queued refPoint has been evaluated at 0x3cead3 the
queue is empty, the condition at 0x3cea54 fails and `run()` returns `out` = that point.
`Manual::Done` 0x345e8c (the only `Return` throw, 0x345ebb) is unreachable, so the state
machine itself never terminates -- the eternal `Manual::Wait::run` self loop
(0x345e5e..0x345e81, `return intrusive_ptr<State>(this)`) is simply never stepped again.

Consequence for this module: the *model* of the observable behaviour is
"one evaluation (the refPoint), then the generator ends", i.e. `return` where the C++ waits
forever.  That is a modelling choice at the driver boundary, not a reachable C++ `Return`.

generator protocol: `batch = yield points`; the driver sends `list[float]`; `run` returns the
best evaluated index list when the (modelled) point queue drains.
"""

from typing import List

STATE_SIZE = 0x40          # `new Manual::Start/SubmitPoint/Wait/Done`  @0x345bb9 / 0x345cbf / 0x345da5 / -
DATA_SIZE = 0x01           # `Manual::Data` occupies 1 byte at [rbp-0x21] in Init::run @0x345ba6
WAIT_LOOP = 0x345e81       # `return intrusive_ptr<State>(this)` inside Manual::Wait::run
DONE_THROW = 0x345ebb      # unreachable `__cxa_throw(typeinfo ShortCircuits::Return)`


def _pget(params, key, default=None):
    if params is None:
        return default
    if isinstance(params, dict):
        return params.get(key, default)
    get = getattr(params, "get", None)
    if get is not None:
        return get(key, default)
    return default


def _as_index(domain, value) -> List[int]:
    if value is None:
        return list(domain.nominal_index()) if hasattr(domain, "nominal_index") \
            else [t // 2 for t in domain.tunables]
    if isinstance(value, dict):
        return list(domain.to_index(value))
    return [int(v) for v in value]


def run(domain, goal, start=None, params=None, sub=None):
    """orchestrates sub-algorithms; yields their points through; returns best index list.

    Manual owns no search: `Manual::Data::Data(Params)` @0x345f52 is a 15-byte no-op and the
    state machine (`Init` 0x345b5c -> `Start` 0x345c92 -> `SubmitPoint` 0x345d78 -> `Wait`
    0x345e5e) never touches the `DesignPointFactory`.  The only evaluation a bare run performs
    is the driver's own reference point, which is why the real library reports nfev=1.
    """
    cur = _as_index(domain, start if start is not None else _pget(params, "current_point"))

    # ---- driver call #0: the refPoint `FunctionOptimizer::run` enqueued itself --------
    # 0x3ce89e create(), 0x3ce8c4..0x3ce91b values = refPoint, 0x3ce958 params["current_point"],
    # 0x3cea95 pop / 0x3cead3 cost_fn / 0x3ceaff setCost
    costs = yield [list(cur)]
    best_ix = list(cur)
    best_cost = float(costs[0]) if costs and costs[0] is not None else float("inf")

    # ---- out-of-band submission (host side, NOT part of libalchemy.so) ----------------
    # Without an injector nothing is ever enqueued, so the driver's loop condition
    # `!fac->isPointQueueEmpty()` (0x3cea54) is already false -> run() returns at 0x3ceb99.
    inject = _pget(params, "inject")
    if inject is None:
        return best_ix

    while True:
        raw = inject() or []
        pts = [_as_index(domain, p) for p in raw]
        if not pts:
            # empty submission == nothing in the queue -> driver loop exits (0x3cea54/0x3cea70)
            return best_ix
        costs = yield [list(p) for p in pts]
        for ix, c in zip(pts, costs or []):
            if c is not None and float(c) < best_cost:
                best_ix, best_cost = list(ix), float(c)
        # `Manual::Wait::run` keeps no state at all (0x345e5e..0x345e81), so the costs are
        # only used for the driver's `out` bookkeeping (0x3ceb04..0x3ceb3d).


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
    return (x - 1.5) ** 2 + (y + 2.5) ** 2        # optimum at index (23, 15)


if __name__ == "__main__":
    dom = _Domain2D()

    def _drive(gen, injector=None):
        """mini driver = FunctionOptimizer::run 0x3cea54..0x3ceb94 (queue drain + best)."""
        nfev, best, best_cost = 0, None, float("inf")
        try:
            batch = next(gen)
            while True:
                costs = []
                for ix in batch:
                    c = _f(dom.from_index(ix))
                    costs.append(c)
                    nfev += 1
                    if c < best_cost:
                        best, best_cost = list(ix), c
                batch = gen.send(costs)
        except StopIteration as stop:
            return (stop.value or best), best_cost, nfev

    # ---- (a) bare run: no out-of-band submission -> exactly ONE evaluation --------------
    best, cost, nfev = _drive(run(dom, _f, start=[3, 37]))
    print("Manual self-test (a): no injector -- the harness case")
    print(f"  point={best} cost={cost} nfev={nfev}")
    print("  native ./harness Manual 2 21 200 0 10 7 3 -> nfev=1, returned=[7,3]")

    # ---- (b) GUI-style submission: 3 user points, then an empty submission --------------
    submitted = [[3, 37], [23, 15], [30, 30]]
    calls = {"n": 0}

    def inject():
        """stands in for the out-of-band (ADE/GUI) point submission."""
        i = calls["n"]
        calls["n"] += 1
        return [submitted[i]] if i < len(submitted) else []

    best, cost, nfev = _drive(run(dom, _f, params={"inject": inject}))
    print("Manual self-test (b): injector submits 3 points, then stops")
    print(f"  point={best} cost={cost} nfev={nfev} injector_calls={calls['n']}")
    print("  stop = driver loop condition 0x3cea54 (queue drained), NOT Manual::Done 0x345e8c")
    print("  Wait::run 0x345e5e..0x345e81 is `return intrusive_ptr<State>(this)`; Done is unreachable")
