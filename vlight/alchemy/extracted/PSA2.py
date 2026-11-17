"""PSA2 — extracted from libalchemy.so (Cadence Alchemy, `algs/PSA2.cpp`).

visible_name : "Parallel Simulated Annealing - NeoCircuit"  (.rodata 0x572008,
               the same string PSA registers; this module is driven through the
               short alias "PSA2")
states       : Init 0x39788c -> Start 0x397a22 -> WaitForStartingPoint
               -> PrepareRun/PrepareHammersley (the same helpers PSA uses)
               -> <Hammersley child> -> HammersleyDone 0x398068
               -> AnnealSplit 0x39889c -> Fork<AlgState, AnnealCall> 0x398a12
               -> (each fork is an **Anneal2** bubble, not Anneal)
               -> AnnealJoin 0x398c0e -> AnnealSplit  (loop)
hyperparams  : ``annealers`` = 9 (@0x572038), ``hammersley_points`` = -1,
               ``seed`` = 1234 (@0x399840, `mov DWORD [rax],0x4d2`),
               ``population`` = Population(90) (`PSA2::DefaultPopulationCount`
               @0x57203c = 90), ``StepsBetweenPopulationSamples`` = 10 (@0x572034),
               internal ``roundsRemaining`` = 200.

What this module reproduces, and why it stops where it does
-----------------------------------------------------------
PSA2 shares PSA's whole front end: the driver's start evaluation, the 50-point
Hammersley warm start (``PrepareHammersley`` 0x39eb76, the same ``points`` formula
and the same archive writes) and ``HammersleyDone`` 0x398068.  Measured: the first
51 native evaluations of ``PSA2`` and ``PSA`` are identical, point for point.

The first round never runs.  Every fork is an ``Anneal2`` bubble, and
``Anneal2::Start_HaveStartingPoint::run()`` builds its cooling schedule with the
``unsigned long`` overload

    KrazKool1(Parameters const&, double, unsigned long, double)  @0x3c6eac
      call site: Anneal2::Start_HaveStartingPoint::run()+0x2bd  @0x2e9ff9

which is the stub

    0x3c6ef4 KrazKool1::Parameters::Parameters()
    0x3c6ef9 __cxa_allocate_exception(8)
    0x3c6f03 std::string "FIXME"  (@0x5776a6)  -> *slot
    0x3c6f12 typeinfo for char const*           (0x6a86c8)
    0x3c6f1f __cxa_throw

The thrown object is a ``char const*`` (typeinfo ``PKc``), i.e. *not* a
``std::exception``, so it is not caught anywhere on the state-machine path and the
harness reports ``unknown exception (not std::exception)``.  Measured with
``catch throw``: 49 ``ShortCircuits::Join`` and one ``ShortCircuits::Return`` are
caught internally, then the ``char const*`` escapes at 51 evaluations -- exactly
1 driver start + 50 Hammersley points, zero round-1 points.

So the model raises ``FixmeAbort`` at the same place instead of inventing an
execution for the round machinery.  Everything past that point (``Anneal2``'s
10-point bubble, the per-fork resampling in ``AnnealJoin``, the 200 rounds) is
**unreachable through this entry point** -- it needs an injectable
``criterion`` / ``move_class`` in the fork ``Params``, which neither
``./native/harness`` nor ``pydrive`` can express.  PSA's own round machinery is
modelled in ``PSA.py`` (PARITY.md §10) and is deliberately not duplicated here:
copying it would claim a PSA2 behaviour that the binary never executes.

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

# --------------------------------------------------------------------------
#  DesignPoint::operator<  (the archive's ordering, and every tie-break that
#  follows from it)  --  copied from HookeJeeves.py, verified against the real
#  symbol with native/probe_cmp.cpp (2401/2401 pairs on a 7x7 grid).
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


class _Multiset(Population):
    """``std::multiset<shared_ptr<DesignPoint>, evil_ptr_less>`` -- the join's sample.

    ``AnnealJoin`` collects ``sampleWithReplacement``'s output into a multiset
    (0x3a4109) and walks it in order (0x3a0667).  With equal-cost points
    ``DesignPoint::operator<`` is not a strict weak ordering, so the *tree shape*
    decides the order: valley/1000 first diverges at ev640 precisely because
    (15,17) and (17,15) have the identical cost 7.4 and a plain ``sorted()``
    orders them the other way round.  ``insert`` is libstdc++
    ``_M_get_insert_equal_pos`` + ``_M_insert_`` (no duplicate rejection).
    """

    def __init__(self):
        Population.__init__(self, 1 << 62)

    def insert(self, pt):
        if pt is None or pt.cost is None:
            return
        x = self.root
        y = None
        while x is not None:
            y = x
            x = x.l if _pt_less(pt, x.pt) else x.r
        insert_left = True if y is None else _pt_less(pt, y.pt)
        node = _PopNode(pt)
        self._insert_rebalance(insert_left, node, y)
        self.n += 1
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
#  PSA
# =============================================================================
DEFAULT_SORTING_VARIABLES = False   # key "sortingVariables"    default false @0x3a0e65
DEFAULT_ANNEALERS = 9               # key "annealers"           default 9     @0x3a1056
DEFAULT_HAMMERSLEY_POINTS = -1      # key "hammersley_points"   default -1    @0x3a124a
DEFAULT_SEED = 1                    # key "seed"                default 1     @0x3a143e
DEFAULT_POPULATION = 90             # Population(90) @0x3a1852; resize(0x5a) @0x39ef76
ROUNDS_REMAINING = 200              # AlgState+0x5c (mov eax,[rsi+0x5c] @0x39f9e1)
BUBBLE_STEPS = 9                    # imm 0x9 @0x39fb60 -> annealers[i]["max_steps"]
INITIAL_TEMPERATURE = 1.0            # Anneal "initial_temperature" default @0x2f9a4c


def hammersley_count(n_dim, annealers, hp):
    """``PrepareHammersley`` 0x39ec50..0x39edfd: how many Hammersley points.

    ``hammersley_points > 0``  -> that value (0x39ec8c);
    ``hammersley_points == -1`` -> ``(int)(ceil((dim-20)/4.0)*50.0
                                   + annealers*250.0/9.0)``,
                                 then ``max(n, 5*annealers)`` (0x39eddc/0x39edf6).
    The truncation is ``cvttsd2si`` @0x39edb4, i.e. toward zero.
    dim 2..24 with 9 annealers gives 50 -- *not* the 90 the earlier note guessed.
    """
    if hp != -1:
        return int(hp)
    x = math.ceil((n_dim - 20) / 4.0) * 50.0          # 0x573410 / 0x573418
    x += (float(annealers) * 250.0) / 9.0             # 0x573420 / 0x573428
    n = int(x)                                        # 0x39edb4 cvttsd2si
    return max(n, annealers * 5)                      # 0x39edf6 std::max


def sample_with_replacement(rng, pts, n):
    """``Random::sampleWithReplacement`` @0x3a3ff5 -- **not** index draws.

    Verbatim from the disassembly (0x3a4036..0x3a412b):

        multiset out;  remaining = set.size();  need = n
        for (it = set.cbegin(); it != set.cend(); ++it) {
            k = 0;
            for (i = 0; i < need; ++i)
                if (1.0 > rng.in01() * (double)remaining) ++k;   // 0x3a40c4 comisd/seta
            for (j = 0; j < k; ++j) out.insert(*it);
            need -= k;                       // 0x3a4117
            --remaining;                     // 0x3a411a
        }

    So the draw pattern is 9, then 9-k0, ... hypergeometric draws, and every draw is
    ``Gen::in01()`` -- which burns **two** engine words (0x3cd65e).  A
    ``inrange(size)``-per-sample model (the previous version of this file) gives a
    completely different assignment and is what kept round 2 from matching.
    """
    out = []
    remaining = len(pts)
    need = n
    for pt in pts:
        k = 0
        for _ in range(need):
            if 1.0 > rng.in01() * float(remaining):
                k += 1
        out.extend([pt] * k)
        need -= k
        remaining -= 1
    return out


# =============================================================================
#  PSA2
# =============================================================================
class FixmeAbort(Exception):
    """``KrazKool1(Parameters const&, double, unsigned long, double)`` @0x3c6eac.

    The stub the library ships in place of the real cooling schedule for
    ``Anneal2``: it default-constructs ``Parameters`` (0x3c6ef4), then

        0x3c6efe  __cxa_allocate_exception(8)
        0x3c6f03  "FIXME" (string @0x5776a6) -> *slot     0x3c6f0a
        0x3c6f12  typeinfo for char const* (@0x6a86c8)
        0x3c6f1f  __cxa_throw

    Thrown from ``Anneal2::Start_HaveStartingPoint::run()+0x2bd`` (@0x2e9ff9), i.e.
    per fork, before a single round-1 point is submitted.  A ``char const*`` is not
    a ``std::exception``, so nothing on the path catches it and the whole process
    dies: ``./harness PSA2 ...`` ends with
    ``error: unknown exception (not std::exception)`` and nfev = 51.
    """


DEFAULT_SORTING_VARIABLES = False   # key "sortingVariables" (unverified for PSA2)
DEFAULT_ANNEALERS = 9               # key "annealers"          default 9   @0x39945e
DEFAULT_HAMMERSLEY_POINTS = -1      # key "hammersley_points"  default -1
DEFAULT_SEED = 1234                 # key "seed"               default 1234 @0x399840
DEFAULT_POPULATION = 90             # PSA2::DefaultPopulationCount @0x57203c (0x5a)
ROUNDS_REMAINING = 200              # AlgState+0x5c
BUBBLE_STEPS = 10                   # Anneal2's bubble is 10 points, not Anneal's 9
INITIAL_TEMPERATURE = 1.0           # const @0x561738 (Anneal2's t0)


def default_criterion(seed, temperature=INITIAL_TEMPERATURE):
    """MODEL-ONLY stand-in for ``KrazKool1(Parameters, double, unsigned long, double)``.

    The library has no implementation of that ctor overload: 0x3c6eac installs the
    vtable, default-constructs ``KrazKool1::Parameters`` (0x3c6ef4 -- it ignores the
    ``Parameters`` argument it was handed) and then throws ``char const*("FIXME")``
    (0x3c6f03/0x3c6f1f).  Anneal2 calls it unconditionally on the only path that
    exists: ``Anneal2::Start_HaveStartingPoint::run()+0x2bd`` @0x2e9ff9, right after
    ``assert(!*criterion)`` @0x2e9e54 (algs/Anneal2.cpp:58) has *rejected* a
    pre-populated criterion.  So:

      * the round machinery behind it (``AnnealSplit`` 0x39889c -> ``Fork`` 0x398968
        -> ``AnnealCall`` 0x398a12 -> ``Anneal2::Init`` 0x398a55 -> ``AnnealJoin``
        0x398c0e) is present in the binary but never entered -- measured for
        ``(annealers, hammersley_points)`` in {(1,1), (2,2), (3,30)}: nfev is always
        ``1 + hammersley_points`` and the backtrace of the escaping ``char const*``
        is ``__cxa_throw <- KrazKool1 ctor <- Start_HaveStartingPoint::run``;
      * anything this module does with ``params["criterion"]`` is *our own cooling*,
        not a reproduction.  The value below mirrors Anneal's implemented ctor
        (0x3c6a7c, ``Parameters, T0, shared_ptr<Gen>, cost``) with a Gen built from
        the fork seed -- a guess at what the stub would have done, unbacked by any
        instruction.  It exists so the vl smoke test (which injects a criterion) can
        exercise the bubble; its numbers are not part of the parity matrix.
    """
    return lambda cost: KrazKool1(temperature, Gen(seed + 101), cost)


def _anneal2_bubble(fac, prm, archive, steps, temperature0):
    """One PSA2 fork: an ``Anneal2`` run (``Anneal2::Start_HaveStartingPoint`` 0x2e9d3c).

    Anneal2 differs from Anneal (the PSA fork) in two places that matter here:
    ``criterion->setCost`` runs after every point (0x2ea5e7), and the archive
    insert happens before ``tryMove`` (0x2ea488 / 0x2ea4cb).  The cooling schedule
    itself is ``KrazKool1`` with the same law as Anneal's -- only the ctor overload
    in the library is a stub, which is why ``prm["criterion"]`` has to be injected.
    """
    cur = prm["current_point"]
    move_class = prm.get("move_class")
    criterion = prm.get("criterion")
    rng = prm["rng"]
    pending = None
    moved = False

    if move_class is not None:                      # re-entry @0x2e9dda
        pending = cur
        own = prm.get("population")
        cur = own.top() if own is not None and own.top() is not None else cur
        moved = False
    else:
        move_class = Hustin(fac.nDims(), ForkedGen(rng, 1).next(), cur.cost)  # @0x2e9f55
        if criterion is None:
            raise FixmeAbort('KrazKool1(..., unsigned long, ...) @0x3c6eac "FIXME"')
        criterion = criterion(cur.cost) if callable(criterion) else criterion
        prm["move_class"] = move_class
        prm["criterion"] = criterion

    own = prm.get("population")
    if own is None:
        own = Population(1)
        prm["population"] = own
    left = int(steps)
    while True:
        if pending is not None:
            cand = pending
            pending = None
        else:
            trial = list(cur.ix)
            d = move_class.getNextClass()
            trial[d] = select_move(fac.levels(d), trial[d], criterion.time(), rng)
            cand = fac.create(trial)
            moved = True
            cand.cost = float((yield cand.ix))
        own.insert(cand)                                # @0x2ea488
        archive.insert(cand)
        accepted = criterion.tryMove(cand.cost)          # @0x2ea4cb
        if accepted:
            cur = cand                                  # @0x2ea4f7
        move_class.update(cand.cost, accepted, moved)    # @0x2ea561
        criterion.setCost(cand.cost)                     # @0x2ea5e7
        if left != 0:
            left -= 1
            if left == 0:
                prm["max_steps"] = 0
                prm["current_point"] = cur
                return cur


def _multiplex(bubbles):
    """``Fork<PDE/PSA AlgState, AnnealCall>::run`` -- round-robin over the forks."""
    st = [{"g": g, "req": None, "done": False} for g in bubbles]
    for s in st:
        try:
            s["req"] = next(s["g"])
        except StopIteration:
            s["done"] = True
    while True:
        live = [s for s in st if not s["done"]]
        if not live:
            return
        costs = yield [s["req"] for s in live]
        for s, c in zip(live, costs):
            try:
                s["req"] = s["g"].send(c)
            except StopIteration:
                s["done"] = True


def run(domain, goal, start=None, params=None):
    """PSA2 as an ask/tell generator.

    Identical to ``PSA.run`` up to and including the Hammersley warm start, then
    ``Anneal2``'s cooling-schedule stub aborts the run (``FixmeAbort``).  Returns
    nothing: the library never reaches a ``Return``.

    ``params`` understands ``annealers``, ``hammersley_points``, ``sortingVariables``,
    ``seed``, ``population``/``population_size``, ``current_point``.
    """
    P = dict(params or {})
    check_domain(domain)
    fac = FreshFactory(domain)
    n = fac.nDims()
    seed = int(P.get("seed", DEFAULT_SEED))
    annealers = int(P.get("annealers", DEFAULT_ANNEALERS))
    hp = int(P.get("hammersley_points", DEFAULT_HAMMERSLEY_POINTS))
    sorting = bool(P.get("sortingVariables", DEFAULT_SORTING_VARIABLES))
    rng = P.get("rng") or Gen(seed)
    pop = P.get("population") or Population(int(P.get("population_size",
                                                      DEFAULT_POPULATION)))

    if P.get("current_point") is not None:
        cur = P["current_point"]
    elif start is not None:
        cur = fac.create(start)
    else:
        cur = fac.create()

    # ---- driver call #0 (FunctionOptimizer::run, cost callback 0x3cead3) ----
    if not cur.isEvaluated():
        costs = yield [cur.ix]
        cur.cost = float(costs[0])

    dim_order = list(range(n))               # identity fill

    if sorting:
        # Same ``InitialOFAT`` shape PSA models (0x39de3c in PSA; the PSA2 copy is
        # [unproven] -- the harness cannot inject a scalar bool, PARITY.md §10.6).
        mid = [int(math.floor(0.5 * fac.tunables[d])) for d in range(n)]
        probes = []
        for d in range(n):
            if fac.tunables[d] <= 1:
                continue
            ix = list(mid)
            if ix[d] == fac.tunables[d] - 1:
                ix[d] -= 1
            else:
                ix[d] += 1
            ix[d] %= fac.tunables[d]
            probes.append((d, fac.create(ix)))
        batch = [p for _, p in probes if not p.isEvaluated()]
        if batch:
            costs = yield [p.ix for p in batch]
            for p, c in zip(batch, costs):
                p.cost = float(c)
        base = fac.create(mid)
        if not base.isEvaluated():
            costs = yield [base.ix]
            base.cost = float(costs[0])
        scored = sorted(((abs(p.cost - base.cost), d) for d, p in probes),
                        reverse=True)
        dim_order = [d for _, d in scored] + [d for d in range(n)
                                             if d not in [x for _, x in scored]]

    # ---- PrepareRun -> PrepareHammersley (shared with PSA) ----------------
    if hp != 0:
        npts = hammersley_count(n, annealers, hp)
        pop.resize(DEFAULT_POPULATION)
        unit = hammersley_points(npts, n)
        designs = [fac.create(hammersley_design(fac, dim_order, u)) for u in unit]
        costs = yield [p.ix for p in designs]
        for p, c in zip(designs, costs):
            p.cost = float(c)
        for p in designs:
            pop.insert(p)
        n_warm = len(designs)
    else:
        pop.insert(cur)
        starts = []
        for _ in range(annealers - 1):
            ix = [0] * n
            for d in range(n):
                t = dim_order[d]
                ix[t] = rng.inrange(fac.tunables[t])
            starts.append(fac.create(ix))
        costs = yield [p.ix for p in starts]
        for p, c in zip(starts, costs):
            p.cost = float(c)
        for p in starts:
            pop.insert(p)
        n_warm = len(starts)

    # ---- HammersleyDone @0x398068 -----------------------------------------
    # Builds the nine fork Params {id, rng, psaPopulation, current_point, dimOrder}
    # exactly as PSA's does (0x39f278); nothing here can emit a point, so the
    # difference between the two copies is unobservable.
    order = pop.as_list()
    if not order:
        raise ValueError("empty archive after the warm start")
    fg = ForkedGen(rng, annealers)
    forks = []
    it = 0
    for i in range(annealers):
        forks.append({
            "id": i,
            "rng": fg.next(),
            "psaPopulation": pop,
            "dimOrder": list(dim_order),
            "current_point": order[it],
            "max_steps": 0,
            "move_class": None,
            "criterion": None,
            "population": None,
        })
        it += 1
        if it >= len(order):
            it = 0

    # ---- AnnealSplit @0x39889c -> Fork<AlgState, AnnealCall> 0x398a12 ------
    # -> AnnealCall::run 0x... -> Anneal2::Init -> Start -> Start_HaveStartingPoint,
    # which asks for the cooling schedule.  With no criterion in Params that is the
    # stub, and the first fork kills the process -- no round-1 evaluation at all:
    criterion_spec = P.get("criterion")
    if criterion_spec is None:
        raise FixmeAbort(
            'KrazKool1(Parameters const&, double, unsigned long, double) @0x3c6eac: '
            'throw "FIXME" (called from Anneal2::Start_HaveStartingPoint::run() '
            '@0x2e9ff9), uncaught `char const*` -> the run dies after %d evaluations'
            % (1 + n_warm))

    # ---- INJECTION BRANCH (unreachable from ./native/harness) --------------
    # ``Anneal2::Start_HaveStartingPoint`` only calls the stub when Params has no
    # ``criterion``; with one injected the rounds run.  Neither `harness` nor
    # `harness_manual` can inject a shared_ptr<Criterion>, so this branch has **no
    # native reference** -- it follows PSA's AnnealJoin/AnnealCall shape (PSA.py,
    # PARITY.md §10.3f/§10.5) with Anneal2's bubble (10 steps, setCost per point).
    for prm in forks:
        prm["criterion"] = criterion_spec
    remaining = int(P.get("rounds", ROUNDS_REMAINING))
    while True:
        e = remaining
        remaining -= 1
        if e == 0:
            return list(pop.top().ix)                   # Return @0x39f9f5
        if annealers <= 0:
            raise ValueError("assert(*annealers>0) @0x39fa46")
        bubbles = []
        for i in range(annealers):
            prm = forks[i]
            prm["max_steps"] = BUBBLE_STEPS             # @0x39fb60
            bubbles.append(_anneal2_bubble(fac, prm, pop, BUBBLE_STEPS,
                                           INITIAL_TEMPERATURE))
        yield from _multiplex(bubbles)

        # ---- AnnealJoin @0x398c0e ------------------------------------------
        sampled = sample_with_replacement(rng, pop.as_list(), annealers)
        if len(sampled) != annealers:
            raise ValueError("assert(pts.size() == *annealers) @0x3a04e3")
        perm = list(range(annealers))
        for i in range(annealers - 1):
            j = i + 1 + rng.inrange(annealers - i - 1)
            perm[i], perm[j] = perm[j], perm[i]
        ms = _Multiset()
        for pt in sampled:
            ms.insert(pt)
        for k, pt in enumerate(ms.as_list()):
            prm = forks[perm[k]]
            if int(prm.get("max_steps") or 0) != 0:     # @0x3a071e
                return list(pop.top().ix)
            prm["current_point"] = pt


PARAMS = {"seed": 1234, "annealers": 9}


# =============================================================================
#  Self-test: a 2-D quadratic on a 41 x 41 integer grid
# =============================================================================
class Quadratic2D:
    """``f(x, y) = (x-1.5)^2 + (y+4.0)^2`` on 41 levels per dimension."""

    NAME = "2-D quadratic, 41 levels"

    def __init__(self, levels=41, lo=-10.0, hi=10.0):
        self.levels = int(levels)
        self.tunables = [self.levels, self.levels]
        self.lo = lo
        self.hi = hi

    def from_index(self, ix):
        span = self.hi - self.lo
        return [self.lo + span * i / float(self.levels - 1) for i in ix]

    def to_index(self, point):
        span = self.hi - self.lo
        return self.clamp_index([int(round((v - self.lo) * (self.levels - 1) / span))
                                 for v in point])

    def center_index(self):
        return [self.levels // 2] * 2

    def clamp_index(self, ix):
        return [max(0, min(self.levels - 1, int(v))) for v in ix]

    def cost(self, point):
        x, y = self.from_index(point)
        return (x - 1.5) ** 2 + (y + 4.0) ** 2

    def evaluate_index(self, ix):
        return self.cost(self.clamp_index(ix))

    def all_specs_good_enough(self, ix, c):
        return False


if __name__ == "__main__":
    # The real library aborts here (see FixmeAbort): the self-test reports the warm
    # start it managed to evaluate instead of a traceback.
    dom = Quadratic2D()
    gen = run(dom, None, params=PARAMS)
    nfev = 0
    best = None
    try:
        batch = next(gen)
        while True:
            costs = [dom.evaluate_index(ix) for ix in batch]
            nfev += len(costs)
            for ix, c in zip(batch, costs):
                if best is None or c < best[1]:
                    best = (list(ix), c)
            batch = gen.send(costs)
    except StopIteration as stop:
        if stop.value is not None:
            best = (list(stop.value), dom.evaluate_index(stop.value))
    except FixmeAbort as exc:
        print("point=%s cost=%.6f nfev=%d  (%s)" % (best[0], best[1], nfev, dom.NAME))
        print("note  = %s" % exc)
        raise SystemExit(0)
    if best is None:
        print("no evaluations")
    else:
        print("point=%s cost=%.6f nfev=%d  (%s)" % (best[0], best[1], nfev, dom.NAME))
