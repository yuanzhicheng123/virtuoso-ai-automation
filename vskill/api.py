"""vskill.api —— 把 SKILL 函数表变成 Python 可调用对象。

名字来自挖矿索引（6,162 个），调用走 `vskill.bridge`：
    import vskill
    vskill.set_bridge(vskill.SkillbridgeAdapter(host="eda-220", port=7000))
    cv = vskill.dbOpenCellViewByType("LIB", "CELL", "schematic", "schematic", "r")
    print(cv.cellName)

没有实现/没有远端时抛 `SkillUnavailable`，不会静默返回 None。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bridge import Symbol, get_bridge, to_skill

_INDEX = json.loads((Path(__file__).resolve().parent / "_index.json").read_text())
FUNCTIONS: dict[str, dict] = _INDEX["functions"]


class SkillUnavailable(RuntimeError):
    pass


class SkillFunction:
    """一个 SKILL 函数的 Python 影子。"""

    def __init__(self, name: str, meta: dict | None = None):
        self.name = name
        self.meta = meta or {}
        self.args = list(self.meta.get("args") or [])
        self.manual = self.meta.get("manual", "")
        self.syntax = self.meta.get("syntax", "")
        self.__doc__ = (f"{self.syntax}\n\n[manual: {self.manual}]\n"
                        f"SKILL 函数，经 vskill.bridge 调用。")

    # -- 调用 ------------------------------------------------------------- #
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.args and len(args) > len(self.args):
            raise TypeError(
                f"{self.name} 最多 {len(self.args)} 个参数，给了 {len(args)} 个；"
                f"签名: {self.syntax or self.name}")
        if kwargs:
            args = args + tuple(
                x for kv in kwargs.items() for x in (Symbol("?" + kv[0]), kv[1]))
        return get_bridge().call(self.name, list(args))

    def skill(self, *args: Any, **kwargs: Any) -> str:
        """只生成 SKILL 代码，不发出去（调试/审计用）。"""
        if kwargs:
            args = args + tuple(
                x for kv in kwargs.items() for x in (Symbol("?" + kv[0]), kv[1]))
        return "(%s %s)" % (self.name, " ".join(to_skill(a) for a in args))

    def __repr__(self) -> str:
        return f"<skill-fn {self.name}({' '.join(self.args)})>"


def get(name: str) -> SkillFunction:
    if name not in FUNCTIONS:
        raise AttributeError(f"SKILL 函数表里没有 {name!r}")
    return SkillFunction(name, FUNCTIONS[name])


def __getattr__(name: str) -> SkillFunction:
    if name.startswith("_"):
        raise AttributeError(name)
    return get(name)


def __dir__():
    return sorted(FUNCTIONS)


def coverage() -> dict:
    per: dict[str, int] = {}
    for meta in FUNCTIONS.values():
        per[meta.get("manual", "?")] = per.get(meta.get("manual", "?"), 0) + 1
    return {"functions": len(FUNCTIONS), "per_manual": per}


__all__ = ["SkillFunction", "SkillUnavailable", "FUNCTIONS", "get", "coverage"]
