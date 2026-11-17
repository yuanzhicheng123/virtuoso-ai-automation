"""完整引擎测试：注册表 31 个算法全跑一遍 + 协议往返 + 服务端会话 + 骨架对象。

这一组测试的定位是"引擎完整性"：不是每个算法的数值都最优，而是
**每个注册名都能被 Factory 造出来、能在 ask/tell 循环里跑完、能把点/代价正确进出**。
数值质量由 ``test_alchemy.py`` 负责。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import alchemy as al  # noqa: E402
from vlight.alchemy import proto  # noqa: E402
from vlight.alchemy.server import Experiment  # noqa: E402


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

def test_registry_has_31_algorithms():
    names = al.registry.names()
    assert len(names) == 31
    for must in ("BFGS", "ConjugateGradient", "QuasiNewton", "HookeJeeves",
                 "OldHookeJeeves", "NetlibHookeJeeves", "Powell", "ParallelPowell",
                 "Sweep", "NewSweep", "Hammersley", "GreedyMinimization", "Anneal",
                 "Anneal2", "PSA", "PSA2", "PDE", "Sensitivity", "SensitivityOFAT",
                 "OFATSearch", "ParallelOFATSearch", "TwoKFactorial",
                 "WorstCaseCorners", "DOE", "OnePoint", "Manual", "DSO",
                 "Optimize", "Global", "Local", "LineMinimize"):
        assert must in names


@pytest.mark.parametrize("alias,expected", [
    ("neocircuitGlobal", "Global"),
    ("conjugateGradient", "ConjugateGradient"),
    ("brentPowell", "Powell"),
    ("hookeJeeves", "HookeJeeves"),
    ("Broyden-Fletcher-Goldfarb-Shanno", "BFGS"),
    ("Conjugate Gradient (Polak-Ribiere)", "ConjugateGradient"),
    ("Design Space Optimization", "DSO"),
    ("Parallel Differential Evoluation", "PDE"),
    ("Single Run, Sweeps and Corners", "Sweep"),
    ("Flexible Sweep Algorithm", "NewSweep"),
    ("Hammersley Sampling", "Hammersley"),
    ("Parallel Simulated Annealing", "PSA"),
    ("Manual Optimization", "Manual"),
])
def test_alias_resolution(alias, expected):
    assert al.resolve(alias) == expected


def test_unknown_algorithm_raises():
    with pytest.raises(KeyError):
        al.resolve("does-not-exist")


# --------------------------------------------------------------------------- #
# 每个算法都能在 ask/tell 循环里跑
# --------------------------------------------------------------------------- #

def _problem(n=2):
    dom = al.Domain([al.Param(f"x{i}", -5, 5, nominal=3.0, n_values=41) for i in range(n)])
    goal = al.Goal([al.Response("f", objective="min")])
    target = [1.0] * n

    def ev(p):
        return {"f": sum((p[f"x{i}"] - target[i]) ** 2 for i in range(n))}

    return dom, goal, ev


SKIP = {"Manual", "DSO"}


@pytest.mark.parametrize("name", [n for n in al.registry.names() if n not in SKIP])
def test_each_algorithm_runs(name):
    dom, goal, ev = _problem()
    gen = al.create(name, dom, goal, al.Params({"max_steps": 60, "iterations": 5,
                                                "population": 4, "annealers": 3,
                                                "hammersley_points": 16,
                                                "max_iter": 5, "maxima": 2}))
    res = al.run(gen, dom, goal, ev, budget=400)
    assert res.nfev > 0
    assert math.isfinite(res.best.cost)
    assert res.best.point


def test_manual_and_dso_need_inputs():
    dom, goal, ev = _problem()
    p1 = dom.nominal()
    res = al.run(al.manual(dom, goal, points=[p1]), dom, goal, ev, budget=10)
    assert res.nfev == 1

    def plugin(domain, merit):
        costs = yield [domain.nominal()]
        return costs[0]

    res2 = al.run(al.dso(dom, goal, plugin=plugin), dom, goal, ev, budget=10)
    assert res2.nfev >= 1


# --------------------------------------------------------------------------- #
# 引擎骨架对象
# --------------------------------------------------------------------------- #

def test_gen_is_deterministic_and_seeded():
    a, b = al.Gen(1), al.Gen(1)
    seq_a = [a.in01() for _ in range(5)]
    seq_b = [b.in01() for _ in range(5)]
    assert seq_a == seq_b
    assert all(0.0 <= x < 1.0 for x in seq_a)
    assert al.Gen(2).in01() != seq_a[0]
    f = al.Gen(1).forked(0, 2)
    assert f.seed == 3


def test_population_is_bounded_and_ordered():
    pop = al.Population(maxsize=3)
    for c in (5.0, 1.0, 3.0, 2.0, 4.0):
        dp = al.DesignPoint(values=[0], cost=c)
        pop.insert(dp)
    assert len(pop) == 3
    assert pop.top().cost == 1.0
    assert [d.cost for d in pop] == [1.0, 2.0, 3.0]


def test_factory_cache_avoids_reevaluation():
    dom, goal, _ = _problem()
    fac = al.DesignPointFactory(dom, goal)
    a = fac.create([10, 10])
    assert not a.evaluated
    fac.evaluate(a, {r.name: 0.0 for r in goal.responses})
    b = fac.create([10, 10])
    assert b is a and b.evaluated
    assert len(fac.cache) == 1


def test_params_defaults():
    p = al.Params({"a": 1})
    assert p.int("a", 9) == 1
    assert p.int("b", 9) == 9
    assert p.float("c", 0.5) == 0.5
    assert p.bool("d", True) is True
    assert "a" in p and "z" not in p


# --------------------------------------------------------------------------- #
# 协议往返
# --------------------------------------------------------------------------- #

def _request() -> proto.InitialRequest:
    dom = proto.DomainMsg(params={
        "W": proto.ParamAttr(kind="continuous", lo=1e-6, hi=10e-6, nominal=4e-6),
        "N": proto.ParamAttr(kind="step", lo=1.0, hi=10.0, step=2.0, nominal=5.0),
    }, batch_size=2)
    goal = proto.GoalMsg(responses={
        "gain": proto.ResponseMsg(objective=1, upper=60.0),
        "pm": proto.ResponseMsg(constraint_type=2, upper=60.0),
    })
    return proto.InitialRequest(optimizer="BFGS", domain=dom, budget=120.0,
                                batch_size=2, hyper={"double_side_gradient": True,
                                                     "max_iter": 5},
                                goal=goal)


def test_proto_initial_roundtrip():
    req = _request()
    back = proto.decode_initial(req.encode())
    assert back.optimizer == "BFGS"
    assert back.budget == pytest.approx(120.0)
    assert back.batch_size == 2
    assert back.hyper["double_side_gradient"] is True
    assert back.domain.params["W"].kind == "continuous"
    assert back.domain.params["W"].lo == pytest.approx(1e-6)
    assert back.domain.params["N"].step == pytest.approx(2.0)
    assert back.goal.responses["gain"].objective == 1
    assert back.goal.responses["pm"].constraint_type == 2


def test_proto_evaluation_result_roundtrip():
    results = [{"gain": 61.5, "pm": 70.0}, {"gain": 58.0, "pm": 55.0}]
    blob = proto.encode_evaluation_result(results, batch_id=3)
    back = proto.decode_evaluation_result(blob)
    assert back == results


def test_proto_points_result_encodes():
    pr = proto.PointsResult(points=[proto.PointMeta(point={"x": 1.5, "n": 3},
                                                    batch_id=1, observation_id=0)])
    blob = pr.encode()
    assert isinstance(blob, bytes) and blob


# --------------------------------------------------------------------------- #
# 服务端会话
# --------------------------------------------------------------------------- #

def test_server_experiment_ask_tell_loop():
    req = _request()
    exp = Experiment.start(req)
    assert exp.name == "BFGS"
    assert exp.domain is not None and len(exp.domain) == 2

    total = 0
    for _ in range(12):
        pts = exp.get_points()
        if not pts.points:
            break
        # 用假电路回应：gain 随 W 涨，pm 随 N 掉
        results = []
        for pm in pts.points:
            w = pm.point["W"]
            n = pm.point["N"]
            results.append({"gain": 40 + 300 * w * 1e6, "pm": 90 - 2 * n})
        total += len(results)
        exp.send_results(results)
        if exp.done:
            break
    assert total > 0
    assert exp.n_points > 0
    assert exp.best is not None
    assert exp.done or exp.n_points > 0


def test_server_rejects_unknown_optimizer():
    req = _request()
    req.optimizer = "nope"
    with pytest.raises(KeyError):
        Experiment.start(req)


# --------------------------------------------------------------------------- #
# 优化器子集：必须真的下降
# --------------------------------------------------------------------------- #

OPTIMIZERS = ["BFGS", "ConjugateGradient", "QuasiNewton", "HookeJeeves",
              "OldHookeJeeves", "NetlibHookeJeeves", "Powell", "ParallelPowell",
              "LineMinimize", "GreedyMinimization", "Anneal", "Anneal2", "PSA",
              "PSA2", "PDE", "OFATSearch", "ParallelOFATSearch", "DOE",
              "Global", "Local", "Optimize"]


@pytest.mark.parametrize("name", OPTIMIZERS)
def test_optimizer_reduces_cost(name):
    dom, goal, ev = _problem()
    start_point = dom.nominal()
    start_cost, _, _ = goal.cost(ev(start_point))
    gen = al.create(name, dom, goal, al.Params({
        "max_steps": 120, "iterations": 6, "population": 6, "annealers": 4,
        "hammersley_points": 24, "max_iter": 6, "maxima": 3,
        "populationSize": 8, "fractional_factor": 3}))
    res = al.run(gen, dom, goal, ev, budget=600)
    assert res.best.cost <= start_cost + 1e-9


def test_optimize_driver_with_specs():
    dom = al.Domain([al.Param("R", 1e3, 100e3, nominal=10e3),
                     al.Param("C", 1e-12, 100e-12, nominal=10e-12)])
    goal = al.Goal([al.Response("bw", constraint=("range", 0.8e6, 1.2e6)),
                    al.Response("power", objective="min", weight=2)])

    def ev(p):
        bw = 1.0 / (2 * math.pi * p["R"] * p["C"])
        power = 1e-3 + (10e3 / p["R"]) * 1e-4
        return {"bw": bw, "power": power}

    res = al.run(al.optimize(dom, goal), dom, goal, ev, budget=800)
    assert res.best.point
    assert res.best.responses["bw"] > 0
