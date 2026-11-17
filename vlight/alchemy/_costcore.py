"""Bit-exact port of Alchemy::CostFunction::computeCostImpl (0x1c6f50) and
SpecInfo::SpecInfo (0x1c4be0).

Auto-extracted from re/alchemy/spec_cost.md section 10, which was validated
against the real machine code (unicorn emulation, >2000 randomized cases,
bit-exact on cost / specCosts / specErrors / hasErrorCost / allSpecsMet).
"""
"""Bit-exact model of Alchemy::CostFunction::Implementation::computeCostImpl
(libpointstrategy.so 0x1c6f50) and SpecInfo::SpecInfo (0x1c4be0).

Constants are the exact .rodata doubles (bit patterns in comments).
"""
import math
from dataclasses import dataclass, field

A_UNMEASURABLE = 201977309.7244549          # 0x5872b0  41a813dbbb72ebc1
B_TAIL         = 201977305.7244549          # 0x5872c0  41a813dbb372ebc1  (== A - 4)
TAU            = 1.980420515885558e-08      # 0x5872b8  3e5543bd0be5296d
TOL_EPS        = 1e-20                      # 0x5872c8  3bc79ca10c924223
DBL_MAX        = 1.7976931348623157e308     # 0x5827e0  7fefffffffffffff
DISABLED_SPEC_COST = 1.0                    # 0x587260  3ff0000000000000
KNOT_UNSET     = -10000000000.0             # 0x587290 / 0x5872d0  c202a05f20000000

# Alchemy::SpecType
SPEC_INVALID, SPEC_MIN, SPEC_MAX, SPEC_LT, SPEC_GT, SPEC_RANGE, SPEC_TOL, SPEC_INFO = range(8)


def _fdiv(a, b):
    """IEEE-754 division; Python raises where the FPU returns inf/nan."""
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or b != b or a == 0.0:
            return float("nan")
        return math.copysign(float("inf"), a) * math.copysign(1.0, b)


def _exp(x):
    try:
        return math.exp(x)
    except OverflowError:
        return float("inf")


def tail_cost(r):
    """r >= 1 branch, identical op order to 0x1c7604..0x1c764c (and 0x1c77b8, 0x1c7b4c, 0x1c7d5a)."""
    return A_UNMEASURABLE - _fdiv(B_TAIL, _exp((r - 1.0) * TAU))


def spec_knots(stype, target1, target2):
    """SpecInfo::SpecInfo @0x1c4be0 -> (good, ok, bad, enabled_after_ctor)."""
    g = o = b = KNOT_UNSET
    if stype < 0 or stype > 7:
        return g, o, b, False                       # 0x1c50e1: enabled=false
    if stype in (SPEC_MIN, SPEC_MAX):               # 0x1c4dd8
        g = target1
        if target1 == 0.0:
            o, b = ((0.2, 0.4) if stype == SPEC_MIN else (-0.2, -0.4))
        elif stype == SPEC_MIN:
            o, b = (1.2 * target1, 1.4 * target1) if target1 > 0 else (0.8 * target1, 0.6 * target1)
        else:
            o, b = (0.8 * target1, 0.6 * target1) if target1 > 0 else (1.2 * target1, 1.4 * target1)
    elif stype in (SPEC_LT, SPEC_GT):               # 0x1c4f80
        o = target1                                 # good stays -1e10
        if target1 == 0.0:
            b = 0.2 if stype == SPEC_LT else -0.2
        elif stype == SPEC_LT:
            b = 1.2 * target1 if target1 > 0 else 0.8 * target1
        else:
            b = 0.8 * target1 if target1 > 0 else 1.2 * target1
    elif stype == SPEC_RANGE:                       # 0x1c5068
        g = 0.5 * (target1 + target2)
        o = 0.5 * abs(target1 - target2)
        b = max(1.5 * o, o + 0.2 * abs(g))
    elif stype == SPEC_TOL:                         # 0x1c4fd0
        if target1 == 0.0:                          # 0x1c5160 -> plain return, knots stay -1e10
            return KNOT_UNSET, KNOT_UNSET, KNOT_UNSET, True
        if target2 == 0.0:
            target2 = 0.01                          # 0x582820
        g = target1
        o = abs(target1 * target2 / 100.0)
        b = max(1.5 * o, o + 0.2 * abs(target1))
    return g, o, b, True                            # SPEC_INFO (7) returns the -1e10 triples


@dataclass
class Spec:
    name: str
    type: int
    target1: float = 0.0
    target2: float = 0.0
    weight: float = 1.0
    replication: int = 1
    enabled: bool = True
    _knots: tuple = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._knots is None:
            g, o, b, ok = spec_knots(self.type, self.target1, self.target2)
            self._knots = (g, o, b)
            self.enabled = self.enabled and ok

    @property
    def good(self):
        return self._knots[0]

    @property
    def ok(self):
        return self._knots[1]

    @property
    def bad(self):
        return self._knots[2]

    @property
    def corner_weight(self):
        """SpecInfo::weightForThisSpecCorner() @0x1c5890 = weight / (double)replication."""
        return _fdiv(self.weight, float(self.replication))


def _quad_penalty(v, g, o):
    """(v-g)^2 / o^2 with the binary's exact op order (0x1c7aab..0x1c7b12, 0x1c7924..0x1c7998)."""
    d = v - g
    return _fdiv(d * _fdiv(d, o), o)


def compute_cost_full(specs, measured, all_met=False):
    """Full 10-tuple; see compute_cost() for the public 3-tuple wrapper.

    measured: {spec_name: None | float}.  A missing key means "this spec/corner row was
    never measured" (map.find() == map.end()); None means "row present, optional empty".
    all_met: value of Implementation::all_met_cost_comp_is_set (Implementation+0x48).
    """
    n = len(specs)
    per_spec = [DISABLED_SPEC_COST] * n          # 0x1c6fbf assign(n, 1.0)
    met = [True] * n                             # *allSpecsMet out-param, init true @0x1c703c
    errs = [False] * n
    has_error = False                            # bool* 2nd arg, init false @0x1c6fa2
    if n == 0:
        return dict(cost=0.0, per_spec=per_spec, met=met, errors=errs, has_error=False,
                    all_measured=True, all_specs_met=True, n_fail=0,
                    sum_bad=0.0, sum_good=0.0, sum_werr=0.0, returned_bool=True)

    sum_bad = sum_good = sum_werr = 0.0
    n_fail = 0
    all_measured = True
    all_specs_met = True

    for i, s in enumerate(specs):
        if s.name not in measured:               # 0x1c70fd  (map lookup fails)
            all_measured = False
            continue
        v_in = measured[s.name]
        unmeasured = v_in is None
        v = 1.0 if unmeasured else v_in          # 0x1c72a0: v := 1.0 when optional empty
        W = s.corner_weight
        t = s.type
        c = gc = 0.0                             # [rbp-0x50] = sumBad component, [rbp-0x68] = sumGood component
        werr = nfail = am_clear = met_clear = False

        if t in (SPEC_LT, SPEC_GT):
            if not s.enabled:                                            # 0x1c7200
                c = gc = 1.0
                per_spec[i] = 0.0
            elif unmeasured:                                             # 0x1c7335
                has_error = errs[i] = True
                am_clear = met_clear = True
                per_spec[i] = DBL_MAX
                c = gc = A_UNMEASURABLE
            else:
                r = _fdiv(s.ok - v, s.ok - s.bad)
                per_spec[i] = r
                if r >= 1.0:                                             # 0x1c7604
                    c = gc = tail_cost(r)
                    am_clear = met_clear = True
                elif not (r >= 0.0):                                     # 0x1c7490 (r<0 or NaN)
                    c = gc = 1.0
                    nfail = True
                else:                                                    # 0x1c7758
                    c = gc = 2.0 + 2.0 * r
                    am_clear = met_clear = True
        elif t in (SPEC_MIN, SPEC_MAX):
            if not s.enabled:                                            # 0x1c74a9
                c = gc = 1.0
                per_spec[i] = 0.0
                werr = True
            elif unmeasured:                                             # 0x1c73bd
                has_error = errs[i] = True
                am_clear = met_clear = True
                per_spec[i] = DBL_MAX
                c = gc = A_UNMEASURABLE
                werr = True
            else:
                r = _fdiv(s.ok - v, s.ok - s.bad)
                per_spec[i] = r
                if r >= 1.0:                                             # 0x1c77b8
                    c = gc = tail_cost(r)
                    am_clear = met_clear = True
                    werr = True
                elif not (r >= 0.0):                                     # 0x1c7548 good-knot refine
                    r2 = _fdiv(s.good - v, s.good - s.ok)
                    if not (r2 > 1.0):                                   # 0x1c7802
                        if r2 >= 0.0:                                    # 0x1c7baf
                            c = gc = 1.0 + r2
                            am_clear = met_clear = True
                            werr = True
                        elif 0.0 > r2:                                   # 0x1c7c13
                            c = gc = _exp(0.5 * r2)
                            nfail = True
                            werr = True
                        else:                                            # 0x1c781e  (r2 is NaN)
                            c = gc = 0.0
                            werr = True
                    else:                                                # r2 > 1, c pre-set 2.0 @0x1c7599
                        c = gc = 2.0
                        am_clear = met_clear = True
                        werr = True
                else:                                                    # 0x1c79a8
                    c = gc = 2.0 + 2.0 * r
                    am_clear = met_clear = True
                    werr = True
        elif t == SPEC_RANGE:
            if not s.enabled:                                            # 0x1c7794
                c, gc, per_spec[i] = 1.0, 0.0, 0.0
                werr = True
            elif unmeasured:                                             # 0x1c76cb
                has_error = errs[i] = True
                am_clear = met_clear = True
                per_spec[i] = DBL_MAX
                c, gc = A_UNMEASURABLE, 0.0
                werr = True
            else:
                r = _fdiv(s.ok - abs(v - s.good), s.ok - s.bad)
                per_spec[i] = r
                if r >= 1.0:                                             # 0x1c7b4c
                    c, gc = tail_cost(r), 0.0
                    am_clear = met_clear = True
                    werr = True
                elif not (r >= 0.0):                                     # 0x1c7a6a
                    c = 1.0
                    nfail = True
                    gc = 1.0 if all_met else _quad_penalty(v, s.good, s.ok)
                    werr = True
                else:                                                    # 0x1c7cfd
                    c, gc = 2.0 + 2.0 * r, 0.0
                    am_clear = met_clear = True
                    werr = True
        elif t == SPEC_TOL:
            if not s.enabled:                                            # 0x1c7b28
                c, gc, per_spec[i] = 1.0, 0.0, 0.0
            elif unmeasured:                                             # 0x1c7c92
                has_error = errs[i] = True
                am_clear = met_clear = True
                per_spec[i] = DBL_MAX
                c, gc = A_UNMEASURABLE, 0.0
            else:
                den = s.ok - s.bad
                if den == 0.0 or den != den:                             # 0x1c788f: ok==bad
                    r = _fdiv(s.ok - abs(v - s.good), TOL_EPS)
                else:                                                    # 0x1c7c31
                    r = _fdiv(s.ok - abs(v - s.good), den)
                per_spec[i] = r
                if r >= 1.0:                                             # 0x1c7d5a
                    c, gc = tail_cost(r), 0.0
                    am_clear = met_clear = True
                elif not (r > 0.0):                                      # 0x1c78f1 (r<=0 or NaN)
                    c = 1.0
                    nfail = True
                    gc = 1.0 if all_met else _quad_penalty(v, s.good, s.ok)
                else:                                                    # 0x1c7dbd
                    c, gc = 2.0 + 2.0 * r, 0.0
                    am_clear = met_clear = True
        else:                                                            # 0, 7, >7
            if unmeasured:                                               # 0x1c71a6
                errs[i] = True        # NOTE: hasError is NOT set on this path
                per_spec[i] = DBL_MAX
                c, gc = A_UNMEASURABLE, 0.0
                nfail = True
            else:                                                        # 0x1c7735
                c, gc, per_spec[i] = 0.0, 0.0, 0.0
                nfail = True

        sum_bad += W * c
        sum_good += W * gc
        if werr:
            sum_werr += W
        if nfail:
            n_fail += 1
        if am_clear:
            all_measured = False
        if met_clear:
            all_specs_met = False
            met[i] = False

    cost = sum_good if all_measured else (n - n_fail) + sum_werr + sum_bad
    return dict(cost=cost, per_spec=per_spec, met=met, errors=errs, has_error=has_error,
                all_measured=all_measured, all_specs_met=all_specs_met, n_fail=n_fail,
                sum_bad=sum_bad, sum_good=sum_good, sum_werr=sum_werr,
                returned_bool=all_measured)


def compute_cost(specs, measured, all_met=False):
    """(total, per_spec, met_flags) - the requested signature.

    total      == 0x1c6f50 return value (pair<double,bool>::first)
    per_spec[i] == specCosts[i]  (the *ratio* r, or DBL_MAX / 0.0 / 1.0 as filled by the code)
    met_flags[i] == "spec i did not clear the allSpecsMet out-param" (i.e. it was not
                    unmeasurable and did not land in an r>=0 branch).  all(met_flags) equals
                    the `bool* allSpecsMet` output parameter of computeCostImpl.

    The bool *returned* by the pair is `all_measured` ("every spec index had a present,
    non-empty measurement") - it is NOT all(met_flags); use compute_cost_full()["returned_bool"].
    """
    d = compute_cost_full(specs, measured, all_met)
    return d["cost"], d["per_spec"], d["met"]
