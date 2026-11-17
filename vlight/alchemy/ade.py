"""ADE run-mode layer —— ``pointStrategy::RunMode`` 的 16 条记录，以及「哪个 mode 驱动哪个算法」。

这是 **门面**（façade）：不新增任何引擎语义，只是把 ``spec_pointstrategy.md`` §registry
（``s_registry`` @0x6442f0，record 0x128 字节）那张表按原样搬进代码，再把
``alchemyFactoryText``（``+0x70``，ADE 交给 ``Alchemy::Factory`` 的算法名）解析成本包
``registry.REGISTRY`` / ``extracted`` 里真能跑的名字。

逐字段出处（``spec_pointstrategy.md`` 的 registry 表；``REPORT.md`` §9.2 是同一张表的中文版）：

======================  ==========================================================
字段                     含义（C++ 侧）
======================  ==========================================================
``token``                ``+0x108`` camelCase token，history / CDS-env 的 key
``unique_name``          ``+0x00`` 人类名，``--mode <name>`` / SDB 用它选模式
``algorithm``            ``+0x70`` ``alchemyFactoryText``：该 mode 交给 Factory 的算法
``abbrev``               ``+0xe8`` 缩写（GO / LO / WCC …）
``requires_merit``       ``+0xb4`` 是否需要 cost function
``lazy``                 ``+0xb6`` 是否用 lazy corner evaluation
======================  ==========================================================

``algorithm`` 是**原始字符串**（含大写、空格、原文拼写），本模块不改写它；``registry``
字段才是本仓库能实例化的注册名，``note`` 说明为什么两者不同。

用法::

    from vlight.alchemy import ade
    ade.algorithm_for_run_mode("globalOptimization")   # -> 'Global'
    ade.describe_run_mode("worstCaseCorners")
    ade.run_mode_table()
"""
from __future__ import annotations

from . import extracted
from .registry import ALIASES, REGISTRY, resolve

__all__ = [
    "RUN_MODES", "AdeError", "run_mode_names", "describe_run_mode", "run_mode_table",
    "algorithm_for_run_mode", "resolve_algorithm", "mode_token", "mode_unique_name",
    "NEEDS_PLUGIN", "NATIVE_DRIVER_PARAMS",
]


class AdeError(ValueError):
    """ADE 层的模式/名字不可用（不是引擎错误）。"""


# --------------------------------------------------------------------------- #
# 16 条 registry 记录（spec_pointstrategy.md 的 registry 表，逐行照抄）
# --------------------------------------------------------------------------- #
#
# registry: 本仓库能跑的注册名（REGISTRY 的键，或 extracted 的模块名）；None = 没有实现。
#           ``algorithm`` 原文与 ``registry`` 不同时，``note`` 说明理由。
#
RUN_MODES: dict[str, dict] = {
    "singleRunSweepsAndCorners": {
        "index": 0, "unique_name": "Single Run, Sweeps and Corners", "algorithm": "NewSweep",
        "abbrev": "SRSC", "requires_merit": False, "lazy": False, "registry": "NewSweep",
        "note": "sweep/采样类，不需要 cost function",
    },
    "monteCarloSampling": {
        "index": 1, "unique_name": "Monte Carlo Sampling", "algorithm": "NewSweep",
        "abbrev": "MC", "requires_merit": False, "lazy": False, "registry": "NewSweep",
        "note": "MC 的统计部分（分布/良率）在 vlight.opt，未合并；这里只有驱动算法",
    },
    "globalOptimization": {
        "index": 2, "unique_name": "Global Optimization", "algorithm": "Global Optimization",
        "abbrev": "GO", "requires_merit": True, "lazy": True, "registry": "Global",
        "note": "drivers.global_search：OFAT→(PSA/PDE)→BFGS 精修",
    },
    "localOptimization": {
        "index": 3, "unique_name": "Local Optimization", "algorithm": "Local Optimization",
        "abbrev": "LO", "requires_merit": True, "lazy": True, "registry": "Local",
        "note": "drivers.local_search：按 gate 依次 BFGS/Powell/CG/HJ/OFAT",
    },
    "improveYield": {
        "index": 4, "unique_name": "Improve Yield", "algorithm": "ImproveYield",
        "abbrev": "IY", "requires_merit": True, "lazy": False, "registry": None,
        "note": "统计良率优化（WCD/σ/蒙特卡洛闭环）在 vlight.opt，未合并",
    },
    "highYieldEstimation": {
        "index": 5, "unique_name": "High Yield Estimation", "algorithm": "HighYieldEstimation",
        "abbrev": "HYE", "requires_merit": True, "lazy": False, "registry": None,
        "note": "同上：良率估计不在本仓库",
    },
    "sensitivityAnalysis": {
        "index": 6, "unique_name": "Sensitivity Analysis", "algorithm": "Sensitivity",
        "abbrev": "SA", "requires_merit": False, "lazy": False, "registry": "Sensitivity",
        "note": "序数型单因子扫描（不计算灵敏度系数，见 algs_doe.sensitivity）",
    },
    "conjugateGradientOptimization": {
        "index": 7, "unique_name": "Conjugate Gradient Optimization", "algorithm": "ConjugateGradient",
        "abbrev": "CG", "requires_merit": True, "lazy": False, "registry": "ConjugateGradient",
        "note": "",
    },
    "feasibilityAnalysis": {
        "index": 8, "unique_name": "Feasibility Analysis", "algorithm": "Optimization",
        "abbrev": "FA", "requires_merit": True, "lazy": True, "registry": "Optimize",
        "note": "``Optimization`` 是 ``Alchemy::Optimize``（drivers.optimize），注册名是 Optimize",
    },
    "worstCaseCorners": {
        "index": 9, "unique_name": "Create Worst Case Corners", "algorithm": "WorstCaseCorners",
        "abbrev": "WCC", "requires_merit": True, "lazy": False, "registry": "WorstCaseCorners",
        "note": "策略选择器：默认 use_OFAT=true → SensitivityOFAT（algs_doe.worst_case_corners）",
    },
    "manualOptimization": {
        "index": 10, "unique_name": "Manual Optimization", "algorithm": "ManualOptimization",
        "abbrev": "MO", "requires_merit": False, "lazy": True, "registry": "Manual",
        "note": "``ManualOptimization`` 在本仓库只保留点序列驱动（drivers.manual）",
    },
    "manualTuning": {
        "index": 11, "unique_name": "Manual Tuning", "algorithm": "ManualTuning",
        "abbrev": "MT", "requires_merit": False, "lazy": True, "registry": "Manual",
        "note": "手工调优＝人工点序列，同 Manual",
    },
    "ldeSensitivityAnalysis": {
        "index": 12, "unique_name": "LDE Sensitivity Analysis", "algorithm": "Sensitivity",
        "abbrev": "LDESA", "requires_merit": False, "lazy": False, "registry": "Sensitivity",
        "note": "LDE（layout-dependent effect）提取在 vlight.lvs/l3，未合并",
    },
    "runPlan": {
        "index": 13, "unique_name": "Run Plan", "algorithm": "RunPlan",
        "abbrev": "RP", "requires_merit": False, "lazy": False, "registry": None,
        "note": "Run Plan 的执行计划层未合并（只恢复了 run-mode 记录本身）",
    },
    "faultSimulation": {
        "index": 14, "unique_name": "Fault Simulation", "algorithm": "FaultSimulation",
        "abbrev": "FS", "requires_merit": False, "lazy": False, "registry": None,
        "note": "故障仿真不在本仓库",
    },
    "dsoOptimization": {
        "index": 15, "unique_name": "Advanced Optimization", "algorithm": "AOP",
        "abbrev": "AOP", "requires_merit": True, "lazy": True, "registry": "Optimize",
        "note": ("AOP 是 DSO 客户端的外部优化器包装（``Alchemy::DSO`` → drivers.dso，需要一个 "
                 "plugin 生成器）；没有 plugin 时退回 ``Optimize``（Global/Local 分发）"),
    },
}

#: 需要外部 optimizer plugin 才能跑的模式（drivers.dso 的 ``plugin=`` 参数）
NEEDS_PLUGIN = frozenset({"dsoOptimization"})

#: 驱动类提取件（``Global`` / ``Local`` / ``Optimize`` / ``DSO``）的**客户端 gate 参数**。
#:
#: 这四个算法在 C++ 里是"编排器"：``Local::Data`` / ``Global::Data`` 的 ``run_bfgs`` /
#: ``algorithm`` 等字段由策略层（也就是 ADE 的 run option + ``?effort``）填，缺省全 false。
#: 所以 ``extracted.Local.run(domain, goal, params=None)`` 只评估起点就返回 —— 不是 bug，
#: 而是忠实复刻"没有客户端参数就什么都不做"。这里补上 ADE 对应模式会写进去的那几个值。
NATIVE_DRIVER_PARAMS: dict[str, dict] = {
    "Local": {"run_bfgs": True},                 # ?effort "coarse" -> BFGS 分支
    "Global": {},                                # Global::Start 自带 Sweep 默认
    "Optimize": {"algorithm": "neocircuitGlobal"},   # Optimize::Start 0x364ca0 的 Global 分支
    "DSO": {},                                   # 需要外部 plugin，空参数=不动
}

#: ``uniqueName`` -> token
_BY_UNIQUE = {v["unique_name"]: k for k, v in RUN_MODES.items()}
_BY_ABBREV = {v["abbrev"]: k for k, v in RUN_MODES.items()}
_BY_FACTORY = {v["algorithm"]: k for k, v in RUN_MODES.items()}


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #

def run_mode_names() -> list[str]:
    """16 个 token，按 registry 顺序。"""
    return [k for k, _ in sorted(RUN_MODES.items(), key=lambda kv: kv[1]["index"])]


def mode_token(name: str) -> str:
    """token / uniqueName / 缩写 / 工厂名都接受。"""
    if name in RUN_MODES:
        return name
    if name in _BY_UNIQUE:
        return _BY_UNIQUE[name]
    if name in _BY_ABBREV:
        return _BY_ABBREV[name]
    if name in _BY_FACTORY:
        return _BY_FACTORY[name]
    low = name.lower()
    for key in list(RUN_MODES) + list(_BY_UNIQUE):
        if key.lower() == low:
            return key if key in RUN_MODES else _BY_UNIQUE[key]
    raise AdeError(f"no such ADE run mode: {name!r}; have: {', '.join(run_mode_names())}")


def mode_unique_name(name: str) -> str:
    return RUN_MODES[mode_token(name)]["unique_name"]


def describe_run_mode(name: str) -> dict:
    tok = mode_token(name)
    d = dict(RUN_MODES[tok])
    d["token"] = tok
    return d


def run_mode_table() -> list[dict]:
    """``[(index, token, unique_name, factory_text, registry, requires_merit, lazy)]``."""
    out = []
    for tok in run_mode_names():
        d = RUN_MODES[tok]
        out.append({"index": d["index"], "token": tok, "unique_name": d["unique_name"],
                    "algorithm": d["algorithm"], "registry": d["registry"],
                    "abbrev": d["abbrev"], "requires_merit": d["requires_merit"],
                    "lazy": d["lazy"]})
    return out


# --------------------------------------------------------------------------- #
# 名字解析
# --------------------------------------------------------------------------- #

def _native_names() -> set[str]:
    return set(extracted.NAMES)


def resolve_algorithm(name: str, engine: str = "ours") -> str:
    """把 ADE 的 ``alchemyFactoryText`` / 长名 / 别名 / 注册名解析成可实例化的名字。

    engine ``"ours"``   -> ``registry.REGISTRY`` 的键（包内实现）
    engine ``"native"`` -> ``extracted.NAMES`` 的模块名（``algorithms/*.py`` 逐指令件）
    """
    if engine not in ("ours", "native"):
        raise AdeError(f"engine must be 'ours' or 'native', not {engine!r}")
    pool = set(REGISTRY) if engine == "ours" else _native_names()
    pool_low = {n.lower(): n for n in pool}
    # 1) exact
    for cand in (name, ALIASES.get(name)):
        if cand and cand in pool:
            return cand
    # 2) registry alias table (long names / OCEAN effort strings)
    try:
        short = resolve(name)
    except KeyError:
        short = None
    if short:
        if short in pool:
            return short
        if short in pool_low.values():
            return short
        if short.lower() in pool_low:
            return pool_low[short.lower()]
    # 3) case-insensitive
    if name.lower() in pool_low:
        return pool_low[name.lower()]
    raise AdeError(
        f"algorithm {name!r} is not available for engine={engine!r}; "
        f"have: {', '.join(sorted(pool))}")


def algorithm_for_run_mode(mode: str, engine: str = "ours", allow_plugin: bool = False) -> str:
    """run mode -> 可实例化的算法名；没有实现的模式抛 :class:`AdeError`。

    ``allow_plugin=False``（默认）时，需要外部 optimizer plugin 的模式（``dsoOptimization``）
    退回它的 ``registry`` 字段（``Optimize``），并在 :func:`describe_run_mode` 的 ``note``
    里说明；``allow_plugin=True`` 时返回 ``DSO`` 让调用方自己接 plugin。
    """
    tok = mode_token(mode)
    d = RUN_MODES[tok]
    if tok in NEEDS_PLUGIN and allow_plugin:
        return resolve_algorithm("DSO", engine)
    target = d["registry"]
    if target is None:
        raise AdeError(
            f"run mode {tok!r} ({d['unique_name']}, factory text {d['algorithm']!r}) has no "
            f"implementation in this repository: {d['note']}"
        )
    return resolve_algorithm(target, engine)
