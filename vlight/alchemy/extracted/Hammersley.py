"""Hammersley — extracted from libalchemy.so (algs/Hammersley.cpp).

states:
    Init -> Start                                        @0x32bb80 / @0x32bd16
    Start -> Fork<AlgState, PointSubmit>(points)         @0x32bf90
    PointSubmit -> PointWait                             @0x32c08a
    PointWait -> {self, Return}                          @0x32c3b8

hyperparams (Data::Data(Params) @0x32c67e):
    seed       shared_ptr<long>      default -1 (deterministic)   0x32c772
    points     shared_ptr<unsigned>  default 10                   0x32c964
    population shared_ptr<Population> default Population(50)      0x32cb58
    dimOrder   shared_ptr<vector<int>> default empty (identity)   0x32cd4e

Point set (Alchemy::PointSet::HammersleyImpl):
    Phi(base,k) @0x3caf79 = sum_j d_j * base^-(j+1),  d_j = base-digits of k
                            (unsigned 32-bit div; base_k multiplies)
    GenerateHammersleyPoints @0x3cafe5:
        stride = 0x199 = 409 when dimFirst (production: dimFirst = true @0x32bee3)
        seed <= 0 (deterministic):
            D == 1 -> points[i][0]   = Phi(Primes[0], (i+1)*stride)
            D >= 2 -> points[i][0]   = (i+1)/N
                      points[i][d]   = Phi(Primes[d-1], (i+1)*stride)   for d >= 1
        seed > 0: drand48-scrambled variant, base index j = floor(drand48()*418+0.5)
    Primes = [2,3,5,7,11,...] (sieve to 1000004, >= 421 entries @0x6be7f0)

Mapping to the integer grid — PointSubmit::run @0x32c08a:
    idx = dimOrder[i];  design[idx] = floor(tunables[idx] * points[FORKIDX][i])
    No -1 and no clamp: u == 1.0 (i == N-1 on dim 0) maps to index == tunables,
    which is out of range; this module clamps through domain.clamp_index.
Batch: one Fork of N = *points, i.e. the whole sample set is submitted at once.
"""

import math
import random

STRIDE = 0x199      # 409, GenerateHammersleyPoints @0x3cb0a5
LINEAR_DIM0 = True  # dimFirst = true, Start::run @0x32bee3

K_SEED = "seed"          # 0x5674e9
K_POINTS = "points"      # 0x5674ee
K_POPULATION = "population"  # 0x5674f5
K_DIM_ORDER = "dimOrder"     # 0x567500

DEFAULTS = {
    K_SEED: -1,
    K_POINTS: 10,
    K_POPULATION: 50,
}

# first entries of Alchemy::PointSet::HammersleyImpl::Primes (sieve @0x3cadfc)
PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61,
          67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137,
          139, 149, 151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199]


def _extend_primes(n):
    """extendPrimeArray: sieve up to max(n, 3)-th prime, >= 421 entries available."""
    if n <= len(PRIMES):
        return
    limit = 6000
    while True:
        sieve = [True] * (limit + 1)
        sieve[0] = sieve[1] = False
        for i in range(2, int(math.sqrt(limit)) + 1):
            if sieve[i]:
                for j in range(i * i, limit + 1, i):
                    sieve[j] = False
        found = [i for i in range(2, limit + 1) if sieve[i]]
        if len(found) >= n:
            PRIMES[:] = found[:max(n, len(PRIMES))]
            return
        limit *= 2


def phi(base, k):
    """Alchemy::PointSet::HammersleyImpl::Phi @0x3caf79 (unsigned 32-bit)."""
    r = 0.0
    bk = base
    while k > 0:
        r += (k % base) / float(bk)      # 0x3cafa8
        k //= base                       # 0x3cafcc
        bk *= base                       # 0x3cafd5
    return r


def generate(points, dim_first, seed):
    """GenerateHammersleyPoints @0x3cafe5, deterministic branch (seed <= 0)."""
    n = len(points)
    d = len(points[0])
    if n == 0 or d == 0:
        raise AssertionError("N != 0 && D != 0")
    _extend_primes(max(n, d))
    stride = STRIDE if dim_first else 1
    if d == 1:
        for i in range(n):
            points[i][0] = phi(PRIMES[0], (i + 1) * stride)
        return points
    for i in range(n):
        points[i][0] = float(i + 1) / float(n)          # 0x3cb3d6
    for dim in range(1, d):
        p = PRIMES[dim - 1]                              # 0x3cb444
        for i in range(n):
            points[i][dim] = phi(p, (i + 1) * stride)    # 0x3cb45f
    return points


_PARAM_KEYS = (K_SEED, K_POINTS, K_POPULATION, K_DIM_ORDER)

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
    """Driver call #0: ``FunctionOptimizer::run`` (0x3ce70a, spec_core.md §6) evaluates
    the start point before the algorithm's fork is built.  Hammersley's point set does
    not depend on it; emitting the call keeps the sequence comparable point-for-point.
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
    seed = int(params.get(K_SEED, DEFAULTS[K_SEED]))
    npoints = int(params.get(K_POINTS, DEFAULTS[K_POINTS]))
    dim_order = params.get(K_DIM_ORDER)

    tun = [int(t) for t in domain.tunables]
    n = len(tun)
    if npoints <= 0:                       # assert *points>0  (Hammersley.cpp:54)
        raise AssertionError("*points>0")

    if dim_order is None:                  # 0x32bd50: identity fill
        order = list(range(n))
    else:
        order = [int(v) for v in dim_order]
    if len(order) != n:                    # assert(dimOrder->size() == dim) 0x32bde0
        raise AssertionError("dimOrder->size() == dim")

    if seed > 0:                           # scrambled variant, [unproven] drand48 port
        rnd = random.Random(seed)
    u = [[0.0] * n for _ in range(npoints)]
    if seed > 0:
        if n == 1:
            for i in range(npoints):
                u[i][0] = phi(PRIMES[0], (i + 1) * STRIDE)
        else:
            for i in range(npoints):
                u[i][0] = float(i + 1) / float(npoints)
            for d in range(1, n):
                j = int(math.floor(rnd.random() * 418.0 + 0.5))
                _extend_primes(max(j + 1, npoints, n))
                for i in range(npoints):
                    u[i][d] = phi(PRIMES[j], (i + 1) * STRIDE)
    else:
        generate(u, LINEAR_DIM0, seed)

    # ---- PointSubmit::run @0x32c08a: design[order[i]] = floor(tunables*u) ----
    batch = []
    for k in range(npoints):
        design = [0] * n
        for i in range(n):
            idx = order[i]
            design[idx] = int(math.floor(tun[idx] * u[k][i]))   # 0x32c229
        batch.append(list(domain.clamp_index(design)))

    # driver call #0: FunctionOptimizer::run (0x3ce70a) evaluates ``start`` before the
    # algorithm's Start::run @0x32bd16 forks the N PointSubmit children.  The point set
    # itself is independent of the start, so this is the only start-related call.
    yield [domain.from_index(_driver_start(domain, start))]

    costs = yield [domain.from_index(ix) for ix in batch]
    best_i = 0
    for i in range(1, len(batch)):
        if costs[i] < costs[best_i]:
            best_i = i
    return list(batch[best_i])


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

    def cost(p):
        nfev[0] += 1
        ix = tuple(dom.to_index(p))
        seen[ix] = p[0] * p[0] + p[1] * p[1]
        return seen[ix]

    gen = run(dom, None, params={"points": 64})
    batch = next(gen)
    first = dom.to_index(batch[0])
    while True:
        try:
            batch = gen.send([cost(p) for p in batch])
        except StopIteration as stop:
            best = stop.value
            break
    print("Hammersley final=%s cost=%.6e nfev=%d first=%s"
          % (best, seen[tuple(best)], nfev[0], first))
