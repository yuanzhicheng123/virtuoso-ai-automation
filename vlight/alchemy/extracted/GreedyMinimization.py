"""GreedyMinimization — extracted from libalchemy.so (Cadence Alchemy, `algs/GreedyMinimization.cpp`).

visible_name : "Greedy Minimization"        (registration table, spec_glue.md §…)
states       : Init 0x3291f6 -> Start 0x32938c -> Start_WaitForStartingPoint 0x329562
               -> Start_HaveStartingPoint 0x329684 -> PointWait 0x329a66 -> Start
hyperparams  : id? no; ``max_steps`` (0 = unbounded), ``best_point``,
               ``move_class`` (null -> Hustin), ``schedule_point`` = 0.5,
               ``rng`` (+ ``seed`` = 1).  Internal: TerminationLimit = 100.

The body is a stochastic hill climb on the integer lattice: clone ``best_point``,
perturb exactly one level via MoveRange::selectMove, accept unless it is worse,
and stop after 100 consecutive non-improving moves (reset on improvement) or
when ``max_steps`` runs out.

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
        """0x3cd697 — boost::random::uniform_01<mt19937,double>().

        53 significant bits built from two words (the two extra mt19937 calls
        the spec notes at 0x3cd6b3/0x3cd6c6).  [unproven]: the exact bit
        assembly is inferred from generate_canonical<double,53>.
        """
        a = self.draw() >> 5          # 27 bits
        b = self.draw() >> 6          # 26 bits
        return (a * 67108864.0 + b) / 9007199254740992.0

    def inrange(self, n):
        """0x3cd602 Gen::inrange(int) — boost::uniform_int_1_36_0<int>(0, n-1).

        The 1.36 shim is a *dispatching* wrapper (0x3cdaba):

            0x3cdaf4  range = n-1 ; if (range == 0) return 0        (no engine call!)
            0x3cdc9d  if (0xFFFFFFFF / range > 4) -> uniform_smallint_1_41 (0x3cdccd)

        and `0xFFFFFFFF / (n-1) > 4` holds for every n < 858993460, i.e. for every
        range this build ever asks for, so the answer comes from
        ``boost::uniform_smallint_1_41<int>`` (0x3ce4b8):

            r      = max - min + 1                                 0x3ce4c9
            b      = eng.max() - eng.min()            = 0xFFFFFFFF  0x3ce4e7
            bucket = 1
            if (b == UINT_MAX) { bucket = 2; b >>= 1; }            0x3ce4fa..0x3ce515
            b += 1                                    = 0x80000000  0x3ce518
            if (b % r == 0) bucket = b / r;                        0x3ce527..0x3ce538
            else while ((b / r) >> 5 >= r) { b >>= 1; bucket <<= 1; }
            return ((eng() - min) / bucket) % r + min              0x3ce574..0x3ce58a

        This is *not* the libstdc++ ``v % n`` reduction the previous revision
        assumed: for n = 2 it is ``(draw >> 30) & 1``, i.e. the top bits.  The old
        reduction reproduced nothing once the live GreedyMinimization loop became
        reachable (it is the only caller), see PARITY.md §12.3.
        """
        if n <= 0:
            raise ValueError("inrange(%d): empty range" % n)
        if n == 1:
            return 0                       # 0x3cdaf4: range == 0 -> min, no draw
        r = n
        b = 0xFFFFFFFF                     # mt19937: max() - min()
        bucket = 1
        if b == 0xFFFFFFFF:                # always true for mt19937
            bucket = 2
            b >>= 1                        # 0x7FFFFFFF
        b += 1                             # 0x80000000
        if b % r == 0:
            bucket = b // r
        else:
            while (b // r) >> 5 >= r:      # 0x3ce53d
                b >>= 1
                bucket = (bucket << 1) & 0xFFFFFFFF
        return (self.draw() // bucket) % r


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
        # selection_ (+0x78) is `classes_per_dim * n_dim` entries, not
        # `num_classes * n_dim`: generateNewMoveClass (0x3c8d4c/0x3c8d99) asks
        # `rng.inrange(selection_.size())` and gdb on `GM 2 3 ...` shows
        # `inrange n=8 -> 6` (= 4*2), while dim 1 shows `inrange n=4`.
        self.sel = [i % n_dim for i in range(classes_per_dim * n_dim)]   # +0x78
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
    """``FunctionOptimizer::DesignPointFactoryImpl::create`` @0x3cef6e / 0x3cf016.

    Always allocates a **new** DesignPoint and never consults the ``Cache``
    (``Cache::hit`` @0x3d7976 has no caller inside libalchemy.so, PARITY.md §4.2),
    so the same design is evaluated again every time it is asked for.  What this
    module used to model was the cache, which made repeated designs free and hid
    the driver's unconditional re-evaluation.
    """

    def create(self, ix=None):
        if ix is None:
            ix = list(self.domain.center_index())
        return Pt([int(v) for v in ix])


class Population:
    """Alchemy::Population<T, evil_ptr_less> (helpers/Population.h).

    Bounded, cost-ordered archive; ``insertAndDiscardExtras`` @0x2efdf0 keeps
    the ``maxsize`` cheapest points, ``top()`` @0x2efeea is the cheapest.
    """

    def __init__(self, maxsize):
        self.maxsize = int(maxsize)
        self.pts = []

    def insert(self, pt):
        if pt.cost is None:
            return
        self.pts.append(pt)
        self.pts.sort(key=lambda p: p.cost)
        del self.pts[self.maxsize:]

    def top(self):
        return self.pts[0] if self.pts else None

    def worst(self):
        return self.pts[-1] if self.pts else None

    def __len__(self):
        return len(self.pts)

    def as_list(self):
        return list(self.pts)


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
#  GreedyMinimization
# =============================================================================
TERMINATION_LIMIT = 100          # GreedyMinimization::TerminationLimit @0x566ef8
SCHEDULE_POINT = 0.5             # key "schedule_point" default, raw 0.5 @0x567210
DEFAULT_MAX_STEPS = 0            # key "max_steps"      default, `mov DWORD [rax],0` @0x329ebf
DEFAULT_SEED = 1                 # key "seed"           default
POP_CAP = 1                      # "population"/"psaPopulation" = Population(1)
BEST_POINT_FROM_DRIVER = "current_point"   # params["best_point"] sentinel: alias the
                                           # driver's point (see run() below)


def run(domain, goal, start=None, params=None):
    """GreedyMinimization as an ask/tell generator.

    Yields a batch of index lists (always exactly one point), receives the
    matching cost list through ``send()``, and returns the best index list.

    ``domain`` must provide ``tunables``, ``from_index``, ``to_index``,
    ``center_index``, ``clamp_index``.  ``goal`` is accepted for driver
    compatibility; the engine never looks at it on this path (``Δ = cost_new -
    cost_old`` with no sign flip, spec_anneal.md §3).
    """
    P = dict(params or {})
    check_domain(domain)
    seed = int(P.get("seed", DEFAULT_SEED))
    max_steps = int(P.get("max_steps", DEFAULT_MAX_STEPS))     # 0 => unbounded
    schedule_point = float(P.get("schedule_point", SCHEDULE_POINT))
    termination_limit = int(P.get("termination_limit", TERMINATION_LIMIT))

    fac = FreshFactory(domain)
    rng = P.get("rng") or Gen(seed)

    # ---- Data::Data @0x329db8: the `best_point` member (Data+0x28) ----------
    # `Params["best_point"]` is probed at 0x32a071 (`boost::any::empty` @0x32a079);
    # when the key is empty the ctor allocates a **default constructed** DesignPoint
    # (operator new 0xf0 @0x32a0ab + DesignPoint::DesignPoint() @0x32a0bd) and writes
    # it back into Params (0x32a14d), then any_cast<>s it into Data+0x28 (0x32a1e3).
    # `FunctionOptimizer::run` parks the driver's own start point under the key
    # `"current_point"` (string @0x5781a4, `Params::operator[]` @0x3ce958 + the
    # `Param::operator=<DesignPoint>` write @0x3ce96d) -- and this algorithm never
    # looks at that key: `Data::Data` probes `"best_point"` (string @0x566f06,
    # 0x32a071) instead.  The state's best_point is therefore a *different, empty*
    # object than the point the driver evaluates, and its `isEvaluated()`
    # (boost::optional<double> at +0x8, @0x3d1728) never becomes true.
    best = P.get("best_point")
    # `best_point = BEST_POINT_FROM_DRIVER` models the harness/`Params` shape where
    # the *same* `shared_ptr<DesignPoint>` is stored under both "best_point" and
    # "current_point": the object is unevaluated when Data::Data caches it, but the
    # driver's own evaluation of the queued refPoint (`setCost` 0x3ceaff) makes
    # `isEvaluated()` true in time for the *second* test, at
    # Start_WaitForStartingPoint::run 0x329586 (`xor/test` 0x32958b..0x329590), which
    # then branches to Start_HaveStartingPoint 0x3295af instead of returning itself
    # (0x3295a5).  That is how the live loop below becomes reachable at all: the
    # object only has to *become* evaluated, it does not have to be pre-evaluated.
    _alias_best = isinstance(best, str) and best == BEST_POINT_FROM_DRIVER
    if best is None or _alias_best:
        best = Pt([], None)                     # default DesignPoint(): no values, no cost

    # ---- Start @0x32938c: best_point->isEvaluated() @0x3293a1 ---------------
    # False => the state hands control to Start_WaitForStartingPoint (branch to
    # 0x329443).  The driver nevertheless runs its own queued start point first
    # (cost callback 0x3cead3 -> DesignPoint::setCost 0x3ceaff), so exactly one
    # evaluation happens no matter what.
    cur = fac.create(start)                     # the driver's point, not the state's
    if not cur.isEvaluated():
        costs = yield [cur.ix]
        cur.cost = float(costs[0])

    if _alias_best:                             # same object as Params["current_point"]
        best = cur

    if not best.isEvaluated():
        # ---- Start_WaitForStartingPoint::run @0x329562 ----------------------
        # `if (!best_point->isEvaluated()) return this;` -- the wait state returns
        # itself for ever (0x329586 xor/test @0x32958b..0x329590, `return this`
        # @0x3295a5), it never reaches Start_HaveStartingPoint (0x3295af branch is
        # dead because isEvaluated() is permanently false).  `FunctionOptimizer::run`
        # leaves its loop as soon as the point queue is empty
        # (isPointQueueEmpty @0x3cea66, `je` @0x3cea70): the run is over after that
        # single evaluation and no move is ever submitted.  That is the copied bug,
        # reproduced with its address -- not a special case for this configuration.
        return list(cur.ix)

    # ---- Start_HaveStartingPoint @0x329684 ---------------------------------
    move_class = P.get("move_class")            # shared_ptr<shared_ptr<Iface>> handle
    if move_class is None:                      # @0x3296ba
        # Hustin(Hustin::Parameters(), nTunables, ForkedGen(*rng,1).next(),
        #        best_point->cost())
        move_class = Hustin(fac.nDims(), ForkedGen(rng, 1).next(), best.cost)

    # The loop below is unreachable through ./native/harness (it would need an
    # already-evaluated `best_point` in Params, and neither harness nor pydrive can
    # inject a shared_ptr<DesignPoint>); it is kept as the disassembly's live path.
    stall = termination_limit                  # state+0x40 = 100
    steps = max_steps
    while True:
        # ---- Start_HaveStartingPoint (again): live path --------------------
        # next = POINTS->create(best_point->design)          (clone the best)
        trial = list(best.ix)
        d = move_class.getNextClass()                      # vtable+0x18, no dimOrder
        trial[d] = select_move(fac.levels(d), trial[d], schedule_point, rng)
        cand = fac.create(trial)                           # 0x3cf016: always a NEW point
        if not cand.isEvaluated():
            costs = yield [cand.ix]
            cand.cost = float(costs[0])
        # ---- PointWait @0x329a66 -------------------------------------------
        if best.cost > cand.cost:               # @0x329ae8  strict improvement
            stall = termination_limit           # @0x329af8  reset the stall counter
        if not (cand.cost > best.cost):         # @0x329b39  accept when not worse
            best = cand                         # @0x329b5d
        if stall == 0:                          # `if (stall-- == 0)` @0x329b66
            return list(best.ix)                # __cxa_throw(Return) @0x329b9b
        stall -= 1
        if steps != 0:                          # @0x329ba0
            steps -= 1
            if steps == 0:
                return list(best.ix)            # __cxa_throw(Return) @0x329c02
        # -> Start (draw the next trial, @0x329c37)


PARAMS = {"seed": 7}



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
    print("note  = the library stops here: Data::Data @0x329db8 builds `best_point` "
          "from Params[\"best_point\"] (0x566f06, probe 0x32a071), but "
          "FunctionOptimizer::run parks the start point under \"current_point\" "
          "(0x5781a4, write 0x3ce96d); Start_WaitForStartingPoint (0x329586) therefore "
          "waits for ever and the driver stops as soon as its queue is empty.")
