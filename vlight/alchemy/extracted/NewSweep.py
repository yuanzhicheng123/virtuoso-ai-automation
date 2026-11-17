"""NewSweep — extracted from libalchemy.so (algs/NewSweep.cpp).

states:
    Init -> Start -> Main                                   @0x34c424 / @0x34c5ba
    Main -> {Return, Submit, WaitAllThenSubmit}              @0x34e0e7 / @0x34e18f / @0x34e215
    Submit -> {WaitAnyOne, Return, WaitAllThenSubmit, Main}  @0x34d682
    WaitAnyOne -> {self, Main}                               @0x34cc62
    ForkedWaitAll / ForkedWaitOne / JoinThenSubmit -> Main   @0x34cf86 / @0x34d0fa / @0x34d38c
    WaitAllThenSubmit -> Main                                @0x34d48c
    Return -> Return (throw)                                 @0x34e34e

hyperparams (Data::Data(Params) @0x34e83e):
    values                          shared_ptr<vector<int>>  default empty vector   0x34e94e
    tunables_to_adjust_incrementally shared_ptr<set<ulong>>  default empty set      0x34eb5b
    tunables_to_adjust_batched       shared_ptr<set<ulong>>  default empty set      0x34ed80
    submit_entire_design_space       shared_ptr<bool>        default true           0x34efaf
    monte_carlo_mode                 shared_ptr<bool>        default false          0x34f19d
    limit_on_outstanding_points      shared_ptr<ulong>       default 100000 (basic run
                                     mode) / 10000 (otherwise)                      0x34f38a

The odometer (Main::run @0x34df58) is exact:
    i = 0
    while i < tunableMap.size():
        ++theValues[tunableMap[i]]
        if theValues[tunableMap[i]] != tunables[tunableMap[i]]: break   # 0x34e02b
        theValues[tunableMap[i]] = 0                                    # 0x34e080
        ++i
    if i == tunableMap.size(): terminate                                # 0x34e0a8
    if i < batchedTunableCount(): Submit(state) else WaitAllThenSubmit(state)
"""

K_VALUES = "values"                                    # 0x56ad7d
K_INCREMENTAL = "tunables_to_adjust_incrementally"     # 0x56ad88
K_BATCHED = "tunables_to_adjust_batched"               # 0x56ada9
K_SUBMIT_ALL = "submit_entire_design_space"            # 0x56adc4
K_MONTE_CARLO = "monte_carlo_mode"                     # 0x56addf
K_LIMIT = "limit_on_outstanding_points"                # 0x56adf0

DEFAULTS = {
    K_SUBMIT_ALL: True,
    K_MONTE_CARLO: False,
    K_LIMIT: 100000,
}


_PARAM_KEYS = (K_VALUES, K_INCREMENTAL, K_BATCHED, K_SUBMIT_ALL, K_MONTE_CARLO, K_LIMIT)

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

def _driver_start(domain, start):
    """Driver call #0: ``FunctionOptimizer::run`` evaluates ``start`` before the
    algorithm is constructed (0x3ce70a, spec_core.md §6).  NewSweep never reads the
    start -- its odometer starts from ``values`` (``K_VALUES``, default all-zero,
    0x34e94e) -- so this evaluation belongs to the driver.  Emitting it keeps the
    sequence point-for-point comparable with the real library.
    """
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


def run(domain, goal, start=None, params=None):
    """yield a batch of points; receive costs via send(); return best index list"""
    params = _as_params(params, _PARAM_KEYS)
    tun = [int(t) for t in domain.tunables]
    n = len(tun)

    values = params.get(K_VALUES)
    values = [int(v) for v in values] if values is not None else [0] * n
    if len(values) != n:
        values = ([0] * n)[:n]

    limit = int(params.get(K_LIMIT, DEFAULTS[K_LIMIT]))
    if limit <= 0:
        limit = 1

    # ---- Start::run @0x34c5ba: build the mixed-radix digit order ----------
    # The incremental set is a std::set (ascending); the remaining dimensions are
    # appended in reverse order.  The batched set (if any) is prepended and its
    # size becomes batchedTunableCount().   [order details: unproven]
    incremental = sorted({int(d) for d in (params.get(K_INCREMENTAL) or [])})
    batched = sorted({int(d) for d in (params.get(K_BATCHED) or [])})
    used = set(incremental) | set(batched)
    rest = [d for d in range(n - 1, -1, -1) if d not in used]
    tunable_map = batched + incremental + rest
    batched_count = len(batched)

    best_ix, best_cost = None, None
    pending = []

    def flush(points):
        nonlocal best_ix, best_cost
        costs = yield [domain.from_index(ix) for ix in points]
        for ix, c in zip(points, costs):
            if best_cost is None or c < best_cost:
                best_cost, best_ix = float(c), list(ix)

    # driver call #0: the start point (FunctionOptimizer::run, before Start::run)
    yield [domain.from_index(_driver_start(domain, start))]

    # Submit the initial design (the digest of Start -> Submit) --------------
    yield from flush([list(domain.clamp_index(values))])

    while True:
        # ---- Main::run @0x34df58 ------------------------------------------
        i = 0
        while i < len(tunable_map):
            t = tunable_map[i]
            values[t] += 1                                  # 0x34dfda
            if values[t] != tun[t]:                         # 0x34e02b
                break
            values[t] = 0                                   # 0x34e080
            i += 1
        if i == len(tunable_map):                           # 0x34e0a8
            if pending:
                yield from flush(pending)
            return list(best_ix)

        pending.append(list(domain.clamp_index(values)))
        # batched digits (i < batchedTunableCount) accumulate; incremental digits
        # flush one point at a time; limit_on_outstanding_points caps the batch.
        if i >= batched_count or len(pending) >= limit:
            batch, pending = pending, []
            yield from flush(batch)


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
    for label, pars in (("incremental", {}), ("batched+dims", {"tunables_to_adjust_batched": [0]}),
                        ("limit=64", {"limit_on_outstanding_points": 64})):
        nfev = [0]
        seen = {}

        def cost(p):
            nfev[0] += 1
            ix = tuple(dom.to_index(p))
            seen[ix] = p[0] * p[0] + p[1] * p[1]
            return seen[ix]

        gen = run(dom, None, params=pars)
        batch = next(gen)
        nb = 0
        while True:
            nb = max(nb, len(batch))
            try:
                batch = gen.send([cost(p) for p in batch])
            except StopIteration as stop:
                best = stop.value
                break
        print("NewSweep(%-12s) final=%s cost=%.6e nfev=%d max_batch=%d"
              % (label, best, seen[tuple(best)], nfev[0], nb))
