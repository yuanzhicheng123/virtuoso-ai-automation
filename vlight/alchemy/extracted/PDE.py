"""PDE — extracted from libalchemy.so (Cadence Alchemy, `algs/PDE.cpp`).

visible_name : "Parallel Differential Evoluation"  (sic — spelling is the
               library's own, `.rodata`; this is **not** "partial differential
               equation")
states       : Init 0x38491e -> Start 0x384ab4 -> FirstOFAT 0x3852dc
               -> StartHammersley 0x3858de -> HammersleyDone 0x385e6e
               -> DESplit 0x3863a0 -> (Fork of GenerateMove 0x3864dc)
               -> DEWait 0x3873a8 -> DEJoin 0x387694 -> DESplit  (loop)
hyperparams  : ``dFactor`` = 0.7, ``crFactor`` = 0.8, ``stoppingRatio`` = 0.01,
               ``populationSize`` (0 -> 30 for nDim <= 20, 60 above),
               ``hammersley_points``, ``seed``, ``rng``, ``r0VectorPtr``,
               ``DE_population``, ``population``, ``totalDistance``.

Parallel Differential Evolution on the integer lattice, DE/rand/1/bin-like but
with two twists that are visible in the code:

* the difference vector is **normalised** and then walked in integer steps
  ``trial[d] = trunc(base[d] + k*unit[d] + 0.5)`` for k = 0,1,2,… — the k-family
  is generated until the first infeasible member, and then *one* member is
  picked with an index ``nSel = (int)(nCand*(dFactor + (1-dFactor)*u))``;
* the binomial "revert to parent" test is ``if (u > crFactor)``.

Both stop tests live in ``DEJoin``: relative spread ``(mean - best)/best <
stoppingRatio`` (0x387900) and ``4*populationSize > totalDistance`` (0x3879a8,
i.e. the generation collapsed).  ``totalDistance`` accumulates ``Σ|r0|²`` in
``GenerateMove`` (0x386a83) and is reset at every join (0x3879f4).

The shared kernel below is the family kernel used by all six extracted
algorithms; unused helpers are kept so that block stays identical everywhere.
"""
import math


# =============================================================================
#  Shared kernel — the machinery every stochastic algorithm in this family sits
#  on.  Byte-for-byte the same block appears in GreedyMinimization.py,
#  Anneal.py, Anneal2.py, PSA.py, PSA2.py and PDE.py (each file is standalone;
#  only the standard library is used).
#
#  Addresses are file/vaddr addresses inside libalchemy.so (ELF64, -O0).
# =============================================================================

MASK32 = 0xFFFFFFFF
MASK64 = 0xFFFFFFFFFFFFFFFF


class Return(Exception):
    """Alchemy::ShortCircuits::Return (kernel.h, typeinfo @0x6a8a78).

    The engine's structured "stop the whole run" signal: unwinds to the
    ask/tell driver.  In Python a generator simply ``return``s instead; the
    six ``run()`` bodies below use ``return`` and mention the address of the
    ``__cxa_throw`` they replace.
    """


class Gen:
    """Alchemy::Random::Gen @0x3cd40e — boost::random::mt19937 + uniform_01.

    Layout (spec_core.md §10.2): vptr, mt19937.state[624], seed @+0x9d0,
    step @+0x9d8.  ``Gen(unsigned long seed)`` does ``mt.seed((uint32)seed)``
    (0x3cd456) and sets ``step = 1`` (0x3cd46e).
    """

    N, M = 624, 397
    MATRIX_A, UPPER, LOWER = 0x9908B0DF, 0x80000000, 0x7FFFFFFF

    def __init__(self, seed, step=1):
        self.seed = seed & MASK64
        self.step = step
        self.mt = [0] * 624
        self.mt[0] = seed & MASK32
        for i in range(1, 624):
            self.mt[i] = (1812433253 * (self.mt[i - 1] ^ (self.mt[i - 1] >> 30)) + i) & MASK32
        self.mti = 624

    def _twist(self):
        mt = self.mt
        for k in range(624):
            y = (mt[k] & self.UPPER) | (mt[(k + 1) % 624] & self.LOWER)
            mt[k] = mt[(k + self.M) % 624] ^ (y >> 1) ^ (self.MATRIX_A if (y & 1) else 0)
        self.mti = 0

    def draw(self):
        """mt19937::operator() — one 32-bit tempered word."""
        if self.mti >= 624:
            self._twist()
        y = self.mt[self.mti]
        self.mti += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & MASK32

    def in01(self):
        """0x3cd65e ``Gen::in01()`` — one value, **two** engine words consumed.

        0x3cd688 builds ``boost::random::uniform_01<mt19937,double>`` from the
        engine **by value** (the frame is 0x9f0 B because the distribution embeds
        a second mt19937 at +0x8) and 0x3cd697 runs
        ``backward_compatible_uniform_01::operator()`` @0x3cd878 on that copy:
        ``(eng()-min()) * 2**-32``.  The value therefore comes from the *next*
        word without consuming it; 0x3cd6b3 / 0x3cd6c6 then discard two words of
        the real engine.  (Same model as ``Anneal.Gen.in01``, validated there.)
        """
        saved_mti, saved_mt = self.mti, self.mt[:]
        u = self.draw() / 4294967296.0    # the value comes from the engine copy
        self.mti, self.mt = saved_mti, saved_mt   # ... which consumed nothing real
        self.draw()                       # discarded @0x3cd6b3
        self.draw()                       # discarded @0x3cd6c6
        return u

    def inrange(self, n):
        """0x3cd623 — ``uniform_int_1_36_0<int>(0,n-1)`` -> ``uniform_smallint_1_41``.

        Not ``draw % n``: ``brange = 0xffffffff`` is halved to 0x7fffffff with
        ``mult = 2`` (0x3ce4fa..0x3ce515), then either ``mult = brange/n`` when it
        divides evenly (0x3ce524/0x3ce535) or both are scaled by the ``>>5`` loop
        (0x3ce548..0x3ce558); the result is ``(draw/mult) % n`` (0x3ce579).
        Exactly one word is consumed for every n >= 2.  23/23 identical to the
        real Gen for n = 2..24 (see Anneal.py, where this was validated).
        """
        if n <= 0:
            raise ValueError("inrange(%d): empty range" % n)
        if n == 1:
            return 0
        mult = 2
        brange = 0xFFFFFFFF >> 1
        brange += 1
        if brange % n == 0:
            mult = brange // n
        else:
            while n <= (brange // n) >> 5:
                brange >>= 1
                mult <<= 1
        return (self.draw() // mult) % n


class ForkedGen:
    """Random::ForkedGen(Gen const&, unsigned long n) @0x2d48a0 + next().

    ``Gen(Gen const& r, int a, int b)`` @0x3cd51a::

        s = r.seed + (a+1)*r.step ;  mt.seed((uint32)s)
        this->seed = s            ;  this->step = r.step * b
    """

    def __init__(self, parent, n):
        self.parent = parent
        self.n = n
        self.i = 0

    def next(self):
        p = self.parent
        a = self.i
        self.i += 1
        return Gen(p.seed + (a + 1) * p.step, p.step * self.n)


# --------------------------------------------------------------------------
#  MoveClass::Hustin  (algs/../helpers, "which variable gets perturbed")
# --------------------------------------------------------------------------
HUSTIN_NUM_CLASSES = 100        # Hustin::Parameters+0x00 @0x2eb76c
HUSTIN_CLASSES_PER_DIM = 4      # Hustin::Parameters+0x04 @0x2eb776
HUSTIN_SMOOTH_FACTOR = 3.0      # Hustin::Parameters+0x08, const 3.0 @0x561730


class Hustin:
    """Alchemy::MoveClass::Hustin, ctor @0x3c80c2 (0xd8 bytes).

    Decides WHICH variable to perturb.  It carries no step size — the step
    comes from MoveRange::selectMove.  Weight bookkeeping (spec_anneal.md §5.3):
    ``w[d] += |dCost|`` when a move is accepted *and* actually moved, quotas are
    rebuilt every ``numClasses`` (=100) moves and low-pass filtered with
    ``smooth = quota + (smooth - quota)/3.0``.
    """

    def __init__(self, n_dim, rng, cost,
                 num_classes=HUSTIN_NUM_CLASSES,
                 classes_per_dim=HUSTIN_CLASSES_PER_DIM,
                 smooth_factor=HUSTIN_SMOOTH_FACTOR):
        self.rng = rng
        self.n_dim = n_dim
        self.ncls = num_classes
        self.cpd = classes_per_dim
        self.smooth_factor = smooth_factor
        self.sparse = self.ncls > 2 * n_dim                 # +0x10 @0x3c812f
        self.lifetime = num_classes                         # +0x38 @0x3c816f
        self.w = [0.0] * n_dim                              # +0x40
        self.succ = [0] * n_dim                             # +0x60
        self.sel = [i % n_dim for i in range(num_classes * n_dim)]   # +0x78
        self.quota = [float(classes_per_dim)] * n_dim       # +0x90
        self.smooth = [float(classes_per_dim)] * n_dim      # +0xa8
        self.cur = 0                                        # +0xc8
        self.last = cost                                    # +0xd0
        self.generate_new_move_class()                      # 0x3c832a

    # ---- 0x3c8d32 -------------------------------------------------------
    def generate_new_move_class(self):
        r = self.rng.inrange(len(self.sel))
        self.cur = self.sel[r]
        if self.sparse:                     # numClasses > 2*nDim
            k = self.lifetime - 1
            if k < self.n_dim and self.succ[k] == 0:
                self.cur = k                # cover never-improving dims

    # ---- 0x3c8e2e -------------------------------------------------------
    def getNextClass(self):
        return self.cur

    # ---- 0x3c8c3a -------------------------------------------------------
    def update(self, cost, accepted, moved):
        mag = abs(self.last - cost)
        if accepted and moved:
            self.w[self.cur] += mag
            self.succ[self.cur] += 1
        self.last = cost
        if not moved:
            return
        self.lifetime -= 1
        if self.lifetime <= 0:
            self.recalculate_weights()
        self.generate_new_move_class()

    # ---- 0x3c842e -------------------------------------------------------
    def recalculate_weights(self):
        n = self.n_dim
        Q = 0.0
        for d in range(n):
            if self.succ[d]:
                Q += self.w[d] / float(self.succ[d])
        if Q > 0.0:
            remaining = len(self.sel)
            done = [False] * n
            changed = True
            while changed:
                changed = False
                for d in range(n - 1, -1, -1):
                    if done[d]:
                        continue
                    if self.succ[d]:
                        share = (self.w[d] / float(self.succ[d])) / Q * remaining
                    else:
                        share = 0.0
                    if share < 1.0:                     # 1.0 @0x577a70
                        share = 1.0
                        remaining -= 1
                        done[d] = True
                        if self.succ[d]:
                            Q -= self.w[d] / float(self.succ[d])
                        changed = True
                    self.quota[d] = share
        else:
            for d in range(n):
                self.quota[d] = float(self.cpd)         # 4.0
        self.quota, self.smooth = self.smooth, self.quota      # swap @0x3c88a2
        for d in range(n):
            self.smooth[d] = self.quota[d] + (self.smooth[d] - self.quota[d]) / self.smooth_factor
            self.w[d] = 0.0
            self.succ[d] = 0
        # priority_queue<RecalcEntry(dim, value), less<RecalcEntry>> — a max-heap
        # on the smoothed quota; the tie rule is [unproven].  The original
        # asserts !AllocPriorityQueue.empty() @0x3c8abb, which a Q == 0 run
        # (quota = classesPerDim, i.e. 4 per dim instead of numClasses*nDim
        # slots) cannot satisfy, so the queue is refilled defensively.
        pq = [(self.smooth[d], d) for d in range(n)]
        idx = 0                                                       # +0x34
        while idx < len(self.sel):
            if not pq:
                pq = [(self.smooth[d], d) for d in range(n)]      # [deviation]
            pq.sort(reverse=True)                                 # top() = largest
            val, d = pq.pop(0)
            k = int(val)                                          # cvttsd2si @0x3c8afb
            if k == 0:
                k = 1
            else:
                val -= float(k)
                if val > 0.0:
                    pq.append((val, d))                           # @0x3c8b50
            for _ in range(k):
                if idx >= len(self.sel):
                    break
                self.sel[idx] = d                                 # @0x3c8bc6
                idx += 1
        self.lifetime = self.ncls                       # back to 100


# --------------------------------------------------------------------------
#  MoveRange::selectMove  (the only step-size generator in the family)
# --------------------------------------------------------------------------
MOVE_W0 = 0.4        # @0x577d30
MOVE_W_SLOPE = -0.35  # @0x577d40
MOVE_W_ASYMPTOTE = 0.05  # @0x577d38 (immediate only)


def select_move(num_indices, current_index, t, rng):
    """Alchemy::MoveRange::selectMove(int,int,double,Random::Gen&) @0x3caadd.

    ``kmax = max(1, floor((N-1)*(0.4 - 0.35*t) + 0.5))``; the new level is
    uniform on ``[c-kmax, c+kmax] ∩ [0, N-1]`` with ``c`` itself excluded.
    Truncation, never reflection.
    """
    if num_indices <= 0:
        raise ValueError("numIndices > 0")
    if not (0 <= current_index < num_indices):
        raise ValueError("0 <= currentIndex < numIndices")
    w = MOVE_W0 + MOVE_W_SLOPE * t                      # 0x3cab9b / 0x3caba7
    kmax = int(math.floor((num_indices - 1) * w + 0.5))  # floor(x+0.5) @0x577d48
    if kmax < 1:
        kmax = 1
    hi = min(num_indices - 1, current_index + kmax)
    lo = max(0, current_index - kmax)
    n_below = current_index - lo
    n_above = hi - current_index
    r = rng.inrange(n_below + n_above)
    if r >= n_below:
        r += 1                                          # skip currentIndex @0x3cace7
    answer = lo + r
    return answer


# --------------------------------------------------------------------------
#  CoolingSchedule::KrazKool1  (accept criterion + non-exponential schedule)
# --------------------------------------------------------------------------
KK_TOTAL_MOVES = 2000          # Parameters+0x00 @0x3c69ee
KK_MOVES_PER_TEMP = 50         # Parameters+0x08 @0x3c69f9
KK_A = 0.15                    # Parameters+0x10 @0x577868
KK_B = 0.60                    # Parameters+0x18 @0x577870
KK_T_HIGH = 0.40               # Parameters+0x20 @0x577878
KK_T_MID = 0.07                # Parameters+0x28 @0x577880
KK_T_LOW = 1e-06               # Parameters+0x30 @0x577888  (also the Δ fast path)
KK_DAMPING = 2.0               # Parameters+0x38 @0x577890
KK_T_FLOOR = 1e-30             # Parameters+0x40 @0x577898
KK_EPS = 1e-4                  # State+0x88, const @0x5778a0
KK_QUENCH_RATE = 0.1           # @0x5778b0
KK_REHEAT_DIV = 0.82           # @0x5778b8
KK_LATE_FACTOR = 0.9           # @0x5778c0
KK_DEV_FALLBACK = 0.01         # @0x5778c8
KK_ADJUST_CLAMP = 0.5          # @0x5778a8 / -0.5 @0x5778d0
KK_FROZEN_AFTER = 7            # isFrozen(): nEquil > 7 @0x3c7934


class Acc:
    """StatisticalAccumulator @0x3c6656: n, min, max, sum, sumSq."""

    __slots__ = ("n", "mn", "mx", "sum", "sumsq")

    def __init__(self):
        self.reset()

    def reset(self):
        self.n = 0
        self.mn = 0.0
        self.mx = 0.0
        self.sum = 0.0
        self.sumsq = 0.0

    def accumulate(self, x):
        if self.n == 0:
            self.mn = self.mx = x
        else:
            if x < self.mn:
                self.mn = x
            if x > self.mx:
                self.mx = x
        self.n += 1
        self.sum += x
        self.sumsq += x * x

    def mean(self):
        return self.sum / self.n if self.n else 0.0

    def deviation(self):
        if self.n <= 1:
            return 0.0
        mean = self.mean()
        var = (self.sumsq - mean * self.sum) / (self.n - 1)
        return math.sqrt(var) if var > 0.0 else 0.0


class KrazKool1:
    """Alchemy::CoolingSchedule::KrazKool1 (ctor @0x3c6a7c, State @0x3c7c60).

    Not exponential cooling: quench to T_floor, reheat to
    ``bestCost*target/0.82``, then adapt by ``1 - adjust`` and finally ×0.9.
    Also implements the Metropolis criterion (@0x3c7aa8).
    """

    def __init__(self, temperature, rng, cost,
                 total_moves=KK_TOTAL_MOVES, moves_per_temperature=KK_MOVES_PER_TEMP):
        self.rng = rng
        self.total_moves = total_moves
        self.mpt = moves_per_temperature
        # ---- State ----
        self.temperature = float(temperature)      # +0x00
        self.n_acc = 0                             # +0x08
        self.n_rej = 0                             # +0x0c
        self.temp_idx = 0                          # +0x10
        self.phase = 0                             # +0x38
        self.cnt_a = 0                             # +0x3c
        self.cnt_b = 0                             # +0x40
        self.best_cost = float("inf")              # +0x48 (DBL_MAX)
        self.acc = Acc()                           # +0x50
        self.last_cost = float(cost)               # +0x78
        self.max_delta = 0.0                       # +0x80
        self.eps = KK_EPS                          # +0x88
        self.n_equil = 0                           # +0x90
        # ---- temps[] table, 0x3c6a7c ----
        self.num_temps = (total_moves + moves_per_temperature - 1) // moves_per_temperature
        n_t = self.num_temps
        n1 = int(KK_A * n_t + 0.5)                 # 0x3c6c71 (+0.5 @0x5778a8)
        n2 = int(KK_B * n_t + 0.5)                 # 0x3c6ca4
        temps = [KK_T_HIGH * math.exp(i * (math.log(KK_T_MID / KK_T_HIGH) / n1))
                 for i in range(n1)]               # 0x3c6d0f..0x3c6d44
        temps += [KK_T_MID] * (n2 - n1)            # 0x3c6d5e..0x3c6d8c
        r2 = math.log(KK_T_MID / KK_T_LOW) / float(n2 - n_t)   # 0x3c6d97
        t0 = KK_T_MID / math.exp(n2 * r2)          # 0x3c6de4
        temps += [t0 * math.exp(i * r2) for i in range(n2, n_t)]   # 0x3c6e1c
        self.temps = temps                         # +0x20

    # ---- 0x3c6fc0 -------------------------------------------------------
    def temperature_value(self):
        return self.temperature

    # ---- 0x3c7868 -------------------------------------------------------
    def time(self):
        return (self.temp_idx * self.mpt + self.n_acc + self.n_rej) / float(self.total_moves)

    # ---- 0x3c7934 -------------------------------------------------------
    def isFrozen(self):
        return self.n_equil > KK_FROZEN_AFTER

    # ---- 0x3c7952 -------------------------------------------------------
    def setCost(self, cost):
        self.last_cost = float(cost)

    # ---- 0x3c7aa8 (the acceptance function itself) ----------------------
    def criterion(self, T, d):
        if KK_T_LOW > d:
            return True                             # 1e-6 > Δ → free pass, no draw
        if T == 0.0:
            return not (d > 0.0)                    # Δ <= 0 only
        p = math.exp(-d / T)                        # xorpd -0.0 @0x577850
        u = self.rng.in01()
        return p > u                                # seta → strictly greater

    # ---- 0x3c78c6 -------------------------------------------------------
    def tryMove(self, cost):
        d = cost - self.last_cost
        accept = self.criterion(self.temperature, d)
        self.update(cost, accept)
        return accept

    # ---- 0x3c7010 -------------------------------------------------------
    def update(self, cost, accepted):
        if accepted:
            d = cost - self.last_cost
            if d > self.max_delta:
                self.max_delta = d
            self.acc.accumulate(cost)
            self.last_cost = cost
            self.n_acc += 1
        else:
            self.n_rej += 1
            cost = self.last_cost
        if self.n_acc + self.n_rej >= self.mpt:
            self.decrement_temperature()            # 0x3c7113
            equilibrated = False
            if self.acc.n != 0:
                spread = self.acc.mx - self.acc.mn - self.max_delta
                if self.acc.mean() * self.eps > spread:
                    equilibrated = True
            if equilibrated:
                self.n_equil += 1
            self.max_delta = 0.0
            self.temp_idx += 1
            self.n_acc = 0
            self.n_rej = 0
            self.acc.reset()
            self.acc.accumulate(cost)

    # ---- 0x3c7290 (real cooling law) ------------------------------------
    def decrement_temperature(self):
        if self.temp_idx >= self.num_temps:
            self.temperature *= 0.5                 # 0.5 @0x5778a8
            return
        if self.phase == 1 and self.cnt_b > 6:
            self.phase = 0
            self.cnt_b = 0
        if self.phase == 0:
            moves = self.n_acc + self.n_rej
            rate = (self.n_acc / float(moves)) if moves else 0.0
            if self.cnt_a <= 1 or rate > KK_QUENCH_RATE:
                self.cnt_a += 1
                self.temperature = KK_T_FLOOR        # quench
            else:
                if self.best_cost > self.acc.mn:
                    self.best_cost = self.acc.mn
                self.phase = 1
                self.cnt_a = 0
                target = self.temps[self.temp_idx]
                self.temperature = (self.best_cost * target) / KK_REHEAT_DIV
                if KK_T_FLOOR > self.temperature:
                    self.temperature = KK_T_FLOOR
        else:
            if self.best_cost > self.acc.mn:
                self.best_cost = self.acc.mn
            if self.cnt_b > 3:
                self.temperature *= KK_LATE_FACTOR
                self.cnt_b += 1
            else:
                dev_pct = (self.acc.deviation() / self.best_cost) if self.acc.n > 1 else 0.0
                if not (dev_pct > 0.0):
                    dev_pct = KK_DEV_FALLBACK
                target = self.temps[self.temp_idx]
                if target > 0.0:
                    adjust = (dev_pct - target) / (KK_DAMPING * target)
                else:
                    adjust = 0.5
                if adjust > KK_ADJUST_CLAMP:
                    adjust = KK_ADJUST_CLAMP
                if adjust < -KK_ADJUST_CLAMP:
                    adjust = -KK_ADJUST_CLAMP
                self.cnt_b += 1
                self.temperature = (1.0 - adjust) * self.temperature


class Greedy:
    """Alchemy::CoolingSchedule::Greedy — ctor @0x3a0b82.

    ``tryMove = (cost_ > newCost)`` @0x3a0bc2: strict improvement only, no RNG.
    ``isFrozen`` is always false @0x3a0c00, ``time`` always 0.0 @0x3a0c10.
    """

    def __init__(self, cost):
        self.cost = float(cost)

    def tryMove(self, cost):
        return self.cost > cost

    def setCost(self, cost):
        self.cost = float(cost)

    def isFrozen(self):
        return False

    def time(self):
        return 0.0


# --------------------------------------------------------------------------
#  Design points, the factory cache and the bounded archive
# --------------------------------------------------------------------------
class Pt:
    """Alchemy::DesignPoint — the only state is an integer level per tunable."""

    __slots__ = ("ix", "cost")

    def __init__(self, ix, cost=None):
        self.ix = list(ix)
        self.cost = cost

    def isEvaluated(self):
        return self.cost is not None

    def __repr__(self):
        return "Pt(%r, %r)" % (self.ix, self.cost)


class Factory:
    """DesignPointFactory + Cache (spec_core.md §8): one point object per design.

    ``create(design)`` is a *cache lookup*: an already-seen design comes back
    already evaluated, exactly like ``Cache::hit`` @0x3d7976, so it costs no
    new simulation.
    """

    def __init__(self, domain):
        self.domain = domain
        self.tunables = list(domain.tunables)
        self.cache = {}

    def create(self, ix=None):
        if ix is None:
            ix = list(self.domain.center_index())
        key = tuple(int(v) for v in ix)
        pt = self.cache.get(key)
        if pt is None:
            pt = Pt(key)
            self.cache[key] = pt
        return pt

    def nDims(self):
        return len(self.tunables)

    def levels(self, d):
        return self.tunables[d]

    def all_points(self):
        return list(self.cache.values())


class FreshFactory(Factory):
    """``DesignPointFactoryImpl::create`` @0x3cef6e / 0x3cf016 -- always a new point.

    ``Cache::hit`` has no caller inside libalchemy.so (PARITY.md §4.2), so the same
    design is evaluated again every time the state machine asks for it.  The cached
    ``Factory`` above is what the earlier version of this module used, which made
    repeated designs free and cut every generation short.
    """

    def create(self, ix=None):
        if ix is None:
            ix = list(self.domain.center_index())
        return Pt([int(v) for v in ix])


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------- #
_HASH_MUL = 0xEF32F8C9      # 0x3d1ed0  初始乘子
_HASH_MUL_STEP = 0x5C6B7    # 0x3d2036  每维推进


def _hash_fold(values, rot_src):
    """fold(values) using the rotation amounts taken from `rot_src` (0x3d1ec2..0x3d2044)."""
    h = 0
    mul = _HASH_MUL
    for i, v in enumerate(values):
        v = int(v)
        h = (h * mul + (v & 0xFFFFFFFF)) & 0xFFFFFFFF     # 0x3d1f08..0x3d1f57
        rv = int(rot_src[i])
        r = ((rv % 16) + 16) % 16                          # 0x3d1f73..0x3d1f80
        h = ((h << r) + (h >> (16 - r))) & 0xFFFFFFFF     # 0x3d1f87..0x3d1fc5
        mul = (mul * _HASH_MUL_STEP) & 0xFFFFFFFF         # 0x3d2036
    return h


def _dp_less(ia, ca, ib, cb, ha=False, hb=False):
    """Exact `DesignPoint::operator<` @0x3d1dda."""
    if ca < cb:                                          # 0x3d1e01
        return True
    if cb < ca:                                          # 0x3d1e2a
        return False
    if (not ha) and hb:                                  # 0x3d1e3d..0x3d1e58
        return True
    if ha and (not hb):                                  # 0x3d1e62..0x3d1e7d
        return False
    if len(ia) != len(ib):                               # 0x3d1e87..0x3d1ebd
        return False
    h1 = _hash_fold(ia, ia)                              # 0x3d1ec2..0x3d1fc5
    h2 = _hash_fold(ib, ia)                              # 0x3d1fc8..0x3d2033 (r from ia!)
    if h1 < h2:                                          # 0x3d2049..0x3d2068 (unsigned)
        return True
    if h2 < h1:
        return False
    for x, y in zip(ia, ib):                             # 0x3d2074..0x3d2127
        if x < y:
            return True
        if y < x:
            return False
    return False                                         # 0x3d212c


def _pt_less(a, b):
    """`*(DesignPoint*)a < *(DesignPoint*)b` for two archived Pt objects."""
    if a is b:
        return False
    return _dp_less(a.ix, a.cost, b.ix, b.cost)


# --------------------------------------------------------------------------
#  The archive: `Alchemy::Population<shared_ptr<DesignPoint>, evil_ptr_less>`
# --------------------------------------------------------------------------
# Why the red-black tree and not "a sorted list": the container really is a
# `std::set` (insert @0x2efdf0, discardExtras 0x2f17a0), and `DesignPoint::
# operator<` is **not** a strict weak ordering for equal-cost points (both hash
# accumulators are rotated with the LEFT operand's digits), so "equivalent" is
# not transitive and the tree *shape* decides which points survive.  PSA reads
# the archive's in-order traversal for `current_point` (HammersleyDone 0x39f620)
# and samples from it every round (0x3a049b), so the tree has to be exact.  The
# operations below are the libstdc++ `bits/stl_tree.h` ones, transcribed
# operation for operation (same code as OFATSearch.py, payload = Pt).

_RED = True
_BLACK = False


class _PopNode(object):
    """`std::_Rb_tree_node<shared_ptr<DesignPoint>>`: payload + links + colour."""

    __slots__ = ("pt", "l", "r", "p", "red")

    def __init__(self, pt):
        self.pt = pt
        self.l = None
        self.r = None
        self.p = None
        self.red = _RED


def _tree_min(x):
    while x.l is not None:
        x = x.l
    return x


def _tree_max(x):
    while x.r is not None:
        x = x.r
    return x


class Population(object):
    """`Alchemy::Population<shared_ptr<DesignPoint>, evil_ptr_less>` (0x2efdf0)."""

    def __init__(self, capacity):
        self.cap = int(capacity)
        self.root = None
        self.leftmost = None
        self.rightmost = None
        self.n = 0

    # ---- Population::resize @0x371722 (sets maxsize_, then discardExtras) ---
    def resize(self, capacity):
        self.cap = int(capacity)
        self._discard_extras()

    # ---- Population::insertAndDiscardExtras @0x2efdf0 ---------------------
    def insert(self, pt):
        if pt is None or pt.cost is None:
            return
        self._insert_unique(pt)                      # std::set::insert @0x2efe0e
        self._discard_extras()                       # discardExtras @0x2f17a0

    # ---- discardExtras @0x2f17a0: erase(--end()) while size() > maxsize ----
    def _discard_extras(self):
        while self.n > self.cap and self.rightmost is not None:
            self.erase(self.rightmost)

    # ---- std::set::insert -> _M_get_insert_unique_pos ---------------------
    def _insert_unique(self, pt):
        x = self.root
        y = None                                     # `_M_end()` (the header)
        comp = True
        while x is not None:                         # descend: less(new, node)
            y = x
            comp = _pt_less(pt, x.pt)
            x = x.l if comp else x.r
        j = y
        if comp:
            if j is self.leftmost:                   # `if (j == begin())` -> insert
                j = None
            else:
                j = self._prev(j)
        if j is not None and not _pt_less(j.pt, pt):  # equivalent -> rejected
            return False
        node = _PopNode(pt)
        self._insert_rebalance(True if y is None else comp, node, y)
        self.n += 1
        return True

    @staticmethod
    def _prev(x):
        if x.l is not None:
            return _tree_max(x.l)
        y = x.p
        while y is not None and x is y.l:
            x = y
            y = y.p
        return y

    @staticmethod
    def _next(x):
        if x.r is not None:
            return _tree_min(x.r)
        y = x.p
        while y is not None and x is y.r:
            x = y
            y = y.p
        return y

    # ---- _Rb_tree_rotate_left / _Rb_tree_rotate_right ---------------------
    def _rotate_left(self, x):
        y = x.r
        x.r = y.l
        if y.l is not None:
            y.l.p = x
        y.p = x.p
        if x is self.root:
            self.root = y
        elif x is x.p.l:
            x.p.l = y
        else:
            x.p.r = y
        y.l = x
        x.p = y

    def _rotate_right(self, x):
        y = x.l
        x.l = y.r
        if y.r is not None:
            y.r.p = x
        y.p = x.p
        if x is self.root:
            self.root = y
        elif x is x.p.r:
            x.p.r = y
        else:
            x.p.l = y
        y.r = x
        x.p = y

    # ---- _Rb_tree_insert_and_rebalance ------------------------------------
    def _insert_rebalance(self, insert_left, x, p):
        x.p = p
        x.l = None
        x.r = None
        x.red = _RED
        if insert_left:
            if p is None:                            # p == header: empty tree
                self.root = x
                self.rightmost = x
                self.leftmost = x
            else:
                p.l = x
                if p is self.leftmost:
                    self.leftmost = x
        else:
            p.r = x
            if p is self.rightmost:
                self.rightmost = x
        while x is not self.root and x.p.red:
            xpp = x.p.p
            if x.p is xpp.l:
                y = xpp.r
                if y is not None and y.red:
                    x.p.red = _BLACK
                    y.red = _BLACK
                    xpp.red = _RED
                    x = xpp
                else:
                    if x is x.p.r:
                        x = x.p
                        self._rotate_left(x)
                    x.p.red = _BLACK
                    xpp.red = _RED
                    self._rotate_right(xpp)
            else:
                y = xpp.l
                if y is not None and y.red:
                    x.p.red = _BLACK
                    y.red = _BLACK
                    xpp.red = _RED
                    x = xpp
                else:
                    if x is x.p.l:
                        x = x.p
                        self._rotate_right(x)
                    x.p.red = _BLACK
                    xpp.red = _RED
                    self._rotate_left(xpp)
        self.root.red = _BLACK

    # ---- _Rb_tree_rebalance_for_erase (libstdc++ verbatim) ----------------
    def erase(self, z):
        y = z
        x = None
        x_parent = None
        if y.l is None:
            x = y.r
        elif y.r is None:
            x = y.l
        else:
            y = y.r
            while y.l is not None:
                y = y.l
            x = y.r
        if y is not z:
            z.l.p = y
            y.l = z.l
            if y is not z.r:
                x_parent = y.p
                if x is not None:
                    x.p = y.p
                y.p.l = x
                y.r = z.r
                z.r.p = y
            else:
                x_parent = y
            if self.root is z:
                self.root = y
            elif z.p.l is z:
                z.p.l = y
            else:
                z.p.r = y
            y.p = z.p
            y.red, z.red = z.red, y.red
            y = z
        else:
            x_parent = y.p
            if x is not None:
                x.p = y.p
            if self.root is z:
                self.root = x
            elif z.p.l is z:
                z.p.l = x
            else:
                z.p.r = x
            if self.leftmost is z:
                self.leftmost = z.p if z.r is None else _tree_min(x)
            if self.rightmost is z:
                self.rightmost = z.p if z.l is None else _tree_max(x)
        if y.red is not _RED:
            while x is not self.root and (x is None or x.red is _BLACK):
                if x is x_parent.l:
                    w = x_parent.r
                    if w.red:
                        w.red = _BLACK
                        x_parent.red = _RED
                        self._rotate_left(x_parent)
                        w = x_parent.r
                    if ((w.l is None or w.l.red is _BLACK) and
                            (w.r is None or w.r.red is _BLACK)):
                        w.red = _RED
                        x = x_parent
                        x_parent = x.p if x is not None else None
                    else:
                        if w.r is None or w.r.red is _BLACK:
                            if w.l is not None:
                                w.l.red = _BLACK
                            w.red = _RED
                            self._rotate_right(w)
                            w = x_parent.r
                        w.red = x_parent.red
                        x_parent.red = _BLACK
                        if w.r is not None:
                            w.r.red = _BLACK
                        self._rotate_left(x_parent)
                        break
                else:
                    w = x_parent.l
                    if w.red:
                        w.red = _BLACK
                        x_parent.red = _RED
                        self._rotate_right(x_parent)
                        w = x_parent.l
                    if ((w.r is None or w.r.red is _BLACK) and
                            (w.l is None or w.l.red is _BLACK)):
                        w.red = _RED
                        x = x_parent
                        x_parent = x.p if x is not None else None
                    else:
                        if w.l is None or w.l.red is _BLACK:
                            if w.r is not None:
                                w.r.red = _BLACK
                            w.red = _RED
                            self._rotate_left(w)
                            w = x_parent.l
                        w.red = x_parent.red
                        x_parent.red = _BLACK
                        if w.l is not None:
                            w.l.red = _BLACK
                        self._rotate_right(x_parent)
                        break
            if x is not None:
                x.red = _BLACK
        self.n -= 1
        return y

    def erase_max(self):
        """`discardExtras` erases the in-order tail, one element at a time."""
        if self.rightmost is not None:
            self.erase(self.rightmost)

    def top(self):
        """Population::top() @0x2efeea -- the in-order first element."""
        return self.leftmost.pt if self.leftmost is not None else None

    def worst(self):
        return self.rightmost.pt if self.rightmost is not None else None

    def __len__(self):
        return self.n

    def __iter__(self):
        out = []
        x = self.leftmost
        while x is not None:
            out.append(x.pt)
            x = self._next(x)
        return iter(out)

    def items(self):
        return list(self)

    as_list = items


def check_domain(domain):
    """One-shot validation of the domain contract.

    ``run()`` works purely in index space; ``from_index``/``to_index`` are the
    caller's bridge to real coordinates.  This check makes sure the grid the
    caller advertises (``tunables``) and the index conversion agree in shape,
    so a mismatched domain fails immediately instead of degrading the search.
    """
    tun = list(domain.tunables)
    centre = list(domain.center_index())
    if len(centre) != len(tun):
        raise ValueError("center_index() has %d entries, tunables has %d"
                         % (len(centre), len(tun)))
    back = list(domain.to_index(domain.from_index(centre)))
    if len(back) != len(tun):
        raise ValueError("to_index(from_index(centre)) has %d entries, expected %d"
                         % (len(back), len(tun)))
    return centre


def sample_with_replacement(rng, pts, n):
    """Random::sampleWithReplacement (used by PSA @0x3a049b, PSA2 @0x398d01)."""
    out = []
    if not pts:
        return out
    for _ in range(n):
        out.append(pts[rng.inrange(len(pts))])
    return out


def shuffle(rng, seq):
    """std::random_shuffle with Random::Gen::inrange as the generator."""
    a = list(seq)
    n = len(a)
    for i in range(n - 1, 0, -1):
        j = rng.inrange(i + 1)
        a[i], a[j] = a[j], a[i]
    return a


# --------------------------------------------------------------------------
#  Hammersley warm start (PointSet::HammersleyImpl @0x3cafe5)
# --------------------------------------------------------------------------
def _sieve(n):
    flags = [True] * (n + 1)
    flags[0] = flags[1] = False
    i = 2
    while i * i <= n:
        if flags[i]:
            for j in range(i * i, n + 1, i):
                flags[j] = False
        i += 1
    return [i for i in range(2, n + 1) if flags[i]]


_PRIMES = _sieve(1000004)      # SieveOfEratosthenes @0x3cadfc


def phi(base, k):
    """HammersleyImpl::Phi @0x3caf79 — van der Corput radical inverse."""
    r = 0.0
    bk = base
    while k > 0:
        r += (k % base) / float(bk)
        k //= base
        bk *= base
    return r


def hammersley_points(n, dim, dim_first=True, seed=0):
    """GenerateHammersleyPoints @0x3cafe5 (deterministic branch, seed <= 0).

    ``stride = 409`` when ``dim_first`` (production, 0x3cb0a5), dim 0 is the
    linear grid ``(i+1)/N``, dim d>=1 uses prime ``Primes[d-1]``.
    """
    if n <= 0 or dim <= 0:
        raise ValueError("points/dim must be positive")
    stride = 409 if dim_first else 1
    pts = [[0.0] * dim for _ in range(n)]
    if dim == 1:
        for i in range(n):
            pts[i][0] = phi(_PRIMES[0], (i + 1) * stride)
        return pts
    for i in range(n):
        pts[i][0] = (i + 1) / float(n)                 # 0x3cb178
    for d in range(1, dim):
        base = _PRIMES[d - 1]                          # 0x3cb441
        for i in range(n):
            pts[i][d] = phi(base, (i + 1) * stride)
    return pts


def hammersley_design(factory, dim_order, unit_pt):
    """Hammersley::PointSubmit @0x32c08a: floor(tunables* u), no -1, no clamp."""
    n = factory.nDims()
    order = list(dim_order) if dim_order else list(range(n))
    design = [0] * n
    for i in range(n):
        t = order[i]
        design[t] = int(math.floor(factory.tunables[t] * unit_pt[i]))
        if design[t] > factory.tunables[t] - 1:        # defensive: u must be < 1
            design[t] = factory.tunables[t] - 1
    return design


def all_specs_good_enough(domain, pt):
    """DesignPoint::allSpecsGoodEnough — host hook, absent from the engine.

    spec_server.md §9: this flag has **zero callers** inside libalchemy.so, so
    the models call an optional domain hook and otherwise answer False.
    """
    fn = getattr(domain, "all_specs_good_enough", None)
    if fn is None or pt is None:
        return False
    return bool(fn(pt.ix, pt.cost))


def drive(gen, evaluate):
    """Self-test driver for the ask/tell protocol (the FunctionOptimizer loop).

    Returns ``(best_index_list, nfev)``.  ``evaluate`` maps one index list to
    one cost; only points the generator actually *asks* for are counted.
    """
    nfev = 0
    try:
        batch = next(gen)
    except StopIteration as stop:
        return (stop.value, 0)
    while True:
        costs = [evaluate(ix) for ix in batch]
        nfev += len(costs)
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            return (stop.value, nfev)


# =============================================================================
#  PDE
# =============================================================================
DEFAULT_DFACTOR = 0.7              # key "dFactor"       default 0.7  @0x570cd8
DEFAULT_CRFACTOR = 0.8             # key "crFactor"      default 0.8  @0x570ce0
DEFAULT_STOPPING_RATIO = 0.01      # key "stoppingRatio" default 0.01 @0x570ce8
DEFAULT_RATIO = 0.01               # Alchemy::PDE::DefaultRatio        @0x570480
ROUNDING = 0.5                     # 0x570cf0  (+0.5 before cvttsd2si)
DEGENERATE_NORM2 = 0.1             # 0x570d10  (dither threshold)
ONE = 1.0                          # 0x570d18
MINUS_ONE = -1.0                   # 0x570d20
POP_SMALL = 30                     # imm 0x1e @0x384b3f
POP_LARGE = 60                     # imm 0x3c @0x384b38
POP_LARGE_DIMS = 20                # imm 0x14 @0x384b33
OFAT_STEP_FRACTION = 0.25          # const 0.25 @0x570cf8 ("design_steps")
POPULATION_ARCHIVE = 90            # "population" default  [unproven]
STOP_DISTANCE_FACTOR = 4.0         # `shl eax,2` @0x387982


def _pick_parents(rng, idx, n_pop, r0vec):
    """Two distinct donors, both different from the fork index and from r0vec[idx]."""
    i3 = r0vec[idx]
    while True:
        i2 = rng.inrange(n_pop)                     # @0x38659e
        if i2 != idx and i2 != i3:
            break
    while True:
        j3 = rng.inrange(n_pop)                     # @0x38661a
        if j3 != idx and j3 != i2 and j3 != i3:
            break
    return i2, j3


def _ieee_div(a, b):
    """``a / b`` with IEEE-754 semantics (Python raises where C++ gives inf/NaN)."""
    if b != 0.0:
        return a / b
    if a == 0.0 or a != a:
        return float("nan")
    return math.copysign(float("inf"), a)


def _gen_copy(rng):
    """``PDE::RandomFn::RandomFn(Gen&)`` @0x387cb6 -- the functor holds a *copy*.

    ``Gen::Gen(Gen const&)`` @0x2b1dd0 deep-copies the mt19937 array and its index,
    so ``std::random_shuffle`` consumes words from that copy and the algorithm's own
    stream (state+0x118) is left where it was.  The stream still advances inside
    ``DEJoin`` -- the join's ``sampleWithReplacement`` uses the real Gen -- so the
    permutations do change from round to round; what the copy buys is that the
    shuffle's own ``inrange`` draws are not part of that stream.
    """
    g = Gen.__new__(Gen)
    g.seed = rng.seed
    g.step = rng.step
    g.mt = list(rng.mt)
    g.mti = rng.mti
    return g


def _random_shuffle(rng, seq):
    """``std::random_shuffle(first,last,RandomFn&)`` @0x38c820 -- forward loop.

        for (i = first+1; i != last; ++i) {
            j = first + rand((i - first) + 1);      // rand == Gen::inrange
            if (i != j) iter_swap(i, j);
        }

    ``std::shuffle``/``random_shuffle``'s usual *backwards* ``j = rand(i+1)`` form is
    a different permutation; the library instantiates this one (0x38c86c..0x38c8dc)
    and calls ``RandomFn::operator()`` @0x387cdc, i.e. ``Gen::inrange(n)`` on the copy.
    """
    a = list(seq)
    fn = _gen_copy(rng)
    for i in range(1, len(a)):
        j = fn.inrange(i + 1)
        if i != j:
            a[i], a[j] = a[j], a[i]
    return a


def _generate(fac, rng, idx, de, r0vec, d_factor, cr_factor, tunables, dims):
    """``PDE::GenerateMove::run`` @0x3864dc for fork ``idx``.

    Returns ``(trial_design, norm2)``; ``norm2`` feeds ``totalDistance``.
    """
    n_pop = len(de)
    n = fac.nDims()
    i2, i3 = _pick_parents(rng, idx, n_pop, r0vec)
    r0 = [float(de[i2].ix[d]) - float(de[i3].ix[d]) for d in range(n)]   # 0x3866c8
    norm2 = 0.0
    for d in range(n):
        norm2 += r0[d] * r0[d]                                            # 0x3867af
    if DEGENERATE_NORM2 > norm2:                        # `0.1 <= norm2` skips this
        if rng.in01() > ROUNDING:                       # @0x3868c3 (comisd 0.5)
            r0[rng.inrange(n)] = ONE                    # @0x386932
        else:
            r0[rng.inrange(n)] = MINUS_ONE              # @0x3869a0
        norm2 = ONE
    length = math.sqrt(norm2)                           # @0x3869c2
    if length != 0.0:
        unit = [r0[d] / length for d in range(n)]       # @0x386a2f
    else:
        unit = [0.0] * n

    # @0x386ae3..0x386aed: the walk starts from ``de[i1]`` -- the same first parent
    # the draws exclude -- not from ``de[idx]`` (i1 = r0vec[idx], see _pick_parents).
    origin = list(de[r0vec[idx]].ix)
    prev = None
    cands = []
    k = 0
    while True:                                         # `for (k = 0; ; ++k)`
        # The loop head 0x386acb..0x386b0e re-copies ``de[i1]->values`` into
        # [rbp-0x140] **every** iteration (the vector is destroyed at 0x386e0e and
        # copy-constructed again at 0x386b0e), so each k is evaluated against the
        # *original* parent; the truncation at 0x386ba4 does not accumulate.
        step = [k * ONE * unit[d] for d in range(n)]    # scale == 1.0 @0x386ab4
        trial = [int(math.trunc(origin[d] + step[d] + ROUNDING)) for d in range(n)]
        ok = True
        for d in range(n):
            if r0[d] > 0.0 and step[d] >= r0[d]:        # @0x386be5
                ok = False
                break
            if r0[d] < 0.0 and r0[d] >= step[d]:        # @0x386c8a
                ok = False
                break
            if trial[d] >= tunables[d] or trial[d] < 0:  # @0x386d40
                ok = False
                break
        if not ok:                                      # first infeasible k ends it
            break
        if k == 0 or trial != prev:                      # @0x386da4
            cands.append(list(trial))
            prev = list(trial)
        k += 1

    u = rng.in01()                                      # @0x386e77
    n_cand = len(cands)
    if n_cand != 0:
        ratio = d_factor
        n_sel = int(n_cand * (ratio + (ONE - ratio) * u))   # @0x386f17
        if n_sel >= n_cand:
            n_sel = n_cand - 1
    else:
        n_sel = 0
    design = list(cands[n_sel]) if cands else list(de[idx].ix)

    dims = dims if len(dims) == n else list(range(n))
    for j in range(len(dims)):                          # @0x38709b
        if rng.in01() > cr_factor:                      # @0x387113 → revert
            d = dims[j]
            design[d] = de[idx].ix[d]                   # @0x3871d2
    return design, norm2


def _driver_start(domain, start):
    """The point ``FunctionOptimizer::run`` evaluates before the state machine.

    ``FunctionOptimizer::run`` creates a design point at 0x3ce89e, copies the
    ``refPoint`` into it (0x3ce8c4..0x3ce91b) and drains the queue at 0x3cead3 --
    the algorithm's first ``state->run()`` at 0x3cea1b happens with that point
    already in the queue, so it is evaluated *first*.  ``./native/harness PDE 2 21
    300 0 10 0 0`` therefore prints ev0 ``[0,0]`` before ``PDE::Start::run``
    (0x384ab4) has created anything; ev1 is the mid point of 0x384c9f.
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
    """PDE as an ask/tell generator: one generation = ``populationSize`` trials."""
    P = dict(params or {})
    check_domain(domain)
    d_factor = float(P.get("dFactor", DEFAULT_DFACTOR))
    cr_factor = float(P.get("crFactor", DEFAULT_CRFACTOR))
    stopping = float(P.get("stoppingRatio", DEFAULT_STOPPING_RATIO))
    population_size = int(P.get("populationSize", 0))
    hammersley_n = int(P.get("hammersley_points", -1))
    seed = int(P.get("seed", 1))
    max_generations = int(P.get("max_generations", 0))   # harness guard (not in .so)
    archive_size = int(P.get("population_size", POPULATION_ARCHIVE))

    fac = FreshFactory(domain)
    n = fac.nDims()
    if population_size == 0:                            # @0x384b29..0x384b59
        population_size = POP_LARGE if n > POP_LARGE_DIMS else POP_SMALL
    if population_size <= 0:
        raise ValueError("assert(*populationSize>0) @0x3863de")

    rng = P.get("rng") or Gen(seed)                     # Data+0xd8 (key "seed")
    pop = P.get("population") or Population(archive_size)

    # ---- driver call #0 (FunctionOptimizer::run 0x3cead3, before 0x3cea1b) --
    yield [domain.from_index(_driver_start(domain, start))]

    # ---- Start @0x384ab4: mid point, then the OFAT screen ------------------
    # 0x384cd0..0x384d4b: for j < nDims  values[j] = (int)floor(0.5 * tunables[j])
    # (0.5 @0x570cf0, mul at 0x384d12, floor at 0x384d16, store at 0x384d3e).
    mid = [int(math.floor(0.5 * fac.tunables[d])) for d in range(n)]
    # 0x384e5e..0x384f2b: design_steps[j] = (floor(0.25*N), floor(0.25*N))
    # (0.25 @0x570cf8) -> Param operator=<vector<pair<int,int>>> @0x384f82 and off
    # to SensitivityOFAT (Call<SensitivityOFAT::Init> @0x384fd5).
    design_steps = [int(math.floor(OFAT_STEP_FRACTION * fac.tunables[d]))
                    for d in range(n)]
    centre = fac.create(mid)                            # 0x384c9f (vptr +0x10)
    batch = [centre]
    probe = {}
    # SensitivityOFAT's 3-level path (SensitivityOFAT.py, 0x3b6e4c): the Split fork
    # recurses on child 0 *first*, so the deepest tunable is swept first --
    # `for t in range(end-1, begin-1, -1)`; per tunable k=1 is origin-steps[t].first
    # (0x3b7638) and k=2 is origin+steps[t].second (0x3b7692), both relative to the
    # untouched origin, and out-of-range levels are *skipped* (0x3b7706..0x3b778c).
    for d in range(n - 1, -1, -1):
        pts = []
        for lvl in (mid[d] - design_steps[d], mid[d] + design_steps[d]):
            if lvl == mid[d] or not (0 <= lvl < fac.tunables[d]):
                continue
            pt = list(mid)
            pt[d] = lvl
            pts.append(fac.create(pt))
        probe[d] = pts
        batch.extend(pts)
    if batch:                                           # FirstOFAT @0x3852dc
        costs = yield [p.ix for p in batch]
        for p, c in zip(batch, costs):
            p.cost = float(c)

    # ---- FirstOFAT @0x3852dc: factor ranking ------------------------------
    # The screening population (0x38531d `this+0x148`, the same `std::set` ordered
    # by DesignPoint::operator<, i.e. by cost) is *scanned*; the points that reach it
    # are the ones the `evil_ptr_less` tree accepted, in evaluation order (mid point
    # first, then the ±step probes of tunable n-1 down to 0).  Two things follow and
    # both are measured, not assumed:
    #
    #   * a probe the tree rejects never takes part (dim=4 levels=17 target
    #     12,11,10,12 drops BOTH d0 probes -- the archived set is 7 of 9 points, see
    #     native/variants/pde_dimorder.gdb), and
    #   * a dimension with no surviving probe keeps the map's initial 0.0
    #     (0x385578), which is what makes it rank last.
    #
    # The key itself: every point whose coordinate differs from floor(0.5*N)
    # (0x385455 `cvtsi2sd/mulsd 0.5/floor/cmp/setne`) assigns
    #
    #     rank[dim] = fabs(cost(point) - base)      0x3855c3 `subsd`, 0x3855d5 `andpd`
    #
    # into a `map<int,double>` initialised to 0.0 -- an *assignment* (0x385614 store),
    # so the last writer wins, and the scan is the tree's in-order = ascending cost, so
    # the survivor that ends up being recorded for a dimension is its most expensive
    # point.  `base` is the cost of the point that differs in *no* coordinate (the mid
    # point; `flag` at 0x385462 only clears when some coordinate differs, and the
    # 0.0 that -0x18(%rbp) is seeded with at 0x385309 survives when there is none).
    # 0x38562c then copies the map into a multimap<double,int> and walks it with
    # rbegin() (0x38570a): keys descending, *equal* keys in reverse dimension order.
    screen = Population(archive_size)
    for p in batch:                                     # batch = mid + probes, eval order
        screen.insert(p)
    base = 0.0                                          # 0x385309 `movsd 0`
    for p in screen.as_list():
        if all(p.ix[j] == mid[j] for j in range(n)):    # 0x385462 `flag` stays true
            base = p.cost                               # 0x38551f store
    rank = [0.0] * n                                    # 0x385578 map[i] = 0.0
    for p in screen.as_list():                          # in-order = ascending cost
        for j in range(n):
            if p.ix[j] != mid[j]:                       # 0x385455
                rank[j] = abs(p.cost - base)            # 0x3855c8/0x3855d5/0x385614
    # multimap<double,int> keeps insertion order among equal keys and the walk is
    # rbegin()..rend() (0x38570a), so ties come back in *reverse* dimension order:
    # plain ascending sort (stable -> j ascending) reversed.
    dim_order = [d for _, d in reversed(sorted(((rank[j], j) for j in range(n)),
                                               key=lambda kv: kv[0]))]

    # ---- StartHammersley @0x3858de / HammersleyDone @0x385e6e --------------
    n_points = hammersley_n if hammersley_n > 0 else population_size
    unit = hammersley_points(n_points, n)
    de_submit = [fac.create(hammersley_design(fac, dim_order, u)) for u in unit]
    batch = [p for p in de_submit if not p.isEvaluated()]
    if batch:
        costs = yield [p.ix for p in batch]
        for p, c in zip(batch, costs):
            p.cost = float(c)
    for p in de_submit:
        pop.insert(p)
    # ---- HammersleyDone @0x385f8a..0x38609c: DE_population from the archive ----
    # `it = population->set().begin()`; for (i = 0; i < *populationSize; ++i) push
    # `*it` and ++it, and when `it == set.end()` push `*set.begin()` **without**
    # advancing (0x386044..0x386097).  So DE_population is the archive's in-order
    # content, right-padded with its cheapest point when the archive holds fewer
    # entries than populationSize (the tree drops points the non-strict-weak
    # comparator calls equivalent -- measured: 29 of the 30 Hammersley points here).
    de = pop.as_list()[:population_size]
    while len(de) < population_size:
        de.append(de[0])
    if len(de) != population_size:
        raise ValueError("assert(DE_population->size() == *populationSize) @0x3860fd")

    r0vec = [population_size - k - 1 for k in range(population_size)]
    # The binomial "revert to parent" loop walks ``state+0x60`` (dims), and that is
    # the *OFAT-ranked* dimOrder, not 0..n-1: 0x38700f only fills it with the
    # identity when it is shorter than nDim.  Measured on this configuration
    # dimOrder = [1, 0] (the rank map's rbegin()), so the first ``in01`` of the pair
    # decides dim 1.
    dims = list(dim_order)
    total_distance = 0.0
    generation = 0

    # ---- HammersleyDone @0x386102: one ForkedGen child per DE individual ----
    # ForkedGen(*state->rng /*+0x118*/, (unsigned long)*populationSize /*+0xe8*/)
    # at 0x386143, then `for (i = 0; i < *populationSize; ++i)` (0x38614f..0x3861b0)
    # pushes fg.next() (0x38618c) into this+0x48, the per-child Gen vector that
    # GenerateMove::run indexes by its fork index at 0x386552/0x3865e1.
    _fg = ForkedGen(rng, population_size)
    rngs = [_fg.next() for _ in range(population_size)]
    # r0vec (this+0x128): 0x3861ba..0x386235 pushes *populationSize - k - 1, i.e.
    # the mapping is REVERSED (individual k inherits parent slot popSize-1-k).
    # `*totalDistance` (+0x158) is cleared at 0x38625c right before DESplit.

    # ---- DESplit / GenerateMove / DEWait / DEJoin -------------------------
    while True:
        if max_generations and generation >= max_generations:
            return list(pop.top().ix)                   # harness guard only
        generation += 1
        # ---- GenerateMove fork, one trial per individual -------------------
        trials = []
        for idx in range(population_size):
            design, norm2 = _generate(fac, rngs[idx], idx, de, r0vec,
                                      d_factor, cr_factor, fac.tunables, dims)
            total_distance += norm2                    # 0x386a7e
            trials.append(fac.create(design))
        batch = [t for t in trials if not t.isEvaluated()]
        if batch:
            costs = yield [t.ix for t in batch]
            for t, c in zip(batch, costs):
                t.cost = float(c)
        # ---- DEWait @0x3873a8 ---------------------------------------------
        for idx, t in enumerate(trials):
            pop.insert(t)                               # @0x38741c
            if de[idx].cost > t.cost:                   # @0x38755e
                de[idx] = t                             # @0x3875a0
        # ---- DEJoin @0x387694 ---------------------------------------------
        best = pop.top().cost
        total = 0.0
        for p in de:
            total += p.cost
        mean = (total / float(len(de))) if de else 0.0
        rel = _ieee_div(mean - best, best)      # 0x3878d0..0x3878df divides unconditionally
        if stopping > rel:                              # @0x387900
            return list(pop.top().ix)
        if STOP_DISTANCE_FACTOR * population_size > total_distance:
            return list(pop.top().ix)                   # @0x3879a8
        total_distance = 0.0                            # @0x3879f4
        r0vec = _random_shuffle(rng, r0vec)             # @0x387a72 (RandomFn copy)
        # -> DESplit


PARAMS = {"seed": 31, "populationSize": 30, "max_generations": 40}



# =============================================================================
#  Self-test: a 2-D quadratic on a 41 x 41 integer grid
# =============================================================================
class Quadratic2D:
    """``f(x, y) = (x-1.5)^2 + (y+4.0)^2`` on 41 levels per dimension.

    The minimum is exactly on a grid point (index 23 / 12) with cost 0.0, so a
    working optimiser has to arrive at ``[23, 12]``.
    """

    NAME = "2-D quadratic, 41 levels"

    def __init__(self, levels=41, lo=-10.0, hi=10.0):
        self.levels = levels
        self.tunables = [levels, levels]
        self.lo = [lo, lo]
        self.step = [(hi - lo) / float(levels - 1)] * 2
        self.target = (1.5, -4.0)

    # ---- the five hooks the generator needs -----------------------------
    def from_index(self, ix):
        return tuple(self.lo[d] + self.step[d] * ix[d] for d in range(2))

    def to_index(self, point):
        return [int(round((point[d] - self.lo[d]) / self.step[d])) for d in range(2)]

    def center_index(self):
        return [self.levels // 2, self.levels // 2]

    def clamp_index(self, ix):
        return [min(max(int(v), 0), self.levels - 1) for v in ix]

    # ---- the objective ---------------------------------------------------
    def cost(self, point):
        x, y = point
        return (x - self.target[0]) ** 2 + (y - self.target[1]) ** 2

    def evaluate_index(self, ix):
        return self.cost(self.from_index(ix))

    def all_specs_good_enough(self, ix, c):
        return c < 0.25


def _report(dom, best, nfev):
    x, y = dom.from_index(best)
    print("point = %r  ->  (x, y) = (%.3f, %.3f)" % (best, x, y))
    print("cost  = %.6f" % dom.evaluate_index(best))
    print("nfev  = %d" % nfev)


if __name__ == "__main__":
    dom = Quadratic2D()
    best, nfev = drive(run(dom, None, params=PARAMS), dom.evaluate_index)
    _report(dom, best, nfev)
