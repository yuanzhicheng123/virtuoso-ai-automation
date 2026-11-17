"""OFATSearch — extracted from libalchemy.so (Alchemy::OFATSearch, algs/OFATSearch.cpp,
visible name "OFATSearch").

states (addresses are the run() entry points):
    Init                        0x357676 -> Start
    Start                       0x35780c -> Call<OFATDone>( SensitivityOFAT::Init(params) )
    OFATDone                    0x357d9c -> SubmitDone
    SubmitDone                  0x3582fe -> Fork<FactorialSubmit>( point_set->size() )
    FactorialSubmit             0x3585ba -> FactorialWait
    FactorialWait               0x35887e -> JOIN -> BeginCentralCompositeDeisgin
    BeginCentralCompositeDeisgin 0x358a10 -> Fork<SplitCentralCompositePoints>(n-currentIdx) | BrentPowellBegin
    SplitCentralCompositePoints 0x358d86 -> Fork<SplitCentralCompositePoints>(n-ci) | CreateCentralCompositePoints | CentralCompositePointsWait
    CreateCentralCompositePoints 0x359176 -> CentralCompositePointsWait
    CentralCompositePointsWait  0x35974c -> JOIN -> BrentPowellBegin
    BrentPowellBegin            0x3599c2 -> Call<BrentPowellDone>( Powell::Init )
    BrentPowellDone             0x359e82 -> throw ShortCircuits::Return      (end of run)

hyperparams (Alchemy::OFATSearch::Data 0x35a21c):
    current_point      shared_ptr<DesignPoint>   --      base point
    fractional_factor  int                       5       0x35a513
    stop_on_error      bool                      false   0x35a704
    population         shared_ptr<Population>    --      cost-ordered bounded set
    (Start 0x35780c additionally injects use_OFAT=1 @0x35799a and allPoints=1 @0x357a13)

1. Screening is NOT done by FactorialSubmit:  Start builds a fresh Params and wraps
   Alchemy::SensitivityOFAT::Init in Call<OFATSearch::OFATDone> (ctor call 0x357b88).
2. The 2-level screen size is 2^min(n, fractional_factor):
   PointSet::fractionalFactorialDesigns 0x3cb4e4 -> N = (uint64_t)pow(2.0, n - nExtra)
   with the 2.0 double at .rodata 0x578008 (0x3cb567-0x3cb577) and nExtra = max(0,n-ff).
3. The "CCD" is a pairwise anti-diagonal design ((max,0) / (0,max) per factor pair),
   no centre point -- the base point already is the centre.
"""

# --- .rodata constants ------------------------------------------------------
KEY_CURRENT_POINT = 0x56bb43   # 'current_point'
KEY_FRACTIONAL    = 0x56bb51   # 'fractional_factor'
KEY_STOP_ON_ERROR = 0x56bb63   # 'stop_on_error'
KEY_POPULATION    = 0x56bb71   # 'population'
KEY_USE_OFAT      = 0x56bb7c   # 'use_OFAT'      (set to 1 at 0x35799a)
KEY_ALLPOINTS     = 0x56bb85   # 'allPoints'     (set to 1 at 0x357a13)

STR_SUBMIT_RUN    = 0x56bb90   # 'virtual Alchemy::State::Ptr Alchemy::OFATSearch::SubmitDone::run()'
STR_SRC           = 0x56bbd3   # 'algs/OFATSearch.cpp'
STR_POINTSET      = 0x56bbe7   # 'point_set->size()>0'                 (line 116)
STR_FACTSUBMIT    = 0x56bc00   # 'virtual ... FactorialSubmit::run()'
STR_FORK_ROWS     = 0x56bc48   # 'FORKIDX<(int)point_set->size()'      (line 123)
STR_ROW_LEN       = 0x56bc68   # 'd<point_set->at(FORKIDX).size()'     (line 124)
STR_CCD_BEGIN     = 0x56bc88   # 'virtual ... BeginCentralCompositeDeisgin::run()'
STR_CI_LT_N       = 0x56bce0   # 'POINTS->tunables.size()-currentIdx>0'
STR_CCD_SPLIT     = 0x56bd08   # 'virtual ... SplitCentralCompositePoints::run()'
STR_CCD_CREATE    = 0x56bd60   # 'virtual ... CreateCentralCompositePoints::run()'
STR_CI_F_LT_N     = 0x56bdb8   # 'currentIdx+FORKIDX<current_point->values.size()'  (line 169)
STR_UPPER_OR_LOW  = 0x56bde8   # 'upperBool||lowerBool'                (line 206)

import importlib.util
import math
import os

POW2_CONST        = 0x578008   # .rodata double 2.0 used by pow() inside fractionalFactorialDesigns
TI_RETURN         = 0x6a8a78   # typeinfo ShortCircuits::Return
DEFAULT_FF        = 5          # 0x35a513  mov DWORD PTR [rax],0x5


# --------------------------------------------------------------------------- #
#  canonical Powell / LineMinimize: algorithms/Powell.py, loaded by path
# --------------------------------------------------------------------------- #
def _load_powell():
    """Load the *verified* `algorithms/Powell.py` extraction (path-relative,
    stdlib only -> `python3 OFATSearch.py` still works).

    Powell.py is the copy that was driven to point-for-point parity on three
    landscapes x dims 2/3/4 (native/PARITY.md) and it owns the two pieces the
    private line search below used to get wrong:

      * `LineMinimize.py` by path -- the canonical Min_Exhaustive / Min_Brent
        (bracket bookkeeping `nb = old-begin index when the probe improves`,
        0x336331..0x3364c1);
      * `_NoCachePointFactory` -- `DesignPointFactoryImpl::create` always
        allocates a *fresh* point and enqueues it (0x3cef6e / 0x3cf016), so the
        cache lookup modelled by LineMinimize.py's `_Factory.create` would
        suppress evaluations the native line search performs (e.g. re-evaluating
        the base point of a Min_Exhaustive scan).

    Importing Powell.py also gives us its `_dp_less` (DesignPoint::operator<
    0x3d1dda), so the OFAT population and Powell's population cannot drift.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Powell.py")
    spec = importlib.util.spec_from_file_location(
        "AlchemyPowell_%s_%d" % (__name__, id(object())), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_Powell = _load_powell()
LineMinimize = _Powell.LineMinimize


def _params(params):
    """Accept None / dict / any Params-like object (e.g. vlight.alchemy.Params)."""
    if params is None:
        return {}
    if isinstance(params, dict):
        return dict(params)
    f = getattr(params, "as_dict", None)
    if callable(f):
        try:
            return dict(f())
        except Exception:
            pass
    d = getattr(params, "_d", None)
    if isinstance(d, dict):
        return dict(d)
    try:
        return dict(params)
    except Exception:
        return {}


def _better(goal, a, b):
    mx = bool(getattr(goal, "maximize", False)) if goal is not None else False
    return a > b if mx else a < b


def _fractional_factorial_designs(n_factors, n_extra):
    """PointSet::fractionalFactorialDesigns 0x3cb4e4, verified by emulation.

    N = (uint64_t)pow(2.0, n_factors - n_extra)                   0x3cb567
    base columns 0..k-1 (k = n-nExtra): d[i] = (row >> i) & 1     0x3cb5e5
    generator columns k..n-1: d[i] = d[a] && d[b] over a pair walk 0x3cb6b4
      a starts at 0, b from a+1 while b < sh (sh = k, then sh += sh-a)
    """
    assert n_factors > n_extra                                  # 0x3cb50f
    k = n_factors - n_extra
    N = int(2.0 ** k)                                           # pow(2.0, k)
    rows = []
    for row in range(N):
        d = [False] * n_factors
        i = 0
        while i < k:
            d[i] = bool((1 << i) & row)
            i += 1
        while i < n_factors:
            a = 0
            sh = k
            while True:
                b = a + 1
                while b < sh:
                    d[i] = d[a] and d[b]
                    i += 1
                    if i >= n_factors:
                        break
                    b += 1
                if i >= n_factors:
                    break
                a += 1
                sh = sh + (sh - a)
        rows.append(d)
    return rows


# --------------------------------------------------------------------------- #
#  Alchemy::Population<shared_ptr<DesignPoint>, evil_ptr_less> emulation
# --------------------------------------------------------------------------- #
# Why this exists: every consumer of the OFAT chain ranks factors by scanning the
# *cost-ordered population*, so both its content and its order matter for the point
# sequence.  DesignPoint::operator< (@0x3d1dda) compares, in this order:
#     1. the boost::optional<double> cost      (0x3d1e01 / 0x3d1e2a)
#     2. the bool at +0x18                     (0x3d1e41 / 0x3d1e66)
#     3. the design sizes (unequal -> not less) (0x3d1e99)
#     4. two 32-bit hash accumulators          (0x3d1ed0-0x3d2033)
#     5. the values lexicographically          (0x3d206d-0x3d2115)
# The hash loop is *asymmetric*: h1 accumulates `a`'s digits while h2 accumulates
# `b`'s, but BOTH are rotated by `a`'s digits (`mov rax,[rbp-0x38]` = `this` at
# 0x3d1f5a / 0x3d1f8b / 0x3d1fc8 / 0x3d1ff9), so operator< is not a strict weak
# ordering for equal-cost points.  Consequence, measured on the real library:
# `std::set::insert` silently drops a point it considers "equivalent", which removes
# whole factor/level combinations from the population and therefore from OFATDone's
# ranking.  Example (`harness OFATSearch 4 21 600 0 18 ...`): the four 972-cost points
# (18,0,0,0) (0,18,0,0) (0,0,18,0) (0,0,0,18) collide pairwise, (0,18,0,0) is dropped,
# and the committed greedy point becomes (18,19,18,18) instead of (18,18,18,18).
# The emulation below is a faithful libstdc++ `std::set`: a *red-black* tree whose
# insert decision (`_M_get_insert_unique_pos`: descend with less(), then compare
# against the in-order predecessor) depends on the tree SHAPE, and whose
# insert/erase rebalance with rotations (`_Rb_tree_insert_and_rebalance`,
# `_Rb_tree_rebalance_for_erase`).  Because operator< is not a strict weak ordering,
# "equivalent" is not transitive, so a plain BST walk sees a different predecessor
# than the real tree and accepts/rejects different points.  Measured example:
# `harness OFATSearch 4 30 --target 7 valley`, screen point (29,0,0,0) (cost 184.1,
# the same cost as (0,0,0,29)/(0,29,0,0)/(0,0,29,0)): the real std::set keeps 100 of
# the 117 inserts, a BST model keeps 99 and loses (29,0,0,0), which flips OFATDone's
# factor-0 commit from 29 to 28.
# `discardExtras` (0x2f17a0) erases [begin+maxsize, end) one element at a time, i.e.
# the in-order tail; capacity comes from SensitivityOFAT::Start's allPoints branch
# (0x3b6707-0x3b678d: 1 + sum(tunables[i] - 1)), see `run()`.
def _dp_less(a, b):
    """DesignPoint::operator< @0x3d1dda.  a, b = (values, cost).

    The asymmetric hash of step 4 (both accumulators rotated with the LEFT
    operand's digits, 0x3d1f5a/0x3d1f8b/0x3d1fc8/0x3d1ff9) is not duplicated
    here: Powell.py's `_dp_less` is the copy that was verified symbol-by-symbol
    against the real library (native/probe_cmp.cpp), so the OFAT population and
    Powell's population are guaranteed to sort identically.
    """
    return _Powell._dp_less(a[0], a[1], b[0], b[1])


_RED = True
_BLACK = False


class _PopNode(object):
    """`std::_Rb_tree_node<shared_ptr<DesignPoint>>`: payload + parent/left/right + colour."""

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
    """`Alchemy::Population<shared_ptr<DesignPoint>, evil_ptr_less>` (0x2efdf0 etc.).

    The container really is a `std::set`, i.e. a red-black tree: `erase` at
    `discardExtras` 0x2f17a0 and `insert` at `insertAndDiscardExtras` 0x2efdf0.
    The methods below mirror libstdc++ `bits/stl_tree.h` operation for operation
    (`_M_get_insert_unique_pos`, `_Rb_tree_insert_and_rebalance`,
    `_Rb_tree_rebalance_for_erase`), because with the non-strict-weak
    `DesignPoint::operator<` the tree *shape* decides which points are considered
    duplicates -- see the comment above `_dp_less`.
    """

    def __init__(self, capacity):
        self.cap = int(capacity)
        self.root = None
        self.leftmost = None
        self.rightmost = None
        self.n = 0

    # ---- Population::insertAndDiscardExtras @0x2efdf0 ---------------------
    def insert(self, values, cost):
        pt = (tuple(int(v) for v in values), float(cost))
        self._insert_unique(pt)                      # std::set::insert @0x2efe0e
        while self.n > self.cap:                     # discardExtras @0x2f17a0
            self.erase_max()

    # ---- std::set::insert -> _M_get_insert_unique_pos ---------------------
    def _insert_unique(self, pt):
        x = self.root
        y = None                                     # `_M_end()` (the header)
        comp = True
        while x is not None:                         # descend: less(new, node)
            y = x
            comp = _dp_less(pt, x.pt)
            x = x.l if comp else x.r
        j = y
        if comp:
            if j is self.leftmost:                   # `if (j == begin())` -> insert
                j = None
            else:
                j = self._prev(j)
        if j is not None and not _dp_less(j.pt, pt):  # equivalent -> rejected
            return False
        node = _PopNode(pt)
        # _M_insert_: insert_left == comp (the tree was empty -> root, colour fixed below)
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

    # ---- _Rb_tree_rebalance_for_erase (SGI / libstdc++ verbatim) ----------
    def erase(self, z):
        y = z
        x = None
        x_parent = None
        if y.l is None:                              # at most one child
            x = y.r
        elif y.r is None:
            x = y.l
        else:                                        # two children: successor
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
        """`discardExtras` erases the in-order tail (one element here)."""
        if self.rightmost is not None:
            self.erase(self.rightmost)

    def top(self):
        """Population::top() -- the in-order first element."""
        return self.leftmost.pt if self.leftmost is not None else None

    def __len__(self):
        return self.n

    def __iter__(self):
        """In-order traversal (cost ascending, hash order inside equal costs)."""
        out = []
        x = self.leftmost
        while x is not None:
            out.append(x.pt)
            x = self._next(x)
        return iter(out)

    def items(self):
        return list(self)


def _driver_start(domain, start):
    """Driver call #0 -- ``FunctionOptimizer::run`` (0x3ce70a, spec_core.md §6) evaluates
    the start point before OFATSearch::Start @0x35780c builds the SensitivityOFAT
    sub-run.  Emitting it keeps the evaluation sequence comparable point-for-point.
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


def _sweep(domain, goal, origin, tun, flags, all_points, steps, pop, stop_on_error,
           begin=0, end=None):
    """SensitivityOFAT restricted to [begin,end): a generator yielding one batch per tunable.

    Yields batches; returns nothing (the population is appended in place).

    Order.  ``SensitivityOFAT::Split::run`` @0x3b6cf2 branches on ``FORKIDX == 0``
    (``mov eax,[rax+8]; test eax,eax`` @0x3b6e0f): that child advances ``tunable_idx``
    (``+0x40``, ``lea edx,[rax+1]`` @0x3b6e42), copies ``original_values`` into
    ``values`` (0x3b6e33) and recurses; children ``1..L-1`` are the level points.
    ``Fork`` runs its children in ascending FORKIDX order (``std::set<int> actives``,
    0x333777 / 0x333791), so the recursion is entered first and the DEEPEST tunable is
    submitted first: ``t = end-1 .. begin``.  Measured: ``harness OFATSearch 4 21 ...``
    scans dim 3, then 2, then 1, then 0.
    """
    n = len(tun)
    if end is None:
        end = n
    for t in range(end - 1, begin - 1, -1):
        L = tun[t] if (all_points or flags[t]) else 3            # 0x3b6af3
        pts = []
        for k in range(1, L):
            if all_points or flags[t]:
                level = k if k > origin[t] else k - 1            # 0x3b7560 / 0x3b7573
            else:
                # 0x3b7638 / 0x3b7692 -- both children fork from the untouched state
                level = origin[t] - steps[t][0] if k == 1 else origin[t] + steps[t][1]
            if level != origin[t] and 0 <= level < tun[t]:
                pt = list(origin)
                pt[t] = level
                pts.append(pt)
        if pts:
            costs = yield pts
            for pt, c in zip(pts, costs):
                pop.insert(pt, c)                    # PointDone -> insertAndDiscardExtras
                if stop_on_error and c != c:
                    return
    # WaitFirstPoint 0x3b8cc4 submits current_point, already evaluated by the driver
    # (call #0) -> no cost call, so nothing is emitted for it here.


# --------------------------------------------------------------------------- #
#  Powell + its nested line search (algorithms/LineMinimize.py, via Powell.py)
#  -- used by BrentPowellBegin 0x3599c2
# --------------------------------------------------------------------------- #
_DBG = bool(os.environ.get("OFAT_DEBUG"))   # set OFAT_DEBUG=1 to trace the Powell loop

CGOLD = 0.381966                 # Alchemy::LineMinimize::CGOLD @0x568a20 / 0x5691f0.
                                 # .rodata bytes 9c c5 8b 85 21 72 d8 3f =
                                 # 0.38196600000000003 -- the *decimal literal*, not
                                 # (3-sqrt(5))/2 = 0.3819660112501051.  LineMinimize.py
                                 # is pinned to the value the library really multiplies with.
STAGNANT_COST_IMPROVEMENT = 0.999  # Powell::StagnantCostImprovement @0x5715b8
MIN_STEPS_FOR_BRENT = 30         # Powell Data default, `movl $0x1e` @0x394dbc

LineMinimize.CGOLD = CGOLD       # 0x568a20 -- see above


class _LineDomain(object):
    """Minimal domain so algorithms/LineMinimize.py can be driven from the tunable
    vector alone (keeps `python3 OFATSearch.py` stdlib-only and argument-free).
    LineMinimize is always called here with an explicit start and with goal=None
    (every cost is sent in), so only `tunables` / `clamp_index` are ever used."""

    __slots__ = ("tunables",)

    def __init__(self, tun):
        self.tunables = [int(t) for t in tun]

    def clamp_index(self, ix):
        return [min(max(int(v), 0), t - 1) for v, t in zip(ix, self.tunables)]

    def center_index(self):
        return [(t - 1) // 2 for t in self.tunables]

    def from_index(self, ix):
        return list(ix)


class _PopView(object):
    """The `population` hyper-parameter bridge: the line search writes into the OFAT
    archive (Powell::Loop::run 0x392de6 / key "population" 0x571619).

    `LineMinimize.run` speaks the library's Population protocol
    (`top()` -> DesignPoint-like with `.ix`/`.cost`, `insert(dp)`, `set_error(bool)`),
    while `Population` above models the same `std::set` with a value/cost API
    (`insert(values, cost)`, `top() -> (values, cost)`).  The wrapper is deliberately
    thin: it carries no state of its own, so every insert lands in the one archive
    that OFATDone / the final `rank()` read.

    `set_error` is a no-op because the single-cost model never stamps
    `hasErrorCost` (`+0x1b`); `Min_Exhaustive_Wait` 0x33539f and
    `Min_Brent_WaitPoint` 0x336258 only forward `probe.err`, which `_Pt` leaves False.
    """

    __slots__ = ("_pop",)

    def __init__(self, pop):
        self._pop = pop

    def top(self):
        pt = self._pop.top()                     # Population::top() 0x2c8330
        return LineMinimize._Pt(tuple(int(v) for v in pt[0]), float(pt[1]), False)

    def insert(self, p):
        # insertAndDiscardExtras 0x2efdf0 -- same call the archive's other writers use
        self._pop.insert([int(v) for v in p.ix], float(p.cost))

    def set_error(self, flag):
        pass


def _line_minimize(tun, base_pt, base_cost, direction, min_steps, population=None,
                   put=None):
    """One nested `Alchemy::LineMinimize` run: the canonical
    algorithms/LineMinimize.py (via Powell.py), never a private copy.

    Which point is the base?  LineMinimize::Start 0x3340c0 branches on
    `current_point && current_point->id != 0` (0x3340f3 / 0x334106, `id` at +0xd0),
    but `DesignPointFactoryImpl::create` always stamps id = 0 (0x3cef98), so the
    `current_point` branch is dead and 0x334162 `population->top()` is taken
    instead.  Powell::Loop::run injects the *shared* population into the nested
    line search (key @0x571619, written at 0x392de6), so inside the OFAT chain the
    base is the OFAT population's top -- not Powell's `current_point`:
    LineMinimizeDone only adopts the top into `current_point` when it is strictly
    cheaper (0x393496 seta, see _powell), so the incumbent can lag behind the top.

    Who writes the population?  The line search, and only the line search:
    `Start` seeds the base point (algorithms/LineMinimize.py:455), `Min_Exhaustive_Wait`
    inserts every scanned point (0x33539f) and `Min_Brent_WaitPoint` inserts its probe
    at 0x336258 -- **after** the fail-count test that throws
    `ShortCircuits::Return` at 0x33622a.
    A probe killed by that test is evaluated and never stored, so the caller must
    not "insert everything I evaluated" (PARITY.md §14.4).  Powell::Loop::run
    forwards no `fail_count_limit` (only current_point/population/vector/
    spec_index/minimum_steps_for_brent, 0x392c56..0x392f80), so the nested Data
    keeps the default built at 0x337422 -- a private `shared_ptr<unsigned>(0)` --
    and today the test cannot fire; passing the population keeps the model right
    for the day it can.

    The integer walk (Min_Exhaustive 0x334bc4..0x334e3e, Min_Brent 0x335520 with
    the `nb` bracket update at 0x336331..0x3364c1), the truncating rounding
    (cvttsd2si, 0x334a2f) and the no-cache point factory all come from
    Powell.py's LineMinimize.  `put` is only used by the standalone fallback
    (`population is None`, the module's own `_Pop(1)`).
    """
    params = {"vector": [float(v) for v in direction],
              "directional": False,
              "fail_count_limit": 0,
              "minimum_steps_for_brent": int(min_steps),
              "start_cost": float(base_cost)}
    if population is not None:
        params["population"] = population
    lm = LineMinimize.run(_LineDomain(tun), None, start=list(base_pt), params=params)
    try:
        batch = next(lm)
    except StopIteration:
        return
    while True:
        got = yield [list(p) for p in batch]
        if put is not None:                       # no shared population -> track here
            for ix, c in zip(batch, got):
                put(ix, float(c))
        try:
            batch = lm.send([float(c) for c in got])
        except StopIteration:
            return


def _powell(tun, cur, cur_cost, best_pt, best_cost, pop=None,
            min_steps=MIN_STEPS_FOR_BRENT, spec_index=-1):
    """Alchemy::Powell (Powell::Init 0x359c18 <- OFATSearch BrentPowellBegin 0x3599c2).

    LoopBegin 0x392a3c  : directions = canonical basis when the set is empty; cursor 0;
                          the decrease map (state+0x60) is cleared.
    Loop 0x392c56       : state+0x48 = cost(current_point) (0x392c82), then one nested
                          LineMinimize along directions[cursor].  The nested run gets
                          `current_point` *and* the shared `population`
                          (0x392d1b / 0x392de0); every factory-made point carries
                          id 0 (+0xd0, 0x3cef98), so LineMinimize::Start takes its
                          base from population->top() (0x334162) -- the line starts
                          from the population top, not from `current_point`.
    LineMinimizeDone    : state+0x60[state->48 - cost(cur)] = cursor  (0x393548-0x393582,
                          a std::map<double,unsigned> keyed by the DECREASE); the
                          population top replaces ``cur`` only when *strictly* cheaper
                          (0x393496 comisd + seta).  An equal-cost top is not adopted;
                          it is merely the next line's base (0x334162 again).

    Who owns the population?  The nested line search, not this loop: `population` is
    passed through as a real LineMinimize hyper-parameter (`_PopView` wrapping the OFAT
    archive) and every insert comes from Start / Min_Exhaustive_Wait 0x33539f /
    Min_Brent_WaitPoint 0x336258 -- the last one *after* the fail-count test at
    0x3361d2 that throws at 0x33622a (PARITY.md §14.4, §16).  With the archive in the
    line search's hands, `best_pt`/`best_cost` are re-read from `population->top()`
    once per line, exactly where LineMinimizeDone reads it (0x393448).

    LoopDone 0x393934   : stop when cur == x0 (pointer 0x39396d or values 0x3939b1);
                          restart with an empty direction set when min(decrease)/cost(x0)
                          > 0.999 (0x39422b / 0x3942d1); else x0 = cur (0x39477c) and
                          dirs[k] = dirs[k-1] .. dirs[0] = (cur-x0)/|cur-x0|
                          (0x3945a2-0x394750) with k = the largest-decrease index
                          (map.rbegin()->second 0x3945ba).
    """
    n = len(tun)

    popview = _PopView(pop) if pop is not None else None

    def put(pt, c):
        """Standalone fallback only (`pop is None`): without a shared population the
        nested run keeps LineMinimize's private `_Pop(1)`, so the caller has to keep
        its own incumbent.  With a shared population this never runs -- the line
        search is the only writer (Min_Exhaustive_Wait 0x33539f /
        Min_Brent_WaitPoint 0x336258), exactly like the library."""
        nonlocal best_pt, best_cost
        if c < best_cost:
            best_pt, best_cost = list(pt), c

    def pop_top():
        """Base of the next nested line search: LineMinimize::Start 0x334162
        (`population->top()`); DesignPointFactoryImpl::create stamps id = 0, so
        the `current_point` branch (0x3340f3 / 0x334106) is never taken -- i.e.
        the OFAT population top, which may be an equal-cost point that Powell's
        own current_point has not adopted yet."""
        if pop is not None:
            top = pop.top()
            if top is not None:
                return list(top[0]), float(top[1])
        return list(cur), cur_cost

    dirs = []
    x0, x0_cost = list(cur), cur_cost
    while True:
        if not dirs:                                         # LoopBegin 0x392a3c
            dirs = [[1.0 if i == j else 0.0 for i in range(n)] for j in range(n)]
        decreases = {}                                       # std::map @state+0x60
        for k in range(n):                                   # Loop / LineMinimizeDone
            before = cur_cost
            base_pt, base_cost = pop_top()                   # 0x334162 population->top()
            if _DBG:
                print("CYCLE x0=%s cur=%s base=%s dir=%s"
                      % (x0, cur, base_pt, ["%.4f" % t for t in dirs[k]]))
            yield from _line_minimize(tun, base_pt, base_cost, dirs[k], min_steps,
                                      population=popview,
                                      put=None if popview is not None else put)
            if popview is not None:
                # the nested run just wrote every probe it accepted into the OFAT
                # archive; LineMinimizeDone compares `population->top()` (0x393448)
                best_pt, best_cost = pop_top()
            if _DBG:
                print("   line done cur=%s cost=%.6g top=%s" % (cur, cur_cost, best_pt))
            if best_cost < cur_cost:                         # 0x393496 comisd + seta
                cur, cur_cost = list(best_pt), best_cost
            decreases[before - cur_cost] = k                 # 0x39357d operator[]
            if _DBG:
                print("   dir %d: before=%.6g after=%.6g dec=%.6g" % (k, before, cur_cost, before - cur_cost))
        if _DBG:
            print("LOOPDONE x0=%s cur=%s dec=%s k=%s dirs=%s" % (
                x0, cur, {k2: v2 for k2, v2 in sorted(decreases.items())},
                decreases[max(decreases)], [["%.3f" % t for t in d] for d in dirs]))
        if cur == x0:                                        # 0x39396d / 0x3939b1
            if _DBG:
                print("  -> stop (no progress)")
            return list(cur), cur_cost, best_pt, best_cost
        if spec_index < 0 and x0_cost > 0.0:                 # 0x3941c8 / 0x3941d9
            if min(decreases) / x0_cost > STAGNANT_COST_IMPROVEMENT:   # 0x39422b
                dirs = []                                    # 0x3942d1 restart, x0 kept
                if _DBG:
                    print("  -> stagnant restart")
                continue
        v = [cur[i] - x0[i] for i in range(n)]               # 0x3943c0
        s = float(sum(t * t for t in v))                     # 0x394430
        if s == 0.0:                                         # 0x394451
            dirs = []                                        # 0x3944f9, x0 kept
            continue
        k = decreases[max(decreases)]                        # map.rbegin()->second 0x3945ce
        while k != 0:                                        # 0x3945df .. 0x3946ab
            dirs[k] = list(dirs[k - 1])
            k -= 1
        norm = math.sqrt(s)
        dirs[0] = [t / norm for t in v]                      # 0x394750
        x0, x0_cost = list(cur), cur_cost                    # 0x39477c


def run(domain, goal, start=None, params=None):
    """Full OFATSearch chain; return the final ranking.

    Batches: screening = one tunable's levels; factorial = all 2^k corners at once;
    CCD = one batch of anti-diagonal pairs per factor; Powell = the line-search points.
    Return value: list of index-vectors, best first.
    """
    P = _params(params)
    tun = list(domain.tunables)
    n = len(tun)
    flags = list(P.get("modelParamFlags") or [False] * n)
    all_points = True                                            # injected by Start 0x357a13
    steps = P.get("design_steps") or [(1, 1)] * n
    ff = int(P.get("fractional_factor", DEFAULT_FF))             # 0x35a513
    stop_on_error = bool(P.get("stop_on_error", False))

    if start is None:
        start = [(t - 1) // 2 for t in tun]
    origin = list(start)
    if n == 0:
        return []

    # Population capacity: SensitivityOFAT::Start resizes the (shared) population on
    # three mutually exclusive paths -- 2n + 2^n + 1 (0x3b662b-0x3b6683), 2n + 1
    # (0x3b66ac-0x3b66ed) and, for the allPoints screen the OFATSearch chain uses,
    # 1 + sum(tunables[i] - 1) (0x3b6707-0x3b678d, `add %rax,-0x28` per tunable then
    # `add $0x1`).  Measured with gdb on `Population::resize`:
    #     harness OFATSearch 4 21 -> resize(81)   4 15 -> 57   4 11 -> 41
    #     harness OFATSearch 3 21 -> resize(61)   3 15 -> 43
    #     harness OFATSearch 2 21 -> resize(41)   2 11 -> 21
    # i.e. 1 + sum(tun-1).  The screen emits exactly sum(tun-1) points and
    # WaitFirstPoint re-submits the start point (+1), so the screen itself never
    # evicts: the first discardExtras happens when SubmitDone inserts mPoint
    # (sum(tun-1) + 2 entries -> the highest-cost one is dropped).  Using 2n + 2^n + 1
    # here (15 for n=3) over-evicted and lost e.g. the factor-1 levels of
    # `OFATSearch 3 21 --target 7 rosen`, which flipped OFATDone's commit from
    # (16,1,0) to (16,0,0).
    pop = Population(1 + sum(t - 1 for t in tun))

    # Driver call #0: FunctionOptimizer::run (0x3ce70a) evaluates the start point before
    # OFATSearch::Start @0x35780c builds the SensitivityOFAT sub-run.  The same point is
    # what SensitivityOFAT's WaitFirstPoint (0x3b8cc4) later re-submits, so this is the
    # ``current_point`` cost that seeds OFATDone's ranking (0x357ef5): it is NOT evaluated
    # a second time (native `harness OFATSearch 2 21 600 0 10 0 0` = 90 calls, not 91).
    costs = yield [domain.from_index(_driver_start(domain, start))]
    base_cost = float(costs[0])
    start_pt = list(origin)

    # ---- 1. screening: SensitivityOFAT(allPoints=true) ---------------------
    yield from _sweep(domain, goal, origin, tun, flags, all_points, steps, pop, stop_on_error)
    pop.insert(start_pt, base_cost)                  # WaitFirstPoint 0x3b8cc4 (last)

    def rank():
        return list(pop)

    # ---- 2. OFATDone 0x357d9c: rank factors, commit the greedy coordinate move
    if stop_on_error:
        _ranked = rank()
        if _ranked and _ranked[-1][1] != _ranked[-1][1]:       # hasErrorCost()
            return [p for p, _ in _ranked]
    index = list(origin)                                         # 0x357e7b
    keys = []
    for i in range(n):                                           # 0x357eca
        best = base_cost if base_cost is not None else float("inf")   # 0x357ef5
        best_idx = index[i]                                      # 0x357f1d
        for pt, c in rank():                                     # population cost-ascending
            if pt[i] != index[i] and _better(goal, c, best):     # 0x357fdc / 0x358014
                best, best_idx = c, pt[i]                        # 0x358043 / 0x35807c
        keys.append((best, i))                                   # 0x3580ca
        index[i] = best_idx                                      # 0x3580e9
    keys.sort()
    order = [i for _, i in keys]                                 # AlgState+0x50, 0x358109
    mpoint = list(index)                                         # 0x3581a7
    costs = yield [mpoint]                                       # SubmitDone
    pop.insert(mpoint, float(costs[0]))                          # 0x358370

    # ---- 3. SubmitDone 0x3582fe: build the 2-level fractional design
    n_extra = n - ff if n > ff else 0                            # 0x35843c
    rows = _fractional_factorial_designs(n, n_extra)             # 0x358480
    if not rows:
        raise AssertionError("point_set->size()>0")

    corners = []
    for row in rows:                                             # FactorialSubmit, FORKIDX = row
        pt = [0] * n
        for i in range(n):                                       # 0x358642
            fi = order[i]                                        # 0x358660
            if row[i]:                                           # 0x35872e
                pt[fi] = tun[fi] - 1                             # 0x358772
            else:
                pt[fi] = 0                                       # 0x358774
        corners.append(pt)
    costs = yield corners                                        # Fork<FactorialSubmit>(len(rows))
    for pt, c in zip(corners, costs):
        pop.insert(pt, c)                                        # FactorialWait 0x3588ee
    if stop_on_error and any(c != c for c in costs):
        return [p for p, _ in rank()]

    # ---- 4. BeginCentralCompositeDeisgin 0x358a10 --------------------------
    # SplitCentralCompositePoints 0x358d86 forks n-currentIdx children: FORKIDX 0 takes
    # the "currentIdx++ and re-fork" branch (0x358db9 / 0x358dec / 0x358f2f), children
    # 1..n-currentIdx-1 build the (currentIdx, currentIdx+FORKIDX) pair.  Fork walks its
    # children in ascending FORKIDX order (0x333777), so the recursion is exhausted first
    # and the pairs come out with currentIdx DESCENDING (measured on dim 4:
    # (2,3) then (1,2),(1,3) then (0,1),(0,2),(0,3)).
    if n > 1:                                                    # 0x358c0c
        for ci in range(n - 1, -1, -1):
            if tun[ci] <= 1:                                     # 0x358ac9 / 0x358dec
                continue
            pairs = []
            for f in range(1, n - ci):                           # fork index f
                if ci + f >= n:                                  # 0x359254
                    raise AssertionError("currentIdx+FORKIDX<current_point->values.size()")
                if tun[ci + f] <= 1:
                    continue
                # CreateCentralCompositePoints copies `current_point->values`
                # (0x3591be / 0x3591e9) -- and current_point is the START point that
                # OFATSearch::Start forwarded to SensitivityOFAT, not the committed
                # mPoint.  Evidence: `harness OFATSearch 4 21 600 0 10 ...` emits
                # (0,0,20,0) for pair (2,3) although the mPoint is (10,10,10,10).
                hi = list(origin)
                lo = list(origin)
                up = low = True                                  # 0x359190 / 0x359194
                if origin[ci] == tun[ci] - 1 and origin[ci + f] == 0:
                    up = False                                   # 0x359325
                else:
                    hi[ci] = tun[ci] - 1                         # 0x35937c
                    hi[ci + f] = 0                               # 0x3593c9
                if origin[ci] == 0 and origin[ci + f] == tun[ci + f] - 1:
                    low = False                                  # 0x359489
                else:
                    lo[ci] = 0                                   # 0x3594ce
                    lo[ci + f] = tun[ci + f] - 1                 # 0x359540
                if not (up or low):                              # 0x35961f
                    raise AssertionError("upperBool||lowerBool")
                if up:
                    pairs.append(hi)
                if low:
                    pairs.append(lo)
            if pairs:
                costs = yield pairs                              # CentralCompositePointsWait
                for pt, c in zip(pairs, costs):
                    pop.insert(pt, c)                            # 0x3597xx

    # ---- 5. BrentPowellBegin 0x3599c2: Powell from population->top()
    # Params{ current_point = population->top() (0x359a85), population (0x359bc5) } ->
    # Powell::Init (0x359c18) wrapped in Call<BrentPowellDone> (0x359cc3).
    best_pt, best_cost = pop.top()                               # 0x359a85 population->top()
    cur, cur_cost, best_pt, best_cost = yield from _powell(
        tun, list(best_pt), best_cost, list(best_pt), best_cost, pop,
        min_steps=int(P.get("minimum_steps_for_brent", MIN_STEPS_FOR_BRENT)))
    pop.insert(cur, cur_cost)
    rank = lambda: list(pop)                                     # noqa: E731
    # BrentPowellDone 0x359e82 -> Return
    return [p for p, _ in rank()]


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class Quad2D:
        """2-D quadratic on a 21-level grid: f = (x-0.3)^2 + 2*(y+0.5)^2."""
        def __init__(self, levels=21):
            self.tunables = [levels, levels]
            self.levels = levels
            self.lo, self.hi = -1.0, 1.0

        def from_index(self, ix):
            return tuple(self.lo + (self.hi - self.lo) * i / (self.levels - 1) for i in ix)

        def to_index(self, p):
            return [int(round((x - self.lo) / (self.hi - self.lo) * (self.levels - 1))) for x in p]

        def cost(self, ix):
            x, y = self.from_index(ix)
            return (x - 0.3) ** 2 + 2.0 * (y + 0.5) ** 2

    dom = Quad2D(21)
    start = dom.to_index((0.0, 0.0))
    print("start:", start, dom.from_index(start), "cost=%.6f" % dom.cost(start))
    g = run(dom, None, start=start, params={"fractional_factor": 5})
    batch = next(g)
    k = 0
    while True:
        k += 1
        print("batch %d %2d pts: %s" % (k, len(batch), batch[:6]))
        try:
            batch = g.send([dom.cost(p) for p in batch])
        except StopIteration as e:
            rank = e.value
            break
    print("\nfinal ranking:")
    for p in rank:
        print("   ", p, dom.from_index(p), "cost=%.6f" % dom.cost(p))
    print("best:", rank[0], dom.from_index(rank[0]), "cost=%.6f" % dom.cost(rank[0]))
