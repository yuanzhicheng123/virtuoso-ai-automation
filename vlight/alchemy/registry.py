"""算法注册表 —— ``Alchemy::Factory``。

C++ 侧：`create 0x3d76d8` 精确匹配 map，`registerFactoryFunction 0x3d7440`、
`registerNumberedAlgorithm 0x3d73ba`；键名就是 `visible_name`。字符串表里能读到
**31 个长名 + 31 个短别名**，短名就是 ``algs/*.cpp`` 那一套（BFGS、Powell、PSA…），
长名是人类可读形式（"Conjugate Gradient (Polak-Ribiere)"、"Design Space Optimization"、
"Parallel Differential Evoluation"[原文拼写]、"Global Optimization"…）。
"""
from __future__ import annotations

from typing import Callable

from .lattice import Domain
from .spec import Goal
from .core import Params
from . import algs_local, algs_direct, algs_stoch, algs_doe, drivers


# 短名 -> (函数, 说明)
REGISTRY: dict[str, tuple[Callable, str]] = {
    # 局部 / 梯度
    "BFGS": (algs_local.bfgs, "Broyden-Fletcher-Goldfarb-Shanno"),
    "ConjugateGradient": (algs_local.conjugate_gradient, "Conjugate Gradient (Polak-Ribiere)"),
    "QuasiNewton": (algs_local.quasi_newton, "Quasi-Newton"),
    "LineMinimize": (algs_local.line_search_algo, "Line Minimize"),
    # 直接搜索
    "HookeJeeves": (algs_direct.hooke_jeeves, "Hooke-Jeeves"),
    "OldHookeJeeves": (algs_direct.old_hooke_jeeves, "Hooke-Jeeves (legacy name)"),
    "NetlibHookeJeeves": (algs_direct.netlib_hooke_jeeves, "Netlib Hooke-Jeeves"),
    "Powell": (algs_direct.powell, "Powell"),
    "ParallelPowell": (algs_direct.parallel_powell, "Parallel Powell"),
    "Sweep": (algs_direct.sweep, "Single Run, Sweeps and Corners"),
    "NewSweep": (algs_direct.new_sweep, "New Sweep"),
    "Hammersley": (algs_direct.hammersley, "Hammersley Sampling"),
    # 随机
    "GreedyMinimization": (algs_stoch.greedy_minimization, "Greedy Minimization"),
    "Anneal": (algs_stoch.anneal, "Simulated Annealing"),
    "Anneal2": (algs_stoch.anneal2, "Simulated Annealing 2"),
    "PSA": (algs_stoch.psa, "Parallel Simulated Annealing"),
    "PSA2": (algs_stoch.psa2, "Parallel Simulated Annealing 2"),
    "PDE": (algs_stoch.pde, "Parallel Differential Evoluation"),
    # DoE
    "Sensitivity": (algs_doe.sensitivity, "Sensitivity"),
    "SensitivityOFAT": (algs_doe.sensitivity, "Sensitivity OFAT"),
    "OFATSearch": (algs_doe.ofat_search, "OFAT Search"),
    "ParallelOFATSearch": (algs_doe.parallel_ofat_search, "Parallel OFAT Search"),
    "TwoKFactorial": (algs_doe.two_k_factorial, "2k Factorial"),
    "WorstCaseCorners": (algs_doe.worst_case_corners, "Worst Case Corners"),
    "DOE": (algs_doe.doe_composite, "Design of Experiments (composite)"),
    # 驱动
    "OnePoint": (drivers.one_point, "Single Point"),
    "Manual": (drivers.manual, "Manual"),
    "DSO": (drivers.dso, "Design Space Optimization"),
    "Optimize": (drivers.optimize, "Optimization"),
    "Global": (drivers.global_search, "Global Optimization"),
    "Local": (drivers.local_search, "Local Optimization"),
}

# 长名 / OCEAN 名字 -> 短名
ALIASES: dict[str, str] = {
    "Broyden-Fletcher-Goldfarb-Shanno": "BFGS",
    "Conjugate Gradient (Polak-Ribiere)": "ConjugateGradient",
    "Quasi-Newton": "QuasiNewton",
    "Design Space Optimization": "DSO",
    "Parallel Differential Evoluation": "PDE",
    "Parallel Differential Evolution": "PDE",
    "Global Optimization": "Global",
    "Local Optimization": "Local",
    "Local Optimization (fine)": "Local",
    "Local Optimization (coarse)": "Local",
    "Optimization": "Optimize",
    "Single Run, Sweeps and Corners": "Sweep",
    "Single Point": "OnePoint",
    "Sampling": "Hammersley",
    "Hammersley Sampling": "Hammersley",
    "Flexible Sweep Algorithm": "NewSweep",
    "Manual Optimization": "Manual",
    "Simulated Annealing": "Anneal",
    "Parallel Simulated Annealing": "PSA",
    "Parallel Powell": "ParallelPowell",
    "Conjugate Gradient": "ConjugateGradient",
    "Design of Experiments": "DOE",
    # ---- 下面这批来自 native harness 实测的 Factory::names()（59 条），补齐别名 ----
    "Greedy Minimization": "GreedyMinimization",
    "Hooke and Jeeves": "HookeJeeves",
    "Powell Loop": "Powell",
    "Simulated Annealing - NeoCircuit": "Anneal2",
    "Parallel Simulated Annealing - NeoCircuit": "PSA2",
    "Exhaustive and Brent Line Minimization": "LineMinimize",
    "Netlib Hooke and Jeeves": "NetlibHookeJeeves",
    "Old Hooke and Jeeves": "OldHookeJeeves",
    "Parallel Powell Loop": "ParallelPowell",
    "OFATSensitivity": "SensitivityOFAT",
    "Local Optimization - Parallel Powell": "Local",
    "neocircuitGlobal": "Global",
    "conjugateGradient": "ConjugateGradient",
    "brentPowell": "Powell",
    "hookeJeeves": "HookeJeeves",
    "simplex": "PSA",
}


def resolve(name: str) -> str:
    """把长名/别名/OCEAN 的 effort 归一成注册名。"""
    if name in REGISTRY:
        return name
    if name in ALIASES:
        return ALIASES[name]
    low = name.lower()
    for key in REGISTRY:
        if key.lower() == low:
            return key
    raise KeyError(f"No such algorithm: {name}")


def create(name: str, domain: Domain, goal: Goal, params: Params | None = None,
           start=None, prefer_extracted: bool = False):
    """``Factory::create``：按名字造一个算法生成器。

    优先用 ``extracted``（``algorithms/<Name>.py``，**逐点对拍过真库**）；
    没有对应提取件时回落到本包内的参考实现（``algs_local``/``algs_direct``/…）。

    历史：早期只走参考实现，导致 ``ade_repro`` 的默认引擎与真库分叉
    （``ade_repro/parity.py --isolate``：门面 22 次求值 vs 库 13 次，第 3 点分叉）。
    """
    key = resolve(name)
    pdict = _params_to_dict(params)
    if prefer_extracted:
        mod = _extracted_module(key)
        if mod is not None:
            pdict = _adapt_population(mod, pdict)
            try:
                return mod.run(domain, goal, start=start, params=pdict)
            except TypeError:
                return mod.run(domain, goal, params=pdict)
    fn = REGISTRY[key][0]
    try:
        return fn(domain, goal, start=start, params=params or Params())
    except TypeError:
        return fn(domain, goal, params=params or Params())


def _params_to_dict(params) -> dict:
    """``Params`` → 提取件认的 dict（提取件的 ``_as_params`` 只吃 mapping）。"""
    if params is None:
        return {}
    if isinstance(params, dict):
        return dict(params)
    inner = getattr(params, "_d", None)
    return dict(inner) if isinstance(inner, dict) else {}


def _adapt_population(mod, pdict: dict) -> dict:
    """``population`` 在参考实现里是**容量整数**，在提取件里是 **Population 对象**。

    提取件（PSA2/PDE）读 ``P["population"]`` 后直接调 ``.insert/.resize``；
    参考实现把它当 ``int``。这里按提取件暴露的 ``Population`` 类做一次转换。
    """
    val = pdict.get("population")
    cls = getattr(mod, "Population", None)
    if isinstance(val, int) and cls is not None and not isinstance(val, bool):
        pd = dict(pdict)
        pd["population"] = cls(int(val))
        return pd
    return pdict


def _extracted_module(key: str):
    """``algorithms/<Key>.py``（找不到返回 None）。"""
    try:
        from . import extracted
        return extracted.load(key)
    except Exception:
        return None


def names() -> list[str]:
    return sorted(REGISTRY)


def describe(name: str) -> str:
    return REGISTRY[resolve(name)][1]
