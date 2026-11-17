"""vskill.tools —— 把 Virtuoso 的设计能力暴露成"可发现 / 可调用 / 可验证"的工具。

三层：
    ToolSpec    一个工具：名字、参数 schema、风险级、实现、验证器
    Registry    注册表：list / describe（MCP 形状）/ call / verify
    Evidence    每次调用留下证据（SKILL 文本、返回、读回校验）

设计约束（对应目标）：
    可发现  —— describe() 输出 MCP 兼容的 {name, description, inputSchema}
    可调用  —— call() 先按 schema 校验参数，再编码成 SKILL，走桥
    可验证  —— 每个工具带 verify()，用"读回"证明副作用真的发生了
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .bridge import Point, SkillHandle, Symbol, to_skill

RISKS = ("read", "write", "destructive")


@dataclass
class Param:
    name: str
    type: str = "string"          # string | number | bool | point | layer | object | list
    required: bool = True
    default: Any = None
    doc: str = ""

    def json(self) -> dict:
        t = {"string": "string", "number": "number", "bool": "boolean",
             "point": "array", "list": "array", "object": "string",
             "layer": "array"}.get(self.type, "string")
        return {"name": self.name, "type": t, "required": self.required,
                "default": self.default, "description": self.doc}


@dataclass
class ToolSpec:
    name: str
    summary: str
    params: list[Param]
    impl: Callable[..., Any]
    verify: Callable[..., Any] | None = None
    risk: str = "read"
    returns: str = "object"
    tags: tuple = ()

    # -- schema ----------------------------------------------------------- #
    def input_schema(self) -> dict:
        props, required = {}, []
        for p in self.params:
            props[p.name] = {"type": {"point": "array", "layer": "array"}
                             .get(p.type, {"number": "number", "bool": "boolean",
                                           "list": "array"}.get(p.type, "string")),
                             "description": p.doc}
            if p.type == "point":
                props[p.name]["items"] = {"type": "number"}
                props[p.name]["minItems"] = 2
            if p.required:
                required.append(p.name)
        return {"type": "object", "properties": props, "required": required}

    def describe(self) -> dict:
        return {"name": self.name, "description": self.summary,
                "inputSchema": self.input_schema(),
                "annotations": {"risk": self.risk, "returns": self.returns,
                                "tags": list(self.tags)}}


@dataclass
class Evidence:
    tool: str
    args: dict
    ok: bool
    skill: str = ""
    result: Any = None
    verified: bool | None = None
    detail: str = ""

    def json(self) -> dict:
        return {"tool": self.tool, "args": self.args, "ok": self.ok,
                "skill": self.skill, "result": _short(self.result),
                "verified": self.verified, "detail": self.detail}


def _short(value: Any, limit: int = 200) -> Any:
    if isinstance(value, SkillHandle):
        return {"handle": value.name, "type": value.type}
    if isinstance(value, (list, tuple)):
        return [_short(v, limit) for v in value][:20]
    if isinstance(value, str):
        return value[:limit]
    return value


class ToolError(RuntimeError):
    pass


class Registry:
    def __init__(self, session=None):
        self.tools: dict[str, ToolSpec] = {}
        self.session = session
        self.evidence: list[Evidence] = []

    # -- 注册 ------------------------------------------------------------- #
    def add(self, spec: ToolSpec) -> ToolSpec:
        if spec.risk not in RISKS:
            raise ToolError(f"风险级必须是 {RISKS}")
        self.tools[spec.name] = spec
        return spec

    def tool(self, name: str, summary: str, params: list[Param],
             risk: str = "read", returns: str = "object", tags: tuple = ()):
        def deco(fn):
            verify = getattr(fn, "_verify", None)
            self.add(ToolSpec(name, summary, params, fn, verify, risk, returns, tags))
            return fn
        return deco

    def verifier(self, name: str):
        def deco(fn):
            spec = self.tools[name]
            spec.verify = fn
            return fn
        return deco

    # -- 发现 ------------------------------------------------------------- #
    def list(self, prefix: str = "", risk: str = "", tag: str = "") -> list[dict]:
        out = []
        for name in sorted(self.tools):
            spec = self.tools[name]
            if prefix and not name.startswith(prefix):
                continue
            if risk and spec.risk != risk:
                continue
            if tag and tag not in spec.tags:
                continue
            out.append(spec.describe())
        return out

    def manifest(self) -> dict:
        return {"schema": "mcp/tools/v1", "count": len(self.tools),
                "tools": self.list()}

    def describe(self, tool_name: str, /) -> dict:
        return self.tools[tool_name].describe()

    # -- 调用 ------------------------------------------------------------- #
    def _bind(self, spec: ToolSpec, kwargs: dict) -> dict:
        bound = {}
        for p in spec.params:
            if p.name in kwargs:
                bound[p.name] = kwargs[p.name]
            elif p.required:
                raise ToolError(f"{spec.name}: 缺少必填参数 {p.name}")
            else:
                bound[p.name] = p.default
        extra = set(kwargs) - {p.name for p in spec.params}
        if extra:
            raise ToolError(f"{spec.name}: 未知参数 {sorted(extra)}")
        return bound

    def call(self, tool_name: str, /, **kwargs) -> Evidence:
        spec = self.tools.get(tool_name) or (_raise_key(tool_name))
        bound = self._bind(spec, kwargs)
        session = self.session
        if session is None:
            from .session import Session
            session = Session()
        ev = Evidence(tool=tool_name, args=bound, ok=False)
        try:
            result = spec.impl(session, **bound)
            ev.result, ev.ok = result, True
        except Exception as exc:                       # noqa: BLE001
            ev.detail = f"{type(exc).__name__}: {exc}"
            ev.skill = getattr(exc, "skill", "")
            self.evidence.append(ev)
            raise
        self.evidence.append(ev)
        return ev

    def verify(self, tool_name: str, /, **kwargs) -> Evidence:
        ev = self.call(tool_name, **kwargs)
        spec = self.tools[tool_name]
        if spec.verify is None:
            ev.verified = None
            ev.detail = "无验证器"
            return ev
        session = self.session
        if session is None:
            from .session import Session
            session = Session()
        ok, detail = spec.verify(session, ev.result, **kwargs)
        ev.verified, ev.detail = bool(ok), detail
        return ev

    # -- 报告 ------------------------------------------------------------- #
    def report(self) -> dict:
        total = len(self.evidence)
        ok = sum(1 for e in self.evidence if e.ok)
        ver = [e for e in self.evidence if e.verified is not None]
        return {"calls": total, "ok": ok,
                "verified": sum(1 for e in ver if e.verified),
                "verifiable": len(ver),
                "tools": len(self.tools),
                "evidence": [e.json() for e in self.evidence]}


def _raise_key(name: str):
    raise ToolError(f"未注册的工具 {name!r}")


def skill_of(fn: Callable, *args) -> str:
    """把一次工具调用渲染成 SKILL（用于证据/审计）。"""
    return " ".join(to_skill(a) for a in args)


__all__ = ["Param", "ToolSpec", "Evidence", "Registry", "ToolError", "skill_of"]
