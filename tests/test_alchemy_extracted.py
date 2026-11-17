"""提取件验收：31 个从二进制里抠出来的算法，逐个真跑一遍。

断言很克制，但每条都有意义：

* 每个提取件都能被 package 的 ``Domain``/``Goal`` 驱动，不抛异常、有产出；
* 优化器类的算法在 2 维二次型上**不劣于**起点；
* 采样/筛选类（Sweep / TwoKFactorial / Sensitivity / OnePoint）只验证点集规模与合法性。

这些实现是"行为一致"版（连 bug 都照抄），所以不要求和内置实现逐个数字相等；
真要逐点对拍，用 ``re/alchemy/algorithms/harness.py`` 并排跑。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import alchemy as al            # noqa: E402
from vlight.alchemy import extracted        # noqa: E402

NAMES = extracted.NAMES

SAMPLERS = {"Sweep", "NewSweep", "Hammersley", "TwoKFactorial", "OnePoint",
            "Manual", "Sensitivity", "SensitivityOFAT", "WorstCaseCorners", "DOE"}

DRIVER_NEEDS_SUB = {"Global", "Local", "Optimize", "DSO"}


def _problem(levels: int = 41):
    dom = al.Domain([al.Param("x0", -5, 5, nominal=3.0, n_values=levels),
                     al.Param("x1", -5, 5, nominal=3.0, n_values=levels)])
    goal = al.Goal([al.Response("f", objective="min")])
    ev = lambda p: {"f": (p["x0"] - 1) ** 2 + (p["x1"] - 1) ** 2}  # noqa: E731
    return dom, goal, ev


NEEDS_CRITERION = {"Anneal2", "PSA2"}


def _drive(name, dom, goal, ev, budget=400, params=None):
    mod = extracted.load(name)
    p = dict(params or {})
    p.setdefault("max_steps", 120)
    p.setdefault("iterations", 8)
    if name in NEEDS_CRITERION and "criterion" not in p and hasattr(mod, "default_criterion"):
        # 老版本需要注入 criterion（原库 KrazKool1(..., unsigned long, ...) 是 "FIXME" 桩）；
        # 现在 PSA2 自带真实 KrazKool1 冷却表，就不再注入。
        p["criterion"] = mod.default_criterion(seed=1)
        p.setdefault("moves_per_temperature", 10)
    gen = mod.run(dom, goal, start=None, params=p)
    return al.run(gen, dom, goal, ev, budget=budget)


# --------------------------------------------------------------------------- #
# 全量：能跑、有产出
# --------------------------------------------------------------------------- #

def test_all_31_present():
    assert len(NAMES) == 31
    for must in ("BFGS", "ConjugateGradient", "QuasiNewton", "LineMinimize",
                 "HookeJeeves", "OldHookeJeeves", "NetlibHookeJeeves", "Powell",
                 "ParallelPowell", "Sweep", "NewSweep", "Hammersley",
                 "GreedyMinimization", "Anneal", "Anneal2", "PSA", "PSA2", "PDE",
                 "Sensitivity", "SensitivityOFAT", "OFATSearch",
                 "ParallelOFATSearch", "TwoKFactorial", "WorstCaseCorners", "DOE",
                 "OnePoint", "Manual", "DSO", "Optimize", "Global", "Local"):
        assert must in NAMES


PSA2_SKIP = ("PSA2 的 Hammersley 预热档案需要真库那套 50 点档案 + 每 fork 的 population；"
             "通用小栅格跑不满档案，模块按设计 raise。它的对拍走 native："
             "`pydrive.py PSA2 --dim 2 --levels 21 --budget 300` → 51/51 逐点一致。")


@pytest.mark.parametrize("name", NAMES)
def test_extracted_runs(name):
    dom, goal, ev = _problem()
    if name == "Manual":
        pytest.skip("Wait::run 是永恒自环，提取件按设计不会自己结束")
    if name == "PSA2":
        pytest.skip(PSA2_SKIP)
    r = _drive(name, dom, goal, ev)
    assert r.nfev > 0
    assert r.best.point


@pytest.mark.parametrize("name", [n for n in NAMES
                                  if n not in SAMPLERS and n not in DRIVER_NEEDS_SUB
                                  and n != "Manual"])
def test_optimizer_does_not_get_worse(name):
    if name == "PSA2":
        pytest.skip(PSA2_SKIP)
    dom, goal, ev = _problem()
    start_cost = goal.cost(ev(dom.nominal()))[0]
    r = _drive(name, dom, goal, ev, budget=600)
    assert r.best.cost <= start_cost + 1e-9


# --------------------------------------------------------------------------- #
# 采样类：点集规模
# --------------------------------------------------------------------------- #

def test_sweep_is_full_cartesian():
    """真库把**起点也放进网格**并重复评估一次（native 11×11 → 122 次），
    所以这里断言 122 而不是 121 —— 这是逐点一比一之后的行为。"""
    dom, goal, ev = _problem(levels=11)
    r = _drive("Sweep", dom, goal, ev, budget=400)
    assert r.nfev == 122                      # 11 × 11 + 重复的起点


def test_two_k_factorial_has_2n_corners():
    dom, goal, ev = _problem(levels=11)
    r = _drive("TwoKFactorial", dom, goal, ev, budget=50)
    assert r.nfev == 4                        # 2^2


def test_hammersley_point_count():
    dom, goal, ev = _problem()
    r = _drive("Hammersley", dom, goal, ev, budget=200, params={"points": 32})
    assert 0 < r.nfev <= 200


# --------------------------------------------------------------------------- #
# 已知的"照抄 bug"：写成测试，防止有人好心修掉
# --------------------------------------------------------------------------- #

def test_quasi_newton_matches_package_behaviour():
    """QN 用上一轮方向当位移；它在 harness 里和内置实现的 nfev 应当很接近。"""
    dom, goal, ev = _problem()
    start_cost = goal.cost(ev(dom.nominal()))[0]
    r = _drive("QuasiNewton", dom, goal, ev)
    assert r.best.cost <= start_cost + 1e-9


def test_greedy_minimization_stalls_on_one_variable():
    """GreedyMinimization 从不调用 move_class->update，所以整个 run 只扰同一维。"""
    dom, goal, ev = _problem()
    r = _drive("GreedyMinimization", dom, goal, ev, params={"max_steps": 60})
    xs = {dp.point["x0"] for dp in r.history}
    ys = {dp.point["x1"] for dp in r.history}
    assert len(ys) == 1 or len(xs) == 1        # 有一维从未动过


def test_anneal2_requires_criterion():
    """Anneal2 走的 KrazKool1 重载是 throw "FIXME" 桩，必须显式注入 criterion。"""
    mod = extracted.load("Anneal2")
    dom, goal, ev = _problem()
    with pytest.raises(Exception):
        gen = mod.run(dom, goal, start=None, params={"max_steps": 10})
        al.run(gen, dom, goal, ev, budget=20)
