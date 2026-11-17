"""virtuoso-bridge 适配器（对接 `virtuoso_bridge.VirtuosoClient`）。

virtuoso-bridge 的实际形态（摘自其文档）：
    from virtuoso_bridge import VirtuosoClient
    client = VirtuosoClient.from_env()
    client.execute_skill("1+1")            # 执行 SKILL，返回字符串/结构化结果
    with client.schematic.create(lib, cell) as sch:
        sch.add("dbCreateInst(...)")      # 批量提交

本适配器做三件事：
    1. 自动找 client（from_env / 传入实例 / 延迟导入）
    2. 把每次调用编码成一条 SKILL 表达式，批量时合成一个 let(...) 程序
    3. 把返回值解成 Python（含句柄）
"""
from __future__ import annotations

import os
from typing import Any

from ..bridge import Bridge, SkillHandle, _decode, to_skill


class VirtuosoBridge(Bridge):
    """真机桥：一次 execute_skill = 一次往返。"""

    def __init__(self, client: Any = None, *, from_env: bool = True,
                 bin_path: str | None = None):
        self._client = client
        self._from_env = from_env
        self.bin_path = bin_path

    # -- 客户端 ----------------------------------------------------------- #
    @property
    def client(self):
        if self._client is None:
            try:
                from virtuoso_bridge import VirtuosoClient      # type: ignore
            except Exception as exc:                             # noqa: BLE001
                raise RuntimeError(
                    "没装 virtuoso_bridge：pip install virtuoso-bridge，"
                    "或把 client= 传进来") from exc
            maker = getattr(VirtuosoClient, "from_env", None)
            if self._from_env and callable(maker):
                self._client = maker()
            else:
                self._client = VirtuosoClient()
        return self._client

    # -- 执行 ------------------------------------------------------------- #
    def _execute(self, code: str) -> Any:
        c = self.client
        for name in ("execute_skill", "executeSkill", "eval_skill", "skill",
                     "send", "call"):
            fn = getattr(c, name, None)
            if callable(fn):
                return fn(code)
        raise RuntimeError("VirtuosoClient 没有 execute_skill/eval 一类的入口")

    def eval(self, code: str) -> Any:
        return _decode(self._execute(code))

    def call(self, fn: str, args: list) -> Any:
        return _decode(self._execute("(%s %s)" % (
            fn, " ".join(to_skill(a) for a in args))))

    # -- 批量 ------------------------------------------------------------- #
    def execute_batch(self, exprs: list[tuple[str, list]]) -> list:
        """把多次调用合成一个 let(...) 程序，一次往返取回所有结果。"""
        if not exprs:
            return []
        if len(exprs) == 1:
            fn, args = exprs[0]
            return [self.call(fn, args)]
        vars_ = ["__vl%d" % i for i in range(len(exprs))]
        body = []
        for var, (fn, args) in zip(vars_, exprs):
            body.append("%s = (%s %s)" % (
                var, fn, " ".join(to_skill(a) for a in args)))
        code = "let( (%s) %s list(%s) )" % (
            " ".join(vars_), " ".join(body), " ".join(vars_))
        raw = self._execute(code)
        value = _decode(raw)
        if not isinstance(value, list) or len(value) != len(exprs):
            raise RuntimeError(f"批量返回形状不符: {value!r}")
        return value

    def screenshot(self, path: str) -> Any:
        fn = getattr(self.client, "screenshot", None)
        return fn(path) if callable(fn) else None


def connect(host: str | None = None, port: int | None = None, **kwargs) -> VirtuosoBridge:
    """连真机：优先环境变量，其次 virtuoso-bridge 的 from_env()。"""
    if host:
        os.environ.setdefault("VIRTUOSO_HOST", host)
    if port:
        os.environ.setdefault("VIRTUOSO_PORT", str(port))
    return VirtuosoBridge(**kwargs)


__all__ = ["VirtuosoBridge", "connect"]
