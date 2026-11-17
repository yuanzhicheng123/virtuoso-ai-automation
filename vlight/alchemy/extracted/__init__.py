"""逐算法提取件（每个文件 = 一个算法的 ask/tell 生成器）。

这些文件直接来自 ``re/alchemy/algorithms/``，各自独立、只依赖标准库，签名统一：

    run(domain, goal=None, start=None, params=None) -> generator
        batch = yield [ix, ...]      # 整数索引；只含仍需评估的点
        costs = send(...)            # 驱动侧回代价
        return best_ix

与 package 内置实现的差别：这里的每一行都能对上 ``libalchemy.so`` 的指令地址，
连 bug 都照抄（例如 ``QuasiNewton`` 用上一轮方向当位移、``BFGS`` 非对角项被加了两次
delta、``GreedyMinimization`` 只扰同一个变量）。之所以保留两套，是为了能对拍：
内置实现"能跑且收敛好"，提取件"行为一致"。

按名字取：

    from vlight.alchemy.extracted import load, NAMES
    gen = load("Powell").run(domain, goal, start=None, params={"minimum_steps_for_brent": 30})
"""
from __future__ import annotations

import importlib
import pathlib

_DIR = pathlib.Path(__file__).resolve().parent

NAMES = sorted(p.stem for p in _DIR.glob("*.py") if not p.stem.startswith("_"))


def load(name: str):
    """按算法名（文件名）载入提取件。"""
    if name not in NAMES:
        raise KeyError(f"no extracted algorithm {name!r}; have: {', '.join(NAMES)}")
    return importlib.import_module(f"{__name__}.{name}")


def run(name: str, domain, goal=None, start=None, params=None):
    """便捷入口：直接拿生成器。"""
    return load(name).run(domain, goal=goal, start=start, params=params)


__all__ = ["NAMES", "load", "run"]
