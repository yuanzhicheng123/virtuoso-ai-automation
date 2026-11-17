"""规格（spec）与代价函数 —— ``Alchemy::CostFunction`` / ``SpecInfo``。

本模块是**门面**，真正的位级一致实现在 ``_costcore.py``（从 ``spec_cost.md`` §10
抽出，那份实现经过 unicorn 映射真实机器码、>2000 组随机用例的位级对拍）。

恢复出来的关键事实：

* ``SpecType``: 1=min 2=max 3=lt 4=gt 5=range 6=tol 7=info，0/>7 无效；
* 三个断点 good/ok/bad 由构造函数按类型从 target1/target2 推出；
* ``r = (ok − v)/(ok − bad)``；
  ``0≤r<1 → 2+2r``；``r≥1 → A − B/exp((r−1)τ)``（τ=1.98e-8 ⇒ **近似线性 4r**）；
  ``r<0`` 按类型分叉（lt/gt 只记 1.0；min/max 用 good 结点再算一次；
  range/tol 记 1.0 并计二次惩罚）；
* 聚合是两条公式：``all_measured ? Σw·c : (n−n_fail) + Σw + Σw·c``；
* ``specCosts`` 存的是**比值 r**，不是代价；
* ``isOpenEnded`` 只看"有没有梯度"，``Impl[0x48]`` 在三个库里从不置 1
  ⇒ range/tol 永远走开放分支、二次井恒开。

常数的字面量都在 ``_costcore`` 里与 ``.rodata`` 同字节。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# 常数（全部来自 libpointstrategy.so 的 .rodata）
# --------------------------------------------------------------------------- #

DISABLED_SPEC_COST = 1.0
COST_BEYOND_SLOPE = 1.980420515885558e-08
UNMEASURABLE_COST = 201977309.7244549
COST_EPS = 1e-20
DBL_MAX = 1.7976931348623157e308


from . import _costcore as _core

# 位级一致的常量（与 .rodata 同字节）
A_UNMEASURABLE = _core.A_UNMEASURABLE
UNMEASURABLE_COST = A_UNMEASURABLE
COST_BEYOND_SLOPE = _core.TAU
DBL_MAX = _core.DBL_MAX
DISABLED_SPEC_COST = _core.DISABLED_SPEC_COST


class SpecType:
    """``SpecTypeToString`` 0x1c5de0 证实的映射：1=min 2=max 3=lt 4=gt 5=range 6=tol 7=info。"""

    INVALID = 0
    MIN = 1
    MAX = 2
    LESS = 3
    GREATER = 4
    WINDOW = 5
    DELTA = 6
    INFO = 7

    NAMES = {0: "invalid", 1: "min", 2: "max", 3: "lt", 4: "gt",
             5: "range", 6: "tol", 7: "info"}


SpecInfo = _core.Spec          # 位级一致的 SpecInfo（断点、权重、复制数）
@dataclass
class Response:
    """一条 output 规格（ADE 的 Spec 列）。

    objective:  ``"max"`` / ``"min"`` / ``None``
    constraint: ``None`` 或 ``("gt", v)`` / ``("lt", v)`` / ``("range", lo, hi)``
                / ``("tol", target, pct)`` / ``("eq", v)``
    weight:     正整数（ADE 的 Weight 列），乘到该条的 cost 上
    info:       True 表示只看不优化
    """

    name: str
    objective: str | None = None
    target: float | None = None
    constraint: tuple | None = None
    weight: int = 1
    info: bool = False

    # -- 语义 ---------------------------------------------------------------- #
    def interval(self) -> tuple[float, float] | None:
        c = self.constraint
        if c is None:
            return None
        k = c[0]
        if k == "gt":
            return (float(c[1]), math.inf)
        if k == "lt":
            return (-math.inf, float(c[1]))
        if k == "range":
            return (float(c[1]), float(c[2]))
        if k == "tol":
            t, p = float(c[1]), float(c[2])
            d = abs(t) * p / 100.0
            return (t - d, t + d)
        if k == "eq":
            return (float(c[1]), float(c[1]))
        raise ValueError(f"unknown constraint {c!r}")

    def is_open_ended(self) -> bool:
        return self.objective is not None and self.constraint is None

    def is_range_like(self) -> bool:
        return self.constraint is not None and self.constraint[0] in ("range", "tol")

    def spec_type(self) -> int:
        if self.constraint is None:
            return SpecType.MAX if self.objective == "max" else SpecType.MIN
        k = self.constraint[0]
        return {"lt": SpecType.LESS, "gt": SpecType.GREATER,
                "range": SpecType.WINDOW, "tol": SpecType.DELTA,
                "eq": SpecType.WINDOW}[k]

    def to_spec(self) -> SpecInfo:
        st = self.spec_type()
        t1 = 0.0 if self.target is None else float(self.target)
        t2 = 0.0
        c = self.constraint
        if c is not None:
            if c[0] in ("gt", "lt"):
                t1 = float(c[1])
            elif c[0] == "range":
                t1, t2 = float(c[1]), float(c[2])
            elif c[0] == "tol":
                t1, t2 = float(c[1]), float(c[2])
            elif c[0] == "eq":
                t1 = t2 = float(c[1])
        spec = SpecInfo(name=self.name, type=st, weight=float(self.weight),
                        target1=t1, target2=t2,
                        enabled=not self.info)
        return spec


class Goal:
    """整套 specs。cost 直接调位级一致的 ``compute_cost_full``（见 ``_costcore``）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.specs = [r.to_spec() for r in self.responses]
        self.by_name = {r.name: r for r in self.responses}

    # -- 单条（调试用） ------------------------------------------------------ #
    def spec_cost(self, resp: Response, spec, value, all_met: bool):
        measured = {spec.name: value}
        out = _core.compute_cost_full([spec], measured, all_met)
        return out["cost"], out["met"][0]

    # -- 聚合 ---------------------------------------------------------------- #
    def cost(self, responses: dict, all_met: bool = False):
        """返回 ``(total, per_spec_ratio, met_flags)``。

        注意：``per_spec`` 是**比值 r**（``specCosts`` 的语义），不是代价；
        ``UNMEASURABLE`` 的行是 ``DBL_MAX``，disabled/type0/7 是 0.0。
        键缺失 = “这一行从没测过”（``all_measured=False``），键存在但值为 None =
        “测了但没测到”，两者在原始实现里走不同分支。
        """
        measured = {r.name: responses[r.name] for r in self.responses if r.name in responses}
        out = _core.compute_cost_full(self.specs, measured, all_met)
        return out["cost"], out["per_spec"], out["all_specs_met"]

    def cost_full(self, responses: dict, all_met: bool = False):
        measured = {r.name: responses[r.name] for r in self.responses if r.name in responses}
        return _core.compute_cost_full(self.specs, measured, all_met)

    def met(self, responses: dict) -> bool:
        return self.cost(responses)[2]

    # -- 调试 ----------------------------------------------------------------- #
    def print_specs(self) -> list[str]:
        return [s.fmt() for s in self.specs]
