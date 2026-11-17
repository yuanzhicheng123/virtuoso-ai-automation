"""Anneal — extracted from libalchemy.so (Cadence Alchemy, `algs/Anneal.cpp`).

visible_name : "Simulated Annealing"  (.rodata 0x562c7f; shared table copy 0x579201)
states       : Init 0x2f7500 -> Start 0x2f7696
               Start -> Start_HaveStartingPoint 0x2f7992   when current_point is evaluated
               Start -> Start_WaitForStartingPoint 0x2f786e otherwise (polls)
               Start_HaveStartingPoint -> PointSubmit 0x2f7fa4
               PointSubmit -> PointDone 0x2f8262
               PointDone -> PointSubmit          (next candidate)
               PointDone -> Return               (max_steps hit 0, @0x2f842d)
hyperparams  : ``id``, ``max_steps`` (default 0 = infinite), ``current_point``,
               ``population``/``psaPopulation`` = Population(1), ``seed`` = 1,
               ``rng`` = Gen(seed), ``move_class`` (lazy Hustin),
               ``initial_temperature`` = 1.0, ``dimOrder`` (lazy identity),
               ``criterion`` (lazy KrazKool1).

Single chain, one point in flight.  The move is: pick a variable with Hustin,
pick a new level with MoveRange::selectMove(N, c, criterion->time(), rng),
decide with the Metropolis test in KrazKool1, and archive the candidate in both
populations (capacity 1 each, so ``population`` degenerates to "the best so far").

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


class CoolingScheduleAbort(Exception):
    """The real library's *own* assert, reached -- not a convergence test.

    ``Alchemy::CoolingSchedule::KrazKool1::decrementTemperature()`` computes
    ``devCostPercent = deviation() / bestCost`` (0x3c7616..0x3c7628) and then

        comisd xmm0(0.0),[rbp-0x18] ; jb 0x3c7649      0x3c7635 / 0x3c763a
        movsd  xmm0,[0x5778c8]      ; 0.01            0x3c763c (finite <= 0 only)
        comisd xmm0,0.0 ; ja 0x3c7677                  0x3c7652 / 0x3c7656
        __assert_fail("devCostPercent > 0.0",...,328)  0x3c7658..0x3c7672

    ``comisd`` raises CF for NaN, so a NaN percentage skips the 0.01 fallback and
    trips the assert, which aborts the whole process.  On a landscape whose optimum
    is exactly 0 (``--target 10`` / quad, cost 0 reached) ``bestCost`` is 0 and
    ``deviation()`` is 0 => 0/0 = NaN: the run dies right there.  That is why the
    native trace of ``Anneal`` stops at 201 evaluations instead of running to the
    budget; ``run()`` raises this exception at the same evaluation so the point
    sequence stays comparable.  See PARITY.md §11.
    """


def _ieee_div(a, b):
    """``a / b`` with IEEE-754 semantics (Python raises where C++ gives inf/NaN)."""
    if b != 0.0:
        return a / b
    if a == 0.0 or a != a:
        return float("nan")
    return math.copysign(float("inf"), a)


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

        Two separate things happen inside the 0x9f0-byte frame:

        1. 0x3cd688 constructs ``boost::random::uniform_01<mt19937,double>`` from
           the engine **by value** — the distribution embeds a *second* mt19937 at
           +0x8 (that is why the frame is 0x9f0 bytes) — and 0x3cd697 evaluates
           ``backward_compatible_uniform_01::operator()`` @0x3cd878 on that copy:
           ``(eng() - eng.min()) * factor`` with ``factor = 1.0/(max-min+1) =
           2**-32`` (ctor @0x3cdd20: ``divsd`` of the 1.0 constant @0x578150), with
           a ``>= 1.0`` retry that can never fire because the largest value is
           ``(2**32-1)*2**-32 < 1``.  So the returned double is the engine's *next*
           word / 2**32 — computed **without** consuming it.
        2. 0x3cd6b3 and 0x3cd6c6 then call ``eng()`` twice on the *real* engine and
           throw both words away.

        Measured on the KrazKool stream (``seed=3``): the first ``in01()`` returns
        0.55079790437594056 == 2365658986/2**32 and the engine index goes 624 -> 2;
        the next one returns 0.70814782218076289 == word #2, i.e. words 1 and 2 were
        discarded.  Both "one word" and "two words for generate_canonical<double,53>"
        are wrong: either one misaligns every later draw of this generator (and the
        Metropolis decisions with it).
        """
        saved_mti, saved_mt = self.mti, self.mt[:]
        u = self.draw() / 4294967296.0    # the value comes from the engine copy
        self.mti, self.mt = saved_mti, saved_mt   # ... which consumed nothing real
        self.draw()                       # discarded @0x3cd6b3
        self.draw()                       # discarded @0x3cd6c6
        return u

    def inrange(self, n):
        """0x3cd623 — ``boost::uniform_int_1_36_0<int>(0, n-1)`` -> ``uniform_smallint_1_41``.

        ``uniform_int_1_36_0::operator()`` (@0x3cdaba) hands every range we use to
        ``boost::uniform_smallint_1_41<int>::operator()`` (@0x3ce4b8), which does
        NOT take ``draw % n``.  Verbatim from the disassembly:

            brange = eng.max() - eng.min()        # 0x3ce4f7
            mult   = 1
            if brange == UINT_MAX:                # 0x3ce4fa
                mult = 2; brange >>= 1            # 0x3ce509 / 0x3ce515
            brange += 1                           # 0x3ce518
            if brange % n == 0:                   # 0x3ce524
                mult = brange // n                # 0x3ce535
            else:
                while n <= (brange // n) >> 5:    # 0x3ce548
                    brange >>= 1; mult <<= 1      # 0x3ce553 / 0x3ce558
            return ((eng() - eng.min()) // mult) % n + min_value   # 0x3ce579 / 0x3ce581

        Exactly one word is consumed for every n >= 2 (no rejection loop), so the
        stream position is unchanged; only the value differs from ``draw % n``.
        Verified against the real Gen (seed 1) for n = 2..24: 23/23 identical.
        """
        if n <= 0:
            raise ValueError("inrange(%d): empty range" % n)
        if n == 1:
            return 0
        mult = 1
        brange = 0xFFFFFFFF
        if brange == 0xFFFFFFFF:
            mult = 2
            brange >>= 1
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


class HustinPriorityQueue:
    """``std::priority_queue<RecalcEntry, vector<RecalcEntry>, less<RecalcEntry>>``.

    Only the **value** (+0x8) is compared: ``RecalcEntry::operator<`` @0x3c8070 is
    ``comisd other->value, this->value; seta`` = ``this->value < other->value``.
    The dimension index (+0x0) never breaks a tie, so the emitted table depends on
    the exact heap layout — the libstdc++ algorithm has to be reproduced, not a
    re-sort (a stable/unstable sort gives a different table whenever two smoothed
    quotas tie, which is the *normal* case: every dimension starts at 4.0).

    ``push`` is ``std::__push_heap`` and ``pop`` is ``std::__pop_heap`` ->
    ``std::__adjust_heap`` (bits/stl_heap.h), transcribed verbatim.
    """

    __slots__ = ("a",)

    def __init__(self):
        self.a = []                       # list of (dim, value)

    @staticmethod
    def _lt(x, y):                        # less<RecalcEntry>: compare +0x8 only
        return x[1] < y[1]

    def empty(self):
        return not self.a

    def push(self, item):
        a = self.a
        x, v = item
        hole = len(a)
        a.append(item)
        parent = (hole - 1) // 2
        while hole > 0 and self._lt(a[parent], item):
            a[hole] = a[parent]
            hole = parent
            parent = (hole - 1) // 2
        a[hole] = (x, v)

    def pop(self):
        """__pop_heap: save the root, move the last element up, sift it down."""
        a = self.a
        n = len(a)
        top = a[0]
        value = a[n - 1]
        length = n - 1                        # __len handed to __adjust_heap
        hole = 0
        second = 0
        while second < (length - 1) // 2:
            second = 2 * (second + 1)
            if self._lt(a[second], a[second - 1]):
                second -= 1
            a[hole] = a[second]
            hole = second
        if (length & 1) == 0 and second == (length - 2) // 2:
            second = 2 * (second + 1)
            a[hole] = a[second - 1]
            hole = second - 1
        parent = (hole - 1) // 2              # __push_heap(first, hole, 0, value)
        while hole > 0 and self._lt(a[parent], value):
            a[hole] = a[parent]
            hole = parent
            parent = (hole - 1) // 2
        a[hole] = value
        del a[length:]
        return top


class Hustin:
    """Alchemy::MoveClass::Hustin, ctor @0x3c80c2 (0xd8 bytes).

    Decides WHICH variable to perturb by drawing uniformly from an explicit
    ``selection_`` table (+0x78) — **not** by ``i % nDim``.  The table is

    * sized ``classesPerDim * nDim`` (ctor 0x3c81f1 ``imul eax,[rbp-0x54]`` with
      ``eax = Parameters+0x04 = 4``), i.e. 4*nDim slots — 8 for dim 2;
    * filled ``selection_[i] = i % nDim`` at construction (0x3c82fb ``div rcx``);
    * rebuilt from the per-dimension success weights by ``recalculateWeights``
      (0x3c842e) every ``numClasses`` (=100) *moved* evaluations (``lifetime``
      +0x38, decremented in ``update`` 0x3c8cf3, reset to 100 at 0x3c8bce).

    Layout: +0x38 lifetime, +0x40 w[nDim], +0x60 succ[nDim], +0x78 selection_,
    +0x90 quota[nDim], +0xa8 smooth[nDim], +0xc8 cur, +0xd0 lastCost.
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
        # ctor 0x3c81ea..0x3c8204: vector<unsigned>(classesPerDim * nDim), then
        # 0x3c82ce..0x3c8321: selection_[i] = i % nDim.
        self.sel = [i % n_dim for i in range(classes_per_dim * n_dim)]   # +0x78
        self.quota = [float(classes_per_dim)] * n_dim       # +0x90
        self.smooth = [float(classes_per_dim)] * n_dim      # +0xa8
        self.cur = 0                                        # +0xc8 @0x3c82ab
        self.last = cost                                    # +0xd0 @0x3c82be
        self.generate_new_move_class()                      # 0x3c832a

    # ---- 0x3c8d32 -------------------------------------------------------
    def generate_new_move_class(self):
        r = self.rng.inrange(len(self.sel))     # virtual inrange, arg = size()
        self.cur = self.sel[r]                  # @0x3c8dc4
        if self.sparse:
            k = (self.lifetime - 1) & MASK32    # unsigned k = lifetime - 1
            if k < self.n_dim and self.succ[k] == 0:
                self.cur = k                    # cover never-improving dims @0x3c8e1d

    # ---- 0x3c8e2e -------------------------------------------------------
    def getNextClass(self):
        return self.cur

    # ---- 0x3c8c3a -------------------------------------------------------
    def update(self, cost, accepted, moved):
        mag = abs(self.last - cost)             # andpd |x| @0x3c8c70
        if accepted and moved:
            self.w[self.cur] += mag             # @0x3c8ca8
            self.succ[self.cur] += 1            # @0x3c8cd2
        self.last = cost                        # @0x3c8ce0 (always)
        if not moved:                           # @0x3c8cec
            return
        self.lifetime -= 1                      # @0x3c8cfa
        if self.lifetime <= 0:                  # @0x3c8d0d setle
            self.recalculate_weights()          # @0x3c8d1b
        self.generate_new_move_class()          # @0x3c8d27

    # ---- 0x3c842e -------------------------------------------------------
    def recalculate_weights(self):
        n = self.n_dim
        # ---- Q = sum_d (succ[d] ? w[d]/succ[d] : 0)   0x3c8451..0x3c8500 ----
        Q = 0.0
        for d in range(n - 1, -1, -1):
            if self.succ[d]:
                Q += self.w[d] / float(self.succ[d])
        if Q > 0.0:                                     # comisd/jbe @0x3c850e
            # ---- proportional quotas, clamped to >= 1 slot, 0x3c8628..0x3c8800 ----
            remaining = len(self.sel)                   # size() @0x3c8526
            done = [False] * n                          # vector<bool> @0x3c855d
            changed = True
            while changed:                              # @0x3c85c3
                changed = False
                for d in range(n - 1, -1, -1):          # @0x3c85d5
                    if done[d]:                         # @0x3c8622
                        continue
                    if self.succ[d]:
                        # ((w/succ) / Q) * remaining — exact op order @0x3c869e..0x3c86dd
                        share = (self.w[d] / float(self.succ[d])) / Q * float(remaining)
                    else:
                        share = 0.0
                    if 1.0 > share:                     # comisd 1.0,share @0x3c86f4
                        share = 1.0                     # @0x3c8707
                        remaining -= 1                  # @0x3c870c
                        done[d] = True                  # @0x3c8723
                        if self.succ[d]:                # @0x3c8757
                            Q -= self.w[d] / float(self.succ[d])
                        changed = True                  # @0x3c87c9
                    self.quota[d] = share               # @0x3c8800
        else:
            for d in range(n - 1, -1, -1):              # 0x3c8820..0x3c887f
                self.quota[d] = float(self.cpd)         # 4.0
        # ---- std::swap(quota @0x90, smooth @0xa8) @0x3c88a2 -----------------
        self.quota, self.smooth = self.smooth, self.quota
        # ---- smooth = smooth + (quota - smooth)/smoothFactor ; zero the stats --
        for d in range(n - 1, -1, -1):                  # 0x3c88a7..0x3c89de
            self.smooth[d] = self.quota[d] + (self.smooth[d] - self.quota[d]) / self.smooth_factor
            self.w[d] = 0.0                             # @0x3c89b9
            self.succ[d] = 0                            # @0x3c89d8
        # ---- deal the 4*nDim slots out of a max-heap on the smoothed quota ----
        pq = HustinPriorityQueue()                      # @0x3c89ed
        for d in range(n - 1, -1, -1):                  # push order nDim-1 .. 0
            pq.push((d, self.smooth[d]))                # @0x3c8a43 / @0x3c8a59
        idx = 0                                         # +0x34 @0x3c8a60
        size = len(self.sel)
        while idx < size:                               # @0x3c8a67..0x3c8a85
            if pq.empty():                              # assert @0x3c8a9f
                raise AssertionError("!AllocPriorityQueue.empty() @0x3c8abb")
            dim, val = pq.pop()                         # @0x3c8af3
            k = int(val)                                # cvttsd2si @0x3c8afb
            if k == 0:
                k = 1                                   # @0x3c8b08
            else:
                val = val - float(k)                    # @0x3c8b1e
                if val > 0.0:                           # @0x3c8b3a
                    pq.push((dim, val))                 # @0x3c8b50
            while k > 0:                                # @0x3c8b55
                if idx >= size:
                    raise AssertionError("AllocIndex < selection_.size() @0x3c8ba0")
                self.sel[idx] = dim                     # @0x3c8bc6
                idx += 1
                k -= 1
        self.lifetime = self.ncls                       # @0x3c8be1 (back to 100)


# --------------------------------------------------------------------------
#  MoveRange::selectMove  (the only step-size generator in the family)
# --------------------------------------------------------------------------
MOVE_W0 = 0.4        # @0x577d30  0x3fd999999999999a
# @0x577d40 is 0xbfd6666666666667, i.e. -0.35000000000000003331 -- ONE ULP BELOW the
# literal -0.35 (0xbfd6666666666666).  This is load-bearing: with the literal,
# kmax at t = 0.9 becomes 9 instead of the library's 8 (measured: the library's
# selectMove gets nBelow=8/nAbove=8 at t=0.9, kmax=9 everywhere else), and
# (N-1)*(0.4-0.35*t)+0.5 lands exactly on 9.0 <-> 8.999999999999998 only for
# k = tempIdx*movesPerTemperature + moveCount that are multiples of 200.
MOVE_W_SLOPE = -0.35000000000000003  # @0x577d40 0xbfd6666666666667
MOVE_W_ASYMPTOTE = 0.05  # @0x577d38 (immediate only)  0x3fa999999999999a


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
                # devCostPercent = deviation() / bestCost            0x3c7616..0x3c7628
                #   deviation() is 0.0 for sampleSize() <= 1 (@0x3c7a5a, 0.0 @0x577860)
                #   and the division is unconditional (divsd @0x3c7628): bestCost == 0
                #   therefore yields 0/0 = NaN, never 0.
                dev = self.acc.deviation() if self.acc.n > 1 else 0.0
                dev_pct = _ieee_div(dev, self.best_cost)
                # comisd xmm0(0.0),[rbp-0x18] ; jb 0x3c7649        0x3c7635 / 0x3c763a
                #   CF=1 -> keep the value; comisd also sets CF=1 for NaN, so a NaN
                #   devCostPercent *skips* the 0.01 fallback.  Only a finite value <= 0
                #   reaches movsd xmm0,[0x5778c8] @0x3c763c..0x3c7644.
                if dev_pct != dev_pct:
                    # comisd xmm0,0.0 ; ja 0x3c7677 @0x3c7652..0x3c7656 falls through
                    # -> __assert_fail("devCostPercent > 0.0",...,328) @0x3c7658..0x3c7672
                    # The library aborts the process here (SIGABRT, exit 134); the trace
                    # simply ends.  This exception *is* that abort, not a convergence test.
                    raise CoolingScheduleAbort(
                        "helpers/CoolingSchedule.cpp:328: Assertion `devCostPercent > 0.0' "
                        "failed (deviation()=%r / bestCost=%r -> NaN; 0x3c7628, 0x3c765f)"
                        % (dev, self.best_cost))
                if not (0.0 < dev_pct):
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
    """``DesignPointFactoryImpl::create`` @0x3cef6e: one *new* DesignPoint per call.

    ``Cache::hit``/``Cache::insert`` (@0x3d7976/..) have no call site inside
    ``libalchemy.so``, so the engine cannot tell "same design as before" from
    "new design": every queued point is evaluated again.  The stochastic modules
    (Anneal family) must therefore not model a cache at all — a cache turns the
    unconditional re-evaluation of the driver into a silent skip (livelock).
    """

    def create(self, ix=None):
        if ix is None:
            ix = list(self.domain.center_index())
        pt = Pt([int(v) for v in ix])
        self.cache[tuple(pt.ix)] = pt
        return pt


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
#  Anneal
# =============================================================================
DEFAULT_MAX_STEPS = 0            # key "max_steps"  default 0  @0x2f8c3e (0 = infinite)
DEFAULT_TEMPERATURE = 1.0        # key "initial_temperature" @0x2f9a4c, const @0x563248
DEFAULT_SEED = 1                 # key "seed" default 1        @0x2f9442
POP_CAP = 1                      # "population" = "psaPopulation" = Population(1)
DEFAULT_ID = 0                   # key "id" default 0         @0x2f8a4d


def run(domain, goal, start=None, params=None):
    """Anneal as an ask/tell generator (one point per batch).

    ``params`` understands every hyper-parameter the C++ ``Data::Data`` reads:
    ``max_steps``, ``initial_temperature``, ``seed``, ``rng``, ``move_class``,
    ``criterion``, ``dimOrder``, ``current_point``.  Returns the best index list.
    """
    P = dict(params or {})
    check_domain(domain)
    seed = int(P.get("seed", DEFAULT_SEED))
    max_steps = int(P.get("max_steps", DEFAULT_MAX_STEPS))       # 0 => infinite
    temperature0 = float(P.get("initial_temperature", DEFAULT_TEMPERATURE))

    # DesignPointFactoryImpl::create @0x3cef6e never caches, so neither does this.
    fac = FreshFactory(domain)
    rng = P.get("rng") or Gen(seed)                              # @0x2f965c
    pop = P.get("population") or Population(POP_CAP)             # @0x2f9039 (cap 1)
    psa_pop = P.get("psaPopulation") or Population(POP_CAP)      # @0x2f923f (cap 1)
    dim_order = list(P.get("dimOrder") or range(fac.nDims()))     # @0x2f79c8
    if len(dim_order) != fac.nDims():
        raise ValueError("assert(dimOrder->size() == POINTS->tunables.size()) @0x2f7a95")

    if "current_point" in P and P["current_point"] is not None:
        cur = P["current_point"]
    elif start is not None:
        cur = fac.create(start)
    else:
        cur = fac.create()

    # ---- Start @0x2f7696 ----------------------------------------------------
    if not cur.isEvaluated():
        # ---- Start_WaitForStartingPoint @0x2f786e (poll) --------------------
        costs = yield [cur.ix]
        cur.cost = float(costs[0])

    # ---- Start_HaveStartingPoint @0x2f7992 ---------------------------------
    move_class = P.get("move_class")            # handle: possibly built by PSA
    criterion = P.get("criterion")
    pending = None
    moved = False
    if move_class is not None:
        # Re-entry branch @0x2f7ab9: assert(criterion); candidate = current_point;
        # current_point = population->top(); moved = false; -> PointDone.
        if criterion is None:
            raise ValueError("assert(criterion) @0x2f7af2")
        pending = cur
        cur = pop.top() or cur
        moved = False
    else:
        if criterion is not None:
            raise ValueError("assert(!*criterion) @0x2f7c3a")
        fg = ForkedGen(rng, 2)                  # @0x2f7c6d: two independent streams
        move_class = Hustin(fac.nDims(), fg.next(), cur.cost)     # @0x2f7d1a
        # 0x2f7dd8 -> KrazKool1(Parameters, double, shared_ptr<Random::Gen>, double)
        # @0x3c6a7c -- the *implemented* ctor (Alchemy's own machine: 0x3c7010
        # update, 0x3c7290 decrementTemperature, 0x3c7aa8 criterion).  Anneal2's
        # seed-taking sibling 0x3c6eac is the `throw "FIXME"` stub, which is the
        # whole difference between `Anneal` (measurable, 600/600) and `PSA2`
        # (stops at the stub): see native/PARITY.md §13.
        criterion = KrazKool1(temperature0, fg.next(), cur.cost)  # @0x2f7dd8

    while True:
        # ---- PointSubmit @0x2f7fa4 -----------------------------------------
        if pending is not None:
            cand = pending
            pending = None
        else:
            trial = list(cur.ix)                # POINTS->create(current_point->tunables)
            cls = move_class.getNextClass()     # vtable+0x18 @0x2f8046
            d = dim_order[cls]                  # @0x2f80be
            trial[d] = select_move(fac.levels(d), trial[d],
                                   criterion.time(), rng)    # @0x2f812a
            cand = fac.create(trial)
            moved = True                        # +0xa8 = 1 @0x2f8182
            # ``candidate->isEvaluated()`` is always false here: DesignPointFactoryImpl::
            # create @0x3cef6e / create(values) @0x3cf016 always make a *new* point and
            # never consult Cache (``Cache::hit`` has no caller inside libalchemy.so),
            # so the driver (FunctionOptimizer::run, 0x3cea95..0x3ceaff) evaluates
            # every queued point even when the same design was evaluated before.
            # Skipping the yield on a repeat design is what made this module livelock.
            costs = yield [cand.ix]
            cand.cost = float(costs[0])
        # ---- PointDone @0x2f8262 -------------------------------------------
        accepted = criterion.tryMove(cand.cost)      # @0x2f82ed
        psa_pop.insert(cand)                          # @0x2f8318
        pop.insert(cand)                              # @0x2f8340
        if accepted:
            cur = cand                                # @0x2f8363
        move_class.update(cand.cost, accepted, moved)  # vtable+0x10 @0x2f83c4
        if max_steps != 0:                            # @0x2f83d4
            max_steps -= 1
            if max_steps == 0:
                return list(pop.top().ix)             # __cxa_throw(Return) @0x2f842d
        # -> PointSubmit


PARAMS = {"seed": 3, "max_steps": 400}



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
    # This landscape's optimum is exactly 0 (index 23/12) -- on the way there the
    # cooling schedule divides 0.0/0.0 and the *library* asserts
    # (helpers/CoolingSchedule.cpp:328).  Native dies the same way, so the self-test
    # reports what the run had reached instead of a traceback.
    dom = Quadratic2D()
    seen = []

    def _evaluate(ix):
        c = dom.evaluate_index(ix)
        seen.append((list(ix), c))
        return c

    try:
        best, nfev = drive(run(dom, None, params=PARAMS), _evaluate)
        aborted = None
    except CoolingScheduleAbort as exc:
        aborted = exc
        best = min(seen, key=lambda t: t[1])[0]
        nfev = len(seen)
    _report(dom, best, nfev)
    if aborted is not None:
        print("note  = %s  (the library aborts here; the trace ends at %d)"
              % (aborted, nfev))

