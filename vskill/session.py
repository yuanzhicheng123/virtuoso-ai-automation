"""vskill.session —— 有状态的会话：绑定桥、批量提交、按名调用。

    import vskill
    with vskill.Session(vskill.MockBridge()) as s:
        cv = s.dbOpenCellViewByType("LIB", "CELL", "layout", "r", "")
        with s.batch() as b:                 # 一次往返提交多条 SKILL
            inst = b.dbCreateInst(cv, "nch", "M1", [0, 0], "R0")
        print(inst.value.name)

批量模式把多次调用合成一个 `let(...)` 程序，避免 6 千个函数变成 6 千次 RTT。
"""
from __future__ import annotations

from typing import Any

from .api import SkillFunction
from .bridge import Bridge, Symbol, get_bridge, set_bridge, to_skill


class BatchError(RuntimeError):
    pass


class Ref:
    """批量里的惰性结果（flush 后填 value）。"""

    __slots__ = ("index", "value")

    def __init__(self, index: int):
        self.index = index
        self.value: Any = None

    def __repr__(self) -> str:
        return f"<ref #{self.index} {self.value!r}>"


class Batch:
    def __init__(self, session: "Session"):
        self.session = session
        self.calls: list[tuple[str, list]] = []
        self.refs: list[Ref] = []
        self.values: list[Any] = []
        self._closed = False

    def call(self, fn: str, args: list) -> Ref:
        encoded = [Symbol("__vl%d" % a.index) if isinstance(a, Ref) else a
                   for a in args]
        self.calls.append((fn, encoded))
        ref = Ref(len(self.calls) - 1)
        self.refs.append(ref)
        return ref

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def caller(*args):
            return self.call(name, list(args))
        caller.__name__ = name
        return caller

    def flush(self) -> list:
        if self._closed:
            raise BatchError("batch 已经提交过")
        self._closed = True
        bridge = self.session.bridge
        runner = getattr(bridge, "execute_batch", None)
        if callable(runner):
            self.values = runner(self.calls)
        else:
            self.values = [bridge.call(fn, args) for fn, args in self.calls]
        for ref, value in zip(self.refs, self.values):
            ref.value = value
        return self.values

    def __enter__(self) -> "Batch":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.flush()


class Session:
    """把函数表绑到一个桥上的会话。"""

    def __init__(self, bridge: Bridge | None = None, *, take_global: bool = False):
        self.bridge = bridge if bridge is not None else get_bridge()
        self._owns_global = take_global
        if take_global:
            set_bridge(self.bridge)
        self.history: list[tuple[str, list, Any]] = []

    # -- 上下文 ----------------------------------------------------------- #
    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        close = getattr(self.bridge, "close", None)
        if callable(close):
            close()

    # -- 调用 ------------------------------------------------------------- #
    def call(self, fn: str, *args, **kwargs) -> Any:
        if kwargs:
            args = args + tuple(
                x for kv in kwargs.items() for x in (Symbol("?" + kv[0]), kv[1]))
        out = self.bridge.call(fn, list(args))
        self.history.append((fn, list(args), out))
        return out

    def eval(self, code: str) -> Any:
        return self.bridge.eval(code)

    def batch(self) -> Batch:
        return Batch(self)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def caller(*args, **kwargs):
            return self.call(name, *args, **kwargs)
        caller.__name__ = name
        return caller

    def doc(self, name: str) -> str:
        return SkillFunction(name).__doc__ or ""

    def close(self) -> None:
        close = getattr(self.bridge, "close", None)
        if callable(close):
            close()


__all__ = ["Session", "Batch", "Ref", "BatchError"]
