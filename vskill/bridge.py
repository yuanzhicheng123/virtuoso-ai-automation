"""vskill.bridge —— 把 Python 调用接到 SKILL 的传输层。

提供三种适配器 + 一个可替换的全局桥：

    MockBridge()                     离线：函数表 + 对象表（测试/演示）
    VirtuosoBridgeAdapter(...)       适配 virtuoso-bridge（execute_skill / send）
    SkillbridgeAdapter(...)         适配 skillbridge（Client.send/call）
    SubprocessSkillBridge(...)       直接喂 `skill` 二进制（无 GUI、无服务端）

约定：桥只做两件事
    call(fn, args) -> value            # args 已编码成 SKILL 字面量
    get_attr(obj, prop) / set_attr(obj, prop, value)
返回值里的 SKILL 数据结构由桥自己解析；远端对象用 SkillHandle 表示。
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# SKILL 字面量
# --------------------------------------------------------------------------- #

def to_skill(value: Any) -> str:
    if isinstance(value, SkillHandle):
        return str(value.name)
    if isinstance(value, Symbol):
        return str(value)
    if value is None:
        return "nil"
    if value is True:
        return "t"
    if value is False:
        return "nil"
    if isinstance(value, str):
        return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, Point):
        return "%r:%r" % (value.x, value.y)
    if isinstance(value, (list, tuple)):
        # SKILL 里 (1 2 3) 是"函数调用"，要传列表必须写 list(...)
        return "(list " + " ".join(to_skill(v) for v in value) + ")"
    if isinstance(value, dict):
        return "(" + " ".join("(%s %s)" % (to_skill(k), to_skill(v))
                              for k, v in value.items()) + ")"
    return str(value)


class Symbol(str):
    """SKILL 符号（不加引号）。"""


class Point:
    """SKILL 点 (x:y)——不是两个数的列表。"""
    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float):
        self.x, self.y = x, y

    def __iter__(self):
        return iter((self.x, self.y))

    def __repr__(self) -> str:
        return f"Point({self.x}, {self.y})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Point) and (self.x, self.y) == (other.x, other.y)


class SkillHandle:
    """远端 SKILL 对象（cellview / inst / net …）。

    属性读写都转发给桥：`cv.cellName` → getq，`cv.cellName = x` → putpropq。
    """
    _reserved = ("name", "type", "bridge")

    def __init__(self, name: str, type: str = "object", bridge: Any = None):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "bridge", bridge)

    def __getattr__(self, prop: str):
        if prop.startswith("_"):
            raise AttributeError(prop)
        bridge = object.__getattribute__(self, "bridge")
        if bridge is None:
            raise RuntimeError(f"{self!r} 没有绑定桥")
        return bridge.get_attr(self, prop)

    def __setattr__(self, prop: str, value: Any) -> None:
        if prop in self._reserved:
            object.__setattr__(self, prop, value)
            return
        bridge = object.__getattribute__(self, "bridge")
        if bridge is None:
            raise RuntimeError(f"{self!r} 没有绑定桥")
        bridge.set_attr(self, prop, value)

    def __repr__(self) -> str:
        return f"<skill {self.type} {self.name}>"


# --------------------------------------------------------------------------- #
# 桥
# --------------------------------------------------------------------------- #

class Bridge:
    def call(self, fn: str, args: list) -> Any:
        raise NotImplementedError

    def get_attr(self, obj: SkillHandle, prop: str) -> Any:
        return self.call("getq", [obj, Symbol(prop)])

    def set_attr(self, obj: SkillHandle, prop: str, value: Any) -> Any:
        return self.call("putpropq", [obj, value, Symbol(prop)])

    def eval(self, skill_code: str) -> Any:
        raise NotImplementedError



class MockBridge(Bridge):
    """离线桥：注册函数与对象，记录所有调用。"""

    def __init__(self, funcs: dict | None = None):
        self.funcs = dict(funcs or {})
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple[str, list]] = []

    def add(self, name: str, fn) -> "MockBridge":
        self.funcs[name] = fn
        return self

    def add_object(self, name: str, props: dict, otype: str = "object") -> SkillHandle:
        self.objects[name] = {"type": otype, **props}
        return SkillHandle(name, otype, self)

    def call(self, fn: str, args: list) -> Any:
        self.calls.append((fn, args))
        if fn in self.funcs:
            return self.funcs[fn](*args)
        if fn == "getq":
            obj, prop = args
            return self.objects.get(obj.name, {}).get(str(prop))
        if fn == "putpropq":
            obj, value, prop = args
            self.objects.setdefault(obj.name, {})[str(prop)] = value
            return value
        raise KeyError(f"MockBridge 没有 {fn}")

    def eval(self, code: str) -> Any:
        self.calls.append(("<eval>", [code]))
        return code


class VirtuosoBridgeAdapter(Bridge):
    """适配 virtuoso-bridge：优先 execute_skill，其次 send/call。

    virtuoso_bridge 的常见用法：
        from virtuoso_bridge import VirtuosoClient
        c = VirtuosoClient(...); c.execute_skill("1+1")  -> "2"
    这里的适配器不硬绑定具体类名：把 client 传进来即可（duck typing）。
    """

    def __init__(self, client=None, host: str = "localhost", port: int = 65432,
                 timeout: float = 30.0):
        if client is None:
            try:
                import virtuoso_bridge as vb          # type: ignore
            except Exception as exc:                  # noqa: BLE001
                raise RuntimeError("没装 virtuoso_bridge，请传入 client=") from exc
            for path in (("VirtuosoClient",), ("Client",), ("Virtuoso",),
                         ("client", "Client")):
                obj = vb
                try:
                    for attr in path:
                        obj = getattr(obj, attr)
                    client = obj(host=host, port=port)
                    break
                except Exception:                     # noqa: BLE001
                    client = None
        if client is None:
            raise RuntimeError("无法从 virtuoso_bridge 里找到客户端类")
        self.client = client
        self.timeout = timeout

    # -- 底层 ------------------------------------------------------------- #
    def execute(self, code: str) -> Any:
        c = self.client
        for name in ("execute_skill", "executeSkill", "eval_skill", "send",
                     "call", "eval"):
            fn = getattr(c, name, None)
            if callable(fn):
                try:
                    return fn(code)
                except TypeError:
                    return fn(code, timeout=self.timeout)
        raise RuntimeError("client 没有可用的执行方法")

    def eval(self, code: str) -> Any:
        return self.execute(code)

    def call(self, fn: str, args: list) -> Any:
        code = "%s(%s)" % (fn, " ".join(to_skill(a) for a in args))
        raw = self.execute(code)
        return _decode(raw)


class SkillbridgeAdapter(VirtuosoBridgeAdapter):
    """适配 skillbridge（220 上的 conda env 里是 1.5.1）。"""

    def __init__(self, client=None, host: str = "localhost", port: int = 7000,
                 timeout: float = 30.0):
        if client is None:
            import skillbridge                        # type: ignore
            client = skillbridge.Client(host=host, port=port)
        super().__init__(client=client, timeout=timeout)


class SubprocessSkillBridge(Bridge):
    """直接跑 `skill` 二进制：把表达式写到 stdin，读 stdout。

    没有常驻服务、没有 GUI；适合离线小脚本（无 license 依赖）。
    """

    PROMPT = "> "

    def __init__(self, binary: str = "skill", env: dict | None = None,
                 timeout: float = 30.0):
        import os
        self.binary = binary
        self.env = env or os.environ.copy()
        self.timeout = timeout

    def eval(self, code: str) -> Any:
        proc = subprocess.run([self.binary], input=code + "\nexit\n",
                              capture_output=True, text=True, timeout=self.timeout,
                              env=self.env)
        return _parse_skill_output(proc.stdout)

    def call(self, fn: str, args: list) -> Any:
        return self.eval("printf(\"%%L\\n\" %s(%s))" % (
            fn, " ".join(to_skill(a) for a in args)))


# --------------------------------------------------------------------------- #
# 结果解析
# --------------------------------------------------------------------------- #

_HANDLE = re.compile(r"^<(?P<type>\w+)[ >]")

def _decode(raw: Any) -> Any:
    """把桥返回的字符串/JSON 解成 Python。"""
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if s in ("t", "nil"):
        return True if s == "t" else None
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        inner = s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        m = _HANDLE.match(inner)          # "<cellview cv1>" 也是句柄
        if m:
            return SkillHandle(name=inner, type=m.group("type"))
        return inner
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1].strip()
        return [_decode(x) for x in _split_skill(inner)] if inner else []
    m = _HANDLE.match(s)
    if m:
        return SkillHandle(name=s, type=m.group("type"))
    return s


def _split_skill(text: str) -> list[str]:
    out, depth, buf = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch.isspace() and depth == 0:
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _parse_skill_output(stdout: str) -> Any:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith(">"):
            ln = ln[1:].strip()
        if ln and ln not in ("t",):
            return _decode(ln)
    return None


# --------------------------------------------------------------------------- #
# 全局桥
# --------------------------------------------------------------------------- #

_BRIDGE: Bridge | None = None


def set_bridge(bridge: Bridge | None) -> None:
    global _BRIDGE
    _BRIDGE = bridge


def get_bridge() -> Bridge:
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = MockBridge()
    return _BRIDGE


__all__ = ["Bridge", "MockBridge", "VirtuosoBridgeAdapter", "SkillbridgeAdapter",
           "SubprocessSkillBridge", "SkillHandle", "Symbol", "Point", "to_skill",
           "set_bridge", "get_bridge"]
