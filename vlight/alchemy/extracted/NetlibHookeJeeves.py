"""NetlibHookeJeeves — extracted from libalchemy.so (algs/NetlibHookeJeeves.cpp).

states:
    Init -> Start -> {Start_WaitForStartingPoint, Start_HaveStartingPoint}
    Start_HaveStartingPoint -> Move                       @0x3471fe
    Move  -> {Done, Wait, Move}                           @0x34759c / @0x347646
    Wait  -> {Wait, Move}                                 @0x347868 / @0x347977
    Done  -> Return (throw)                               @0x347a56

hyperparams (Data::Data(Params) @0x347c0e):
    current_point  shared_ptr<DesignPoint>  default: empty DesignPoint  0x347cd2
    population     shared_ptr<Population>   default: Population(1)      0x347ed3

===========================================================================
THE INNER ROUTINE IS A STUB IN THIS BUILD — that *is* the algorithm here
===========================================================================

`hooke` is the free function the algorithm calls, and in libalchemy.so it is a
65-byte stub that ignores both arguments, zero-fills its sret vector<double> and
returns:

    $ objdump -dC -M intel --no-show-raw-insn \
        --start-address=0x32f4b1 --stop-address=0x32f4f1 libalchemy.so
    32f4b1 <hooke(std::vector<double> const&,
                  boost::function1<double, std::vector<double>> const&)>:
      32f4b9: mov QWORD PTR [rbp-0x8],rdi        ; sret slot
      32f4bd: mov QWORD PTR [rbp-0x10],rsi       ; x      (never read)
      32f4c1: mov QWORD PTR [rbp-0x18],rdx       ; f      (never called)
      32f4c5: mov rax,QWORD PTR [rbp-0x8]
      32f4c9: mov QWORD PTR [rax],0x0            ; _M_start  = nullptr
      32f4d0: mov QWORD PTR [rax+0x8],0x0        ; _M_finish = nullptr
      32f4d8: mov QWORD PTR [rax+0x10],0x0       ; _M_end_of_storage = nullptr
      32f4e7: call std::vector<double>::vector() ; empty vector
      32f4f1: ret

No call through the boost::function1 in rdx exists in the body, so the objective
is never evaluated through `hooke`; the classic Netlib ±step / halving search is
absent from this binary.  `Move::run` @0x347552 calls the stub through the PLT
(0x2cf600) and, on a normal return (the `NotEvaluated` landing pad is 0x347749),
falls straight through to `Done` @0x34759c -> `ShortCircuits::Return`
@0x347a56.

Observable behaviour, measured on native/harness (dim 2/3/4, levels 21, target
10, start 0 and start 10, problems quad/rosen/valley):

    nfev = 1   ->  exactly the one cost-function call FunctionOptimizer::run
                   makes for the *driver's* start point (docs/spec_core.md §6),
                   returned = that start point, status "ok".

So the module below reproduces the library as shipped: one driver evaluation of
the start, then Return.  The memoised-replay machinery that surrounds the call
(evaluateAt @0x346a67 floors to an integer cell and throws NotEvaluated
@0x346b4c; Move catches it, submit() @0x346bd0 creates the design point, Wait
stores the cost @0x34795c and re-enters Move) is kept below because it is part
of Move, but with the stub `hooke` it is unreachable: nothing ever calls
`evaluate_at`, so no `_NotEvaluated` is ever raised.

This is a *reproduction* of the shipped behaviour, not a substitution: the
classical Netlib hooke body that earlier revisions of this file implemented is
not what libalchemy.so does.  Whether the vendor shipped the stub on purpose or
lost the object at link time is unproven; the observable contract is the stub.
"""

import math

HALF = 0.5                    # 0x56a968 (x = design + 0.5, Move 0x34748f)
BIG = 1.7976931348623157e308  # 0x56a960 (initial best inside the missing hooke body)

K_CURRENT_POINT = "current_point"  # 0x56a3e9
K_POPULATION = "population"        # 0x56a3f7

# Knobs of the *classical* routine; they have no consumer in this build because
# `hooke` never looks at its arguments.  Kept as documented defaults so a caller
# can still see what the classic body would have used.
DEFAULTS = {
    "rho": 0.5,         # [unproven] argument of the missing hooke body
    "eps": 1e-6,        # [unproven]
    "max_iter": 10000,  # [unproven]
}

# assertion text 0x56a3c0: "p.size() == pointFactory.tunables.size()"


class _NotEvaluated(Exception):
    def __init__(self, key):
        Exception.__init__(self, key)
        self.key = key


def _hooke(x, f):
    """The library's `hooke()` as linked into libalchemy.so — a stub @0x32f4b1.

    It zero-fills the sret vector and returns an empty vector<double>; the
    point `x` is not read and the callback `f` is not invoked
    (0x32f4b9..0x32f4f1).  Keeping the signature lets Move's call site stay
    faithful.
    """
    return []


def _hooke_classic(x, f, rho, eps, max_iter):
    """[unreachable in this build] the classical Netlib body, for reference only.

    ±step exploratory moves per coordinate, multiply the whole step vector by
    rho after a sweep without improvement, stop when max(step) < eps or
    max_iter is reached.  libalchemy.so contains none of this: 0x32f4b1 is the
    whole routine.
    """
    n = len(x)
    step = [1.0] * n
    fx = f(x)
    for _ in range(max_iter):
        improved = False
        for i in range(n):
            for sgn in (1.0, -1.0):
                xt = list(x)
                xt[i] += sgn * step[i]
                ft = f(xt)
                if ft < fx:
                    x, fx = xt, ft
                    improved = True
                    break
            if improved:
                break
        if not improved:
            step = [s * rho for s in step]
            if max(step) < eps:
                return x
    return x


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


_PARAM_KEYS = (K_CURRENT_POINT, K_POPULATION)


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
    """yield a batch of points; receive costs via send(); return best index list.

    Two native evaluations would only happen if `hooke` asked for them; the stub
    never does, so the whole run is exactly one driver evaluation.
    """
    params = _as_params(params, _PARAM_KEYS)

    tun = [int(t) for t in domain.tunables]

    start_ix = list(domain.clamp_index(
        _resolve_start(domain, start, params.get(K_CURRENT_POINT))))

    cache = {}

    def evaluate(batch_ix):
        todo = [ix for ix in batch_ix if tuple(ix) not in cache]
        if todo:
            costs = yield [domain.from_index(ix) for ix in todo]
            if len(costs) != len(todo):
                raise ValueError("expected %d costs, got %d" % (len(todo), len(costs)))
            for ix, c in zip(todo, costs):
                cache[tuple(ix)] = float(c)
        return [cache[tuple(ix)] for ix in batch_ix]

    def evaluate_at(xv):
        """0x346a67: floor every coordinate to an integer cell, look it up in the
        cost map; a miss throws NotEvaluated(key).  Unreachable with the stub."""
        key = tuple(int(math.floor(v)) for v in xv)          # 0x346ade floor
        if key not in cache:                                  # 0x346b39
            raise _NotEvaluated(key)                          # 0x346b6f
        return cache[key]

    # ---- Start_WaitForStartingPoint 0x3470dc ------------------------------
    # the driver has already evaluated the start (FunctionOptimizer::run); the
    # Start state only waits for that point to carry a cost.
    (c0,) = yield from evaluate([start_ix])
    x = [float(v) + HALF for v in start_ix]                   # 0x34748f

    # ---- Move::run 0x3471fe ------------------------------------------------
    # `hooke()` @0x347552 returns an empty vector without touching its
    # callback, so no NotEvaluated is raised, `evaluate_at` above is never
    # called, and control falls through to Done @0x34759c.
    sol = _hooke(x, evaluate_at)                              # stub -> []

    if not sol:
        # Done::run -> ShortCircuits::Return (0x347a56); the engine returns the
        # best evaluated point, which for us is the driver's starting point.
        return list(start_ix)

    # ---- unreachable from the shipped binary (kept for the replay shape) ----
    best = [min(max(int(math.floor(v)), 0), tun[i] - 1)
            for i, v in enumerate(sol)]
    return best


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class Quad:
        tunables = [41, 41]

        def from_index(self, ix):
            return {"x": -2.0 + 0.1 * int(ix[0]), "y": -2.0 + 0.1 * int(ix[1])}

        def to_index(self, point):
            return [self.clamp(int(round((point["x"] + 2.0) * 10))),
                    self.clamp(int(round((point["y"] + 2.0) * 10)))]

        def center_index(self):
            return [20, 20]

        def clamp(self, i):
            return max(0, min(40, int(i)))

        def clamp_index(self, ix):
            return [self.clamp(i) for i in ix]

    dom = Quad()
    nfev = [0]
    seen = {}

    def cost(p):
        nfev[0] += 1
        ix = tuple(dom.to_index(p))
        seen[ix] = p["x"] ** 2 + p["y"] ** 2
        return seen[ix]

    gen = run(dom, None, start=[3, 37])
    npts = 0
    batch = next(gen)
    while True:
        npts += len(batch)
        try:
            batch = gen.send([cost(p) for p in batch])
        except StopIteration as stop:
            best = stop.value
            break
    print("NetlibHookeJeeves self-test: 2-D quadratic, 41 levels/dim, start [3, 37]")
    print("  hooke()   : stub @0x32f4b1 -> empty vector, Move -> Done -> Return")
    print("  points    :", npts, "(the driver's start evaluation only)")
    print("  returned  :", best)
    print("  nfev      :", nfev[0])
