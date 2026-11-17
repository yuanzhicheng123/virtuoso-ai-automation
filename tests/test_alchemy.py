"""alchemy 引擎测试：代价函数语义 + 各优化算法 + ask/tell 驱动。

代价函数的期望值来自逆向出来的常数（DISABLED_SPEC_COST / COST_BEYOND_SLOPE /
UNMEASURABLE_COST），算法的期望值来自在解析解上的收敛行为。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import alchemy as al  # noqa: E402


# --------------------------------------------------------------------------- #
# 参数域
# --------------------------------------------------------------------------- #

def test_domain_projection_continuous_and_discrete():
    dom = al.Domain([
        al.Param("W", 1e-6, 10e-6),
        al.Param("N", 1, 10, step=2),
        al.Param("top", values=[1, 2, 4, 8]),
    ])
    p = dom.point([20e-6, 4.4, 3.6])
    assert p["W"] == pytest.approx(10e-6)      # 夹到边界
    assert p["N"] == pytest.approx(5)          # 1 + 2*2
    assert p["top"] == pytest.approx(4)        # 最近候选

    u = dom.to_unit([1e-6, 1, 1])
    assert u == pytest.approx([0.0, 0.0, 0.0])
    assert dom.from_unit([1.0, 1.0, 1.0])[0] == pytest.approx(10e-6)


def test_nominal_roundtrip():
    dom = al.Domain([al.Param("a", 0.0, 2.0, nominal=0.5), al.Param("b", -1.0, 1.0)])
    assert dom.vector(dom.point(dom.from_unit(dom.unit()))) == pytest.approx([0.5, 0.0])


# --------------------------------------------------------------------------- #
# 代价函数（Alchemy::CostFunction 的语义）
# --------------------------------------------------------------------------- #

def test_open_ended_maximize_keeps_improving():
    """max + ideal：越接近/超过理想值 cost 越低（r<0 走 good 结点）。"""
    g = al.Goal([al.Response("gain", objective="max", target=60.0)])
    c50 = g.cost({"gain": 50.0})[0]
    c60 = g.cost({"gain": 60.0})[0]
    c70 = g.cost({"gain": 70.0})[0]
    c90 = g.cost({"gain": 90.0})[0]
    assert not g.cost({"gain": 50.0})[2]
    assert g.cost({"gain": 70.0})[2] is True
    assert c70 < c60 < c50
    assert c90 < c70                      # 越过理想值继续变好


def test_close_ended_three_knot_model():
    """gt：ok=target=60、bad=0.8·target=48；v=ok 恰好是 3 分（regime switch），
    越过 ok 才 jump 到 1 分并算 met。"""
    g = al.Goal([al.Response("pm", constraint=("gt", 60.0))])
    assert g.cost({"pm": 60.0})[0] == pytest.approx(3.0)      # r=0 -> 2+2r，另加常数项
    assert g.cost({"pm": 72.0})[0] == pytest.approx(1.0)      # r<0 -> lt/gt 只记 1.0
    assert g.cost({"pm": 72.0})[2] is True
    assert g.cost({"pm": 60.0})[2] is False
    assert g.cost({"pm": 48.0})[0] == pytest.approx(5.0)      # r=1 -> 尾部饱和起始
    assert g.cost({"pm": 40.0})[0] > 5.0
    # 尾部 c = A − B/exp((r−1)τ)，τ=1.98e-8 极小 => 实际是 **线性 4r**
    assert g.cost({"pm": 0.0})[0] == pytest.approx(1.0 + 4.0 * 5.0, rel=1e-6)


def test_range_window_semantics():
    """range=tol 都用 |v−good|/ok 归一；区间内 met，中点最便宜。"""
    g = al.Goal([al.Response("vout", constraint=("range", 0.9, 1.1))])
    assert g.cost({"vout": 1.0})[2] is True
    assert g.cost({"vout": 0.9})[2] is True
    assert g.cost({"vout": 1.1})[2] is False        # 边界值落到 r>=0 侧
    assert g.cost({"vout": 1.0})[0] < g.cost({"vout": 0.9})[0]
    assert g.cost({"vout": 0.8})[0] > g.cost({"vout": 1.0})[0]
    assert g.cost({"vout": 1.3})[0] > g.cost({"vout": 1.0})[0]


def test_weight_scales_the_spec_term():
    g1 = al.Goal([al.Response("p", constraint=("lt", 1e-3), weight=1)])
    g4 = al.Goal([al.Response("p", constraint=("lt", 1e-3), weight=4)])
    c1 = g1.cost({"p": 2e-3})[0]
    c4 = g4.cost({"p": 2e-3})[0]
    assert c4 > c1
    assert (c4 - 1.0) == pytest.approx(4 * (c1 - 1.0), rel=1e-6)


def test_info_spec_still_counts_a_row():
    """库本身**不特殊处理** type 7：ADE 在建 cost function 前会把 info 规格剔掉。
    这里只验证“多一行就多 1 分”的聚合行为。"""
    g = al.Goal([al.Response("note", info=True),
                 al.Response("p", constraint=("lt", 1e-3))])
    g0 = al.Goal([al.Response("p", constraint=("lt", 1e-3))])
    assert g.cost({"note": 1e9, "p": 5e-4})[0] == pytest.approx(
        g0.cost({"p": 5e-4})[0] + 1.0)


def test_unmeasurable_spec_sets_sentinel_cost():
    g = al.Goal([al.Response("x", constraint=("gt", 1.0))])
    cost, _, met = g.cost({"x": None})
    assert cost > al.UNMEASURABLE_COST          # A + (n−nfail) + w
    assert met is False
    # NaN 是“测到了但是 NaN”：走 r>=1 的否定分支 -> c=1.0、算失败，
    # 与 None（走 unmeasured 哨兵）完全不同。
    assert g.cost({"x": float("nan")})[0] == pytest.approx(1.0)


def test_disabled_spec_cost_constant():
    assert al.DISABLED_SPEC_COST == 1.0
    assert al.UNMEASURABLE_COST == pytest.approx(4.0 / al.COST_BEYOND_SLOPE)


# --------------------------------------------------------------------------- #
# 算法：解析解
# --------------------------------------------------------------------------- #

def _run(gen, dom, goal, ev, budget):
    return al.run(gen, dom, goal, ev, budget=budget)


def _quad_domain(n_values=201):
    return al.Domain([al.Param("x", -5, 5, nominal=4.0, n_values=n_values),
                      al.Param("y", -5, 5, nominal=-3.0, n_values=n_values)])


def _quad(p):
    return {"f": (p["x"] - 1.0) ** 2 + (p["y"] - 2.0) ** 2}


@pytest.mark.parametrize("make", [
    lambda d, g: al.bfgs(d, g),
    lambda d, g: al.conjugate_gradient(d, g),
    lambda d, g: al.powell(d, g),
    lambda d, g: al.hooke_jeeves(d, g),
])
def test_algorithms_hit_quadratic_minimum(make):
    dom = _quad_domain()
    goal = al.Goal([al.Response("f", objective="min")])
    r = _run(make(dom, goal), dom, goal, _quad, budget=400)
    assert r.best.responses["f"] < 5e-3
    assert dom.vector(r.best.point) == pytest.approx([1.0, 2.0], abs=0.1)


def test_powell_solves_rosenbrock():
    def rosen(p):
        return {"f": (1 - p["x"]) ** 2 + 100 * (p["y"] - p["x"] ** 2) ** 2}

    # 引擎在整数格上跑，档位越细越接近连续最优；这里取 501 档（步长 0.008）
    dom = al.Domain([al.Param("x", -2, 2, nominal=-1.2, n_values=501),
                     al.Param("y", -1, 3, nominal=1.0, n_values=501)])
    goal = al.Goal([al.Response("f", objective="min")])
    r = _run(al.powell(dom, goal), dom, goal, rosen, budget=900)
    assert r.best.responses["f"] < 1e-3


def test_sweep_enumerates_cartesian_grid():
    dom = al.Domain([al.Param("a", 0, 4, step=1), al.Param("b", 0, 4, step=1)])
    goal = al.Goal([al.Response("f", objective="min")])

    def f3(p):
        return {"f": (p["a"] - 1) ** 2 + (p["b"] - 3) ** 2}

    r = _run(al.sweep(dom, goal), dom, goal, f3, budget=100)
    assert r.nfev == 25                       # 5 × 5 全笛卡尔
    assert dom.vector(r.best.point) == [1.0, 3.0]
    assert len({(d.point["a"], d.point["b"]) for d in r.history}) == 25

    r2 = _run(al.new_sweep(dom, goal), dom, goal, f3, budget=100)
    assert r2.nfev == 25                      # 里程表同样遍历全部格点
    assert r2.best.responses["f"] == 0.0


def test_hammersley_covers_the_box():
    dom = _quad_domain()
    goal = al.Goal([al.Response("f", objective="min")])
    r = _run(al.hammersley(dom, goal, n_points=64), dom, goal, _quad, budget=200)
    assert r.nfev == 64
    xs = [dp.point["x"] for dp in r.history]
    assert min(xs) < -3 and max(xs) > 3
    assert r.best.responses["f"] < 1.0


def test_anneal_finds_basin_of_quadratic():
    dom = _quad_domain()
    goal = al.Goal([al.Response("f", objective="min")])
    r = _run(al.anneal(dom, goal, population=8, max_steps=200), dom, goal, _quad, budget=500)
    assert r.best.responses["f"] < 0.5


# --------------------------------------------------------------------------- #
# 驱动：预算 / 早停 / 记账
# --------------------------------------------------------------------------- #

def test_run_respects_budget_and_records_history():
    dom = _quad_domain()
    goal = al.Goal([al.Response("f", objective="min")])
    r = _run(al.hooke_jeeves(dom, goal), dom, goal, _quad, budget=17)
    assert r.nfev <= 17 + 2 * len(dom) + 1      # 允许最后一批越界
    assert len(r.history) == r.nfev
    assert all(dp.point for dp in r.history)


def test_meet_all_goals_stops_early():
    dom = al.Domain([al.Param("x", -5, 5, nominal=-4.0)])
    goal = al.Goal([al.Response("v", constraint=("gt", 0.0))])

    def ev(p):
        return {"v": p["x"]}

    r = _run(al.hooke_jeeves(dom, goal), dom, goal, ev, budget=200)
    assert r.met is True
    r2 = al.run(al.hooke_jeeves(dom, goal), dom, goal, ev, budget=200, meet_all_goals=True)
    assert r2.met is True
    assert r2.nfev <= 20        # 一旦越过 0 就停


# --------------------------------------------------------------------------- #
# 端到端：一个“有规格的尺寸优化”
# --------------------------------------------------------------------------- #

def test_neocircuit_global_meets_specs():
    """一个 RC 低通的“尺寸优化”：增益要够、功耗要小、带宽要落在区间内。"""
    dom = al.Domain([
        al.Param("R", 1e3, 100e3, nominal=10e3),
        al.Param("C", 1e-12, 100e-12, nominal=10e-12),
    ])
    goal = al.Goal([
        al.Response("bw", constraint=("range", 0.8e6, 1.2e6)),
        al.Response("power", objective="min", weight=2),
    ])

    def ev(p):
        r, c = p["R"], p["C"]
        bw = 1.0 / (2 * math.pi * r * c)
        power = 1e-3 + (10e3 / r) * 1e-4
        return {"bw": bw, "power": power}

    start_cost = goal.cost(ev(dom.nominal()))[0]
    r = al.run(al.neocircuit_global(dom, goal, population=8, iterations=25),
               dom, goal, ev, budget=800, meet_all_goals=False)
    assert r.best.cost < start_cost
    assert 0.7e6 < r.best.responses["bw"] < 1.3e6
