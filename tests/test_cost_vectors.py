"""用**机器码实测向量**验收代价函数（不是自证）。

向量来源：`re/alchemy/spec_cost.md` —— 那份报告把 `libpointstrategy.so` 映射进 unicorn、
用**真实的 SpecInfo 构造函数**造对象、`exp` 挂到 libm，跑出下面这些数。我们这边只是
把同样的输入喂给 `vlight.alchemy._costcore`，逐位比对。

两处**文档自身的不一致**（本文件按"代码 + 2000 组差分测试"为准，并把矛盾写进注释）：

1. §2.7 的 `type6 t1=100 t2=0 -> ok=0.001 bad=2.001`：`bad = ok + 0.2·|t1|` 反解出
   `|t1| = 10`，说明该行是用 `t1=10` 算的却标成 100（`0x582820 = 0.01` 的替换值也支持
   `10·0.01/100 = 0.001`）。本文件用 `t1=10` 建向量。
2. §4.6 的 `type1 t1=inf, v=inf -> cost = A + 2`：与 §10 的实现（`r>=1` 才走尾部，
   NaN 落到 good-knot 分支）矛盾；而 §10 那份实现做过 >2000 组含 ±inf/NaN 的位级差分
   且 0 失配，所以这里以 §10 为准，并把这个向量单独标成 `DOC_INCONSISTENT`。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight.alchemy import _costcore as C  # noqa: E402

A = C.A_UNMEASURABLE
INF = math.inf


# --------------------------------------------------------------------------- #
# §2.7 构造函数实测：spec -> (good, ok, bad)
# --------------------------------------------------------------------------- #

KNOTS = [
    (C.SPEC_MIN, 10.0, 0.0, (10.0, 12.0, 14.0)),
    (C.SPEC_MIN, -10.0, 0.0, (-10.0, -8.0, -6.0)),
    (C.SPEC_MIN, 0.0, 0.0, (0.0, 0.2, 0.4)),
    (C.SPEC_MAX, 10.0, 0.0, (10.0, 8.0, 6.0)),
    (C.SPEC_MAX, -10.0, 0.0, (-10.0, -12.0, -14.0)),
    (C.SPEC_MAX, 0.0, 0.0, (0.0, -0.2, -0.4)),
    (C.SPEC_LT, 10.0, 0.0, (-1e10, 10.0, 12.0)),
    (C.SPEC_LT, -10.0, 0.0, (-1e10, -10.0, -8.0)),
    (C.SPEC_LT, 0.0, 0.0, (-1e10, 0.0, 0.2)),
    (C.SPEC_GT, 10.0, 0.0, (-1e10, 10.0, 8.0)),
    (C.SPEC_GT, -10.0, 0.0, (-1e10, -10.0, -12.0)),
    (C.SPEC_GT, 0.0, 0.0, (-1e10, 0.0, -0.2)),
    (C.SPEC_RANGE, 10.0, 20.0, (15.0, 5.0, 8.0)),
    (C.SPEC_RANGE, 10.0, 0.0, (5.0, 5.0, 7.5)),
    (C.SPEC_RANGE, -1.0, 5.0, (2.0, 3.0, 4.5)),
    (C.SPEC_RANGE, 0.0, 0.0, (0.0, 0.0, 0.0)),
    (C.SPEC_TOL, 10.0, 5.0, (10.0, 0.5, 2.5)),       # o=|t1·t2/100|=0.5
    (C.SPEC_TOL, 100.0, 5.0, (100.0, 5.0, 25.0)),
    (C.SPEC_TOL, 10.0, 0.0, (10.0, 0.001, 2.001)),    # t2==0 -> 0.01
    (C.SPEC_TOL, 0.0, 0.0, (-1e10, -1e10, -1e10)),    # ctor 提前返回
    (C.SPEC_INFO, 0.0, 0.0, (-1e10, -1e10, -1e10)),
    (C.SPEC_INVALID, 0.0, 0.0, (-1e10, -1e10, -1e10)),
]


@pytest.mark.parametrize("stype,t1,t2,want", KNOTS,
                         ids=[f"{s}-{t1}-{t2}" for s, t1, t2, _ in KNOTS])
def test_knots_match_machine(stype, t1, t2, want):
    s = C.Spec(name="x", type=stype, target1=t1, target2=t2)
    assert (s.good, s.ok, s.bad) == pytest.approx(want)


# --------------------------------------------------------------------------- #
# §4.5 r2==0 的 regime 跳变（这是最容易写错的地方）
# --------------------------------------------------------------------------- #

def test_regime_jump_at_good():
    s = C.Spec(name="x", type=C.SPEC_MIN, target1=10.0)     # g=10, o=12, b=14
    lo = C.compute_cost_full([s], {"x": 9.999}, False)
    at = C.compute_cost_full([s], {"x": 10.0}, False)
    hi = C.compute_cost_full([s], {"x": 10.001}, False)

    assert lo["cost"] == pytest.approx(math.exp(0.5 * -0.0005), rel=1e-15)
    assert at["cost"] == pytest.approx(3.0)                  # (n−nf)+Σw+Σw·c = 1+1+1
    assert hi["cost"] == pytest.approx(3.0005, rel=1e-12)   # 表里 5 位小数的写法

    assert lo["all_measured"] is True                        # r2<0 不清 regime
    assert at["all_measured"] is False
    assert hi["all_measured"] is False


# --------------------------------------------------------------------------- #
# §4.6 退化情形（实测）
# --------------------------------------------------------------------------- #

def test_degenerate_range_nan_and_inf():
    s = C.Spec(name="x", type=C.SPEC_RANGE, target1=0.0, target2=0.0)
    assert math.isnan(C.compute_cost_full([s], {"x": 0.0}, False)["cost"])
    assert math.isinf(C.compute_cost_full([s], {"x": 1.0}, False)["cost"])


def test_replication_zero_makes_cost_infinite():
    s = C.Spec(name="x", type=C.SPEC_MIN, target1=10.0, replication=0)
    assert math.isinf(C.compute_cost_full([s], {"x": 9.0}, False)["cost"])


def test_null_measurement_is_sentinel_not_inf():
    """缺键 != 键存在但空 optional：前者不参与，后者是 A 级哨兵。"""
    s = C.Spec(name="x", type=C.SPEC_GT, target1=1.0)
    missing = C.compute_cost_full([s], {}, False)
    empty = C.compute_cost_full([s], {"x": None}, False)
    assert missing["all_measured"] is False and missing["per_spec"] == [1.0]
    assert empty["cost"] > A and empty["met"] == [False]


# --------------------------------------------------------------------------- #
# 文档自身不一致的那一行：只记录，不当成失败
# --------------------------------------------------------------------------- #

@pytest.mark.xfail(reason="spec_cost.md §4.6 的 type1/inf 一行与 §10 实现（已过 2000 组位级差分）矛盾",
                   strict=False)
def test_doc_inconsistent_inf_row():
    s = C.Spec(name="x", type=C.SPEC_MIN, target1=INF)
    assert C.compute_cost_full([s], {"x": INF}, False)["cost"] == pytest.approx(A + 2)
