"""整数格设计空间 —— ``Alchemy::DesignPoint::design`` / ``DesignPointFactory::tunables``。

逆向结论：引擎内部**没有连续变量**。一个设计点是 ``vector<int>``（``DesignPoint+0xd8``），
第 i 维是 ``[0, tunables[i]-1]`` 里的下标；``tunables`` 由 ``DesignPointFactory+0x8`` 给出。
所有算法都在这个整数格上移动，这也是为什么 ADE 的优化结果总是落在变量网格上。
"""
from __future__ import annotations

from typing import Iterable, Sequence


class Param:
    """一个设计变量（对应 ADE 的 Design Variable）。

    kind:
      * ``"continuous"``      —— 连续量，但引擎会离散成 ``n_values`` 档
      * ``"discrete_step"``   —— ``lo..hi`` 按 ``step`` 取格点
      * ``"discrete_values"`` —— 有限候选取值（可以是字符串，如器件 model）
    """

    __slots__ = ("name", "kind", "lo", "hi", "step", "values", "nominal", "n_values")

    def __init__(self, name: str, lo: float = 0.0, hi: float = 1.0,
                 step: float | None = None, values: Sequence | None = None,
                 nominal=None, n_values: int | None = None):
        self.name = name
        if values is not None:
            self.kind = "discrete_values"
            self.values = list(values)
            nums = [v for v in self.values if isinstance(v, (int, float))]
            self.lo = float(min(nums)) if nums else 0.0
            self.hi = float(max(nums)) if nums else 1.0
            self.step = None
            self.nominal = nominal if nominal is not None else self.values[0]
        elif step is not None:
            self.kind = "discrete_step"
            self.lo, self.hi, self.step = float(lo), float(hi), float(step)
            self.values = None
            self.nominal = nominal if nominal is not None else 0.5 * (lo + hi)
        else:
            self.kind = "continuous"
            self.lo, self.hi, self.step = float(lo), float(hi), None
            self.values = None
            self.nominal = nominal if nominal is not None else 0.5 * (lo + hi)

        if n_values is not None:
            self.n_values = int(n_values)
        elif self.kind == "discrete_values":
            self.n_values = len(self.values)
        elif self.kind == "discrete_step":
            self.n_values = int(round(self.span() / self.step)) + 1
        else:
            self.n_values = 101          # 连续变量的默认档位数（ADE 由 domain sampling 决定）

    def span(self) -> float:
        return self.hi - self.lo

    def project(self, x):
        """夹到边界；离散域上取最近的合法值（``fitToBounds``，不是 floor/round）。"""
        if self.kind == "discrete_values":
            nums = [v for v in self.values if isinstance(v, (int, float))]
            if not nums or not isinstance(x, (int, float)):
                return x if not isinstance(x, (int, float)) else self.values[0]
            return min(nums, key=lambda v: abs(float(v) - float(x)))
        x = min(max(float(x), self.lo), self.hi)
        if self.kind == "discrete_step" and self.step:
            n = round((x - self.lo) / self.step)
            x = min(max(self.lo + n * self.step, self.lo), self.hi)
        return x

    def value_at(self, index: int):
        """第 index 档对应的物理值。"""
        i = min(max(int(index), 0), self.n_values - 1)
        if self.kind == "discrete_values":
            return self.values[i]
        if self.kind == "discrete_step":
            return self.project(self.lo + i * self.step)
        return self.lo + i * self.span() / max(1, self.n_values - 1)

    def index_of(self, value) -> int:
        if self.kind == "discrete_values":
            return self.values.index(value) if value in self.values else 0
        if self.kind == "discrete_step":
            return int(round((float(value) - self.lo) / self.step))
        return int(round((float(value) - self.lo) / self.span() * (self.n_values - 1)))

    def __repr__(self) -> str:  # pragma: no cover
        if self.kind == "discrete_values":
            return f"Param({self.name!r}, values={self.values})"
        if self.kind == "discrete_step":
            return f"Param({self.name!r}, {self.lo}:{self.step}:{self.hi})"
        return f"Param({self.name!r}, [{self.lo}, {self.hi}], n={self.n_values})"


class Domain:
    """一组变量 + 整数格 ↔ 物理点的双向映射（``Domain`` / ``tunables``）。"""

    def __init__(self, params: Iterable[Param]):
        self.params = list(params)
        self.by_name = {p.name: p for p in self.params}

    # -- 基本 --------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self.params)

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.params]

    @property
    def tunables(self) -> list[int]:
        """每个变量的合法档位数 —— C++ 里的 ``POINTS->tunables``。"""
        return [p.n_values for p in self.params]

    def nominal(self) -> dict:
        return {p.name: p.nominal for p in self.params}

    def n_points(self) -> int:
        n = 1
        for t in self.tunables:
            n *= max(1, t)
        return n

    # -- 整数格 ------------------------------------------------------------- #
    def from_index(self, ix: Sequence[int]) -> dict:
        return {p.name: p.value_at(i) for p, i in zip(self.params, ix)}

    def to_index(self, point: dict) -> list[int]:
        return [p.index_of(point[p.name]) for p in self.params]

    def clamp_index(self, ix: Sequence[int]) -> list[int]:
        return [min(max(int(i), 0), p.n_values - 1) for p, i in zip(self.params, ix)]

    def center_index(self) -> list[int]:
        return [p.n_values // 2 for p in self.params]

    def nominal_index(self) -> list[int]:
        return self.to_index(self.nominal())

    # -- 单位立方体（给需要连续近似的场合） ---------------------------------- #
    def to_unit(self, values) -> list[float]:
        """物理值（list 或 dict）-> 单位立方体坐标。"""
        if isinstance(values, dict):
            vals = [values[p.name] for p in self.params]
        else:
            vals = list(values)
        out = []
        for p, v in zip(self.params, vals):
            v = float(v) if isinstance(v, (int, float)) else 0.0
            s = p.span()
            out.append(0.0 if s <= 0 else (v - p.lo) / s)
        return out

    def from_unit(self, u: Sequence[float]) -> list[float]:
        """单位立方体 -> 物理值列表（落格）。"""
        out = []
        for p, ui in zip(self.params, u):
            ui = min(max(float(ui), 0.0), 1.0)
            out.append(p.value_at(int(round(ui * (p.n_values - 1)))))
        return out

    def point(self, values: Sequence[float]) -> dict:
        """按物理值造点（夹边界 + 就近取档），等价于旧版的投影语义。"""
        out = {}
        for p, v in zip(self.params, values):
            if p.kind == "discrete_values" and not isinstance(v, (int, float)):
                out[p.name] = v
                continue
            out[p.name] = p.value_at(p.index_of(p.project(v)))
        return out

    def unit(self) -> list[float]:
        """nominal 在单位立方体里的位置。"""
        return self.to_unit(self.nominal())

    def vector(self, point: dict) -> list[float]:
        return [float(point[p.name]) if isinstance(point[p.name], (int, float)) else float("nan")
                for p in self.params]
