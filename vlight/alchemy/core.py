"""引擎骨架 —— ``Alchemy::State`` / ``Fork`` / ``Call`` / ``Population`` /
``DesignPointFactory`` / ``Params`` / ``Random::Gen`` / ``FunctionOptimizer::run``。

C++ 侧是一台**异常驱动的状态机**：每个状态实现 ``State::Ptr run()``，返回下一个状态；
子算法跑完时 ``Alchemy::Finish::run`` 抛 ``ShortCircuits::Done``，``Call<T>`` 吞掉它后
继续跑 T；要整条链停下就抛 ``ShortCircuits::Return``（一路穿透所有嵌套 ``Call<>``
到最外层驱动循环，LSDA 是 ``catch(...)``）。

Python 这边没有 ``State::run()`` 的返回状态链，改用**生成器**表达同一件事：每次
``yield`` 一批待仿真点、``send()`` 收回代价，语义与 ``FunctionOptimizer::run`` 的
ask/tell 循环一致。但异常/容器/工厂/RNG 这些都按原样复刻，算法实现可以照着 C++ 读。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .lattice import Domain, Param
from .spec import DBL_MAX, Goal, UNMEASURABLE_COST


# --------------------------------------------------------------------------- #
# 短路异常（对应 ShortCircuits::Done / Return / Join）
# --------------------------------------------------------------------------- #

class ShortCircuit(Exception):
    """所有短路异常的基类。"""


class Done(ShortCircuit):
    """子算法跑完：``Call<T>`` 会吞掉它并继续跑 T。"""


class Return(ShortCircuit):
    """整条链结束：一直穿到最外层驱动循环。"""


class Join(ShortCircuit):
    """fork 分支退出。"""


# --------------------------------------------------------------------------- #
# 随机数：boost::random::mt19937 + uniform_01
# --------------------------------------------------------------------------- #

class Gen:
    """``Alchemy::Random::Gen``：mt19937 + ``uniform_01``。

    逆向要点：``Gen(seed)`` 的 ``seed`` 缺省 **1**；``in01()`` 每次消耗 3 次引擎抽样
    （不是 1 次）；``ForkedGen(rng, a, b)`` 用 ``seed + (a+1)*step`` 派生独立流。
    """

    __slots__ = ("_mt", "_idx", "seed", "count")

    N, M = 624, 397
    MATRIX_A = 0x9908B0DF
    UPPER = 0x80000000
    LOWER = 0x7FFFFFFF

    def __init__(self, seed: int = 1):
        self.seed = int(seed)
        self.count = 0
        self._mt = [0] * self.N
        self._idx = self.N + 1
        self._mt[0] = self.seed & 0xFFFFFFFF
        for i in range(1, self.N):
            self._mt[i] = (1812433253 * (self._mt[i - 1] ^ (self._mt[i - 1] >> 30)) + i) & 0xFFFFFFFF
        self._idx = self.N

    def _genrand(self) -> int:
        if self._idx >= self.N:
            for i in range(self.N):
                y = (self._mt[i] & self.UPPER) | (self._mt[(i + 1) % self.N] & self.LOWER)
                self._mt[i] = self._mt[(i + self.M) % self.N] ^ (y >> 1) ^ (self.MATRIX_A if y & 1 else 0)
            self._idx = 0
        y = self._mt[self._idx]
        self._idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        self.count += 1
        return y & 0xFFFFFFFF

    def in01(self) -> float:
        """``uniform_01`` —— 每次烧 3 次引擎抽样（照抄原实现）。"""
        a = self._genrand()
        b = self._genrand()
        c = self._genrand()
        x = ((a >> 5) * 67108864.0 + (b >> 6)) / 9007199254740992.0
        _ = c
        return x if x < 1.0 else 0.9999999999999999

    def inrange(self, lo: int, hi: int) -> int:
        if hi <= lo:
            return lo
        return lo + int(self.in01() * (hi - lo + 1)) % (hi - lo + 1)

    def forked(self, a: int = 0, b: int = 2) -> "Gen":
        return Gen(self.seed + (a + 1) * b)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Gen(seed={self.seed})"


# --------------------------------------------------------------------------- #
# 参数表（Params / Dictionary）
# --------------------------------------------------------------------------- #

class Params:
    """``Alchemy::Params``：超参数字典，取值都带默认值。"""

    def __init__(self, data: dict | None = None):
        self._d = dict(data or {})

    def has(self, key: str) -> bool:
        return key in self._d

    def get(self, key: str, default=None):
        return self._d.get(key, default)

    def int(self, key: str, default: int) -> int:
        v = self._d.get(key, default)
        return int(v) if isinstance(v, (int, float)) else default

    def float(self, key: str, default: float) -> float:
        v = self._d.get(key, default)
        return float(v) if isinstance(v, (int, float)) else default

    def bool(self, key: str, default: bool) -> bool:
        v = self._d.get(key, default)
        return bool(v) if isinstance(v, bool) else default

    def str(self, key: str, default: str) -> str:
        v = self._d.get(key, default)
        return v if isinstance(v, str) else default

    def set(self, key: str, value) -> None:
        self._d[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def as_dict(self) -> dict:
        return dict(self._d)


# --------------------------------------------------------------------------- #
# 设计点
# --------------------------------------------------------------------------- #

@dataclass
class DesignPoint:
    """``Alchemy::DesignPoint``：整数索引 + 测量值 + 代价。"""

    values: list[int]                     # C++ DesignPoint+0xd8
    point: dict = field(default_factory=dict)
    responses: dict = field(default_factory=dict)
    cost: float = math.inf                # +0x8
    spec_costs: list[float] = field(default_factory=list)   # +0x30
    spec_errors: list[bool] = field(default_factory=list)   # +0x48
    spec_values: list[float] = field(default_factory=list)  # +0x70
    all_met: bool = False
    evaluated: bool = False
    error_cost: bool = False
    is_cache_hit: bool = False            # +0x18
    id: int = 0                           # +0xd0

    def key(self) -> tuple:
        return tuple(self.values)

    def __repr__(self) -> str:  # pragma: no cover
        return f"DesignPoint(values={self.values}, cost={self.cost:.6g})"


class Population:
    """``Alchemy::Population<T, evil_ptr_less>``：按 cost 升序的有界容器。

    ``insert`` 满了会 ``discardExtras``；``top()`` 就是当前最优点。
    NaN / 哨兵代价排在最后（``evil_ptr_less`` 的语义）。
    """

    def __init__(self, maxsize: int = 100):
        self.maxsize = max(1, int(maxsize))
        self._set: list[DesignPoint] = []

    @staticmethod
    def _key(dp: DesignPoint):
        """``DesignPoint::operator<`` 0x3d1dda：

        cost（optional 空 < 有值）→ ``isCacheHit``（false 优先）→ values 的 32 位 hash
        （``*0xef32f8c9``、按 ``v[i]%16`` 旋转、``*0x5c6b7``）→ 字典序。
        """
        c = dp.cost
        cf = (1, 0.0) if c is None or not math.isfinite(c) else (0, float(c))
        h = 0
        for v in dp.values:
            h = (h * 0xEF32F8C9) & 0xFFFFFFFF
            h ^= (h << (v % 16)) & 0xFFFFFFFF
            h = (h * 0x5C6B7) & 0xFFFFFFFF
        return (cf, 1 if dp.is_cache_hit else 0, h, list(dp.values))

    def insert(self, dp: DesignPoint) -> None:
        """``insertAndDiscardExtras`` 0x2efdf0：插入后总是 trim 掉最差的。"""
        self._set.append(dp)
        self._set.sort(key=self._key)
        if len(self._set) > self.maxsize:
            self._set = self._set[: self.maxsize]

    def top(self) -> DesignPoint | None:
        return self._set[0] if self._set else None

    def best_cost(self) -> float:
        t = self.top()
        return math.inf if t is None else t.cost

    def __len__(self) -> int:
        return len(self._set)

    def __iter__(self):
        return iter(self._set)


class Cache:
    """``DesignPointFactory::Cache``：``vector<int>`` → 已评估点，避免重复仿真。"""

    CACHE_SIZE = 100000          # ``cacheSize()`` 0x3d2138，非虚函数

    def __init__(self, size: int = CACHE_SIZE):
        self.size = size
        self._map: dict[tuple, DesignPoint] = {}
        self.inflight: dict[tuple, DesignPoint] = {}

    def hit(self, key: tuple) -> DesignPoint | None:
        """``Cache::hit`` 0x3d7976：只有**已评估**的点才算命中；
        否则登记 in-flight 并返回 None（调用方据此判断“已在飞”）。"""
        dp = self._map.get(key)
        if dp is not None and dp.evaluated:
            return dp
        return None

    def insert(self, dp: DesignPoint) -> None:
        """``Cache::insert`` 0x3d7b20：断言已评估 → 插入 → 超过 100000 时淘汰
        **cost 最大**的那个 → 把该 key 的所有 in-flight 副本用本结果填掉。"""
        if not dp.evaluated:
            raise ValueError("Cache::insert expects an evaluated point")
        self._map[dp.key()] = dp
        self.inflight.pop(dp.key(), None)
        if len(self._map) > self.size:
            worst = max(self._map.values(), key=lambda d: (d.cost if math.isfinite(d.cost) else math.inf))
            self._map.pop(worst.key(), None)

    def __len__(self) -> int:
        return len(self._map)


class DesignPointFactory:
    """``Alchemy::DesignPointFactory``：``tunables`` + 造点 + 缓存 + 参考点。"""

    def __init__(self, domain: Domain, goal: Goal, cache_size: int = 10000):
        self.domain = domain
        self.goal = goal
        self.tunables = domain.tunables          # 对应 factory+0x8
        self.cache = Cache(cache_size)
        self.reference: DesignPoint | None = None
        self._next_id = 1
        self.n_created = 0

    # vtable +0x10 / +0x18
    def create(self, values: Sequence[int]) -> DesignPoint:
        ix = self.domain.clamp_index(values)
        key = tuple(ix)
        hit = self.cache.hit(key)
        if hit is not None:
            return hit
        dp = DesignPoint(values=list(ix), point=self.domain.from_index(ix),
                         id=self._next_id)
        self._next_id += 1
        self.n_created += 1
        self.cache.inflight[key] = dp
        return dp

    def create_point(self, point: dict) -> DesignPoint:
        return self.create(self.domain.to_index(point))

    # vtable +0x20
    def set_reference_point(self, dp: DesignPoint) -> None:
        self.reference = dp

    def evaluate(self, dp: DesignPoint, responses: dict) -> DesignPoint:
        dp.responses = dict(responses)
        all_met_sofar = self.reference.all_met if self.reference else False
        dp.cost, dp.spec_costs, dp.all_met = self.goal.cost(dp.responses, all_met_sofar)
        dp.spec_values = [float(responses.get(r.name, math.nan)) for r in self.goal.responses]
        dp.spec_errors = [not math.isfinite(v) for v in dp.spec_values]
        dp.error_cost = any(e for e, s in zip(dp.spec_errors, self.goal.specs)
                            if s.enabled and s.type != 7)
        dp.evaluated = True
        self.cache.insert(dp)
        return dp

    def __len__(self) -> int:
        return len(self.cache)


# --------------------------------------------------------------------------- #
# 驱动：FunctionOptimizer::run 的 ask/tell 循环
# --------------------------------------------------------------------------- #

@dataclass
class OptResult:
    best: DesignPoint
    history: list[DesignPoint]
    nfev: int
    met: bool
    converged: bool = False
    message: str = ""


def run(algo, domain: Domain, goal: Goal, evaluate: Callable[[dict], dict],
          budget: int = 200, meet_all_goals: bool = False,
          max_stall: int | None = None) -> OptResult:
    """驱动循环：生成器 ask / 驱动 tell（= ``FunctionOptimizer::run``）。"""
    factory = DesignPointFactory(domain, goal)
    history: list[DesignPoint] = []
    best: DesignPoint | None = None
    best_cost = 1e100          # FunctionOptimizer::run 的初值 0x5783a0
    n = 0
    stall = 0
    converged = False
    message = "budget"

    try:
        batch = next(algo)
    except StopIteration:
        raise ValueError("algorithm produced no points") from None

    while True:
        if n >= budget:
            message = "budget"
            break
        if not batch:
            message = "algorithm done"
            break
        pts = []
        for item in batch:
            if isinstance(item, dict):
                dp = factory.create_point(item)
            elif item and isinstance(item[0], float):
                dp = factory.create_point(domain.point(domain.from_unit(item)))
            else:
                dp = factory.create(item)
            pts.append(dp)
        new = [dp for dp in pts if not dp.evaluated]
        for dp in new:
            factory.evaluate(dp, evaluate(dp.point))
        costs = [dp.cost for dp in pts]
        for dp in pts:
            history.append(dp)
            if best is None or dp.cost < best.cost:
                best = dp
                stall = 0
            else:
                stall += 1
        n += len(pts)
        if meet_all_goals and best is not None and best.all_met:
            converged, message = True, "all specs met"
            break
        if max_stall is not None and stall >= max_stall:
            converged, message = True, "no improvement"
            break
        try:
            batch = algo.send(costs)
        except StopIteration:
            converged, message = True, "done"
            break

    if best is None:
        raise ValueError("budget must be > 0")
    return OptResult(best=best, history=history, nfev=n, met=best.all_met,
                     converged=converged, message=message)
