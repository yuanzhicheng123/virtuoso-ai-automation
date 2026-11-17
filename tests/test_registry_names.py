"""真库注册名覆盖：native harness 实测的 `Alchemy::Factory::names()` 全部 58 条。

这些名字是从**真实 libalchemy.so** 里 `FunctionOptimizer::run` 开头那句
`Factory::names()` 打印出来的（见 `re/alchemy/native/REPORT.md`），
不是从字符串表猜的。我们的 registry 必须能解析全部。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import alchemy as al  # noqa: E402

REAL_NAMES = [
    "Sweep",
    "Hammersley Sampling",
    "Simulated Annealing",
    "Parallel Simulated Annealing",
    "Greedy Minimization",
    "Hooke and Jeeves",
    "Powell Loop",
    "Simulated Annealing - NeoCircuit",
    "Parallel Simulated Annealing - NeoCircuit",
    "Local Optimization",
    "Global Optimization",
    "Exhaustive and Brent Line Minimization",
    "Conjugate Gradient (Polak-Ribiere)",
    "Sensitivity",
    "Netlib Hooke and Jeeves",
    "Old Hooke and Jeeves",
    "Parallel Powell Loop",
    "Quasi-Newton",
    "Optimization",
    "OFATSensitivity",
    "Flexible Sweep Algorithm",
    "Manual Optimization",
    "Broyden-Fletcher-Goldfarb-Shanno",
    "OFATSearch",
    "ParallelOFATSearch",
    "Parallel Differential Evolution",
    "WorstCaseCorners",
    "DOE",
    "TwoKFactorial",
    "Design Space Optimization",
    "Anneal",
    "Anneal2",
    "BFGS",
    "Conjugate Gradient",
    "ConjugateGradient",
    "DSO",
    "Global",
    "GreedyMinimization",
    "Hammersley",
    "HookeJeeves",
    "LineMinimize",
    "Local",
    "Local Optimization - Parallel Powell",
    "Manual",
    "NetlibHookeJeeves",
    "NewSweep",
    "OldHookeJeeves",
    "OnePoint",
    "Optimize",
    "PDE",
    "PSA",
    "PSA2",
    "ParallelPowell",
    "Powell",
    "QuasiNewton",
    "Sampling",
    "SensitivityOFAT",
    "Single Run, Sweeps and Corners",
]


@pytest.mark.parametrize("name", REAL_NAMES)
def test_every_real_name_resolves(name):
    short = al.resolve(name)
    assert short in al.REGISTRY


def test_registry_covers_all_real_names():
    missing = [n for n in REAL_NAMES if not _resolves(n)]
    assert missing == []


def _resolves(name):
    try:
        al.resolve(name)
        return True
    except KeyError:
        return False


def test_create_works_for_short_names():
    dom = al.Domain([al.Param("x", 0, 10, step=1)])
    goal = al.Goal([al.Response("f", objective="min")])
    gen = al.create("BFGS", dom, goal)
    assert hasattr(gen, "send")
