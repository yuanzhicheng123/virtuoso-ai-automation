"""vskill.gateway —— 受控执行网关：白名单 + 风险 + 类型检查 + 快照回读。

设计对应任务第四阶段：
    - 复用现有 Bridge（不替换通信内核）
    - Agent 不能拼任意 SKILL，只能调白名单里的 operation
    - 执行前做参数类型检查与风险判定
    - 写操作前后取快照并做差异回读
    - 超时/重试/会话锁
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "audit" / "2026-10-22" / "registry"

TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, (list, tuple)),
    "layerPurpose": lambda v: isinstance(v, (list, tuple)) and len(v) == 2,
    "object": lambda v: v is not None,
    "general": lambda v: True,
    "symbol": lambda v: isinstance(v, str),
    "window": lambda v: v is not None,
    "unknown": lambda v: True,
}

DENY_PATTERNS = ("delete", "destroy", "exit", "quit", "unlink", "remove",
                 "purge", "kill")


class PolicyDenied(RuntimeError):
    pass


@dataclass
class Operation:
    name: str
    skill: str
    risk: str
    params: list[dict] = field(default_factory=list)


class Gateway:
    """白名单执行器。会话由外部注入（VirtuosoBridge / skillbridge / Nograph）。"""

    def __init__(self, session, registry_dir: Path = REG,
                 allow_write: bool = False, snapshot=None, max_retries: int = 1,
                 contract=None):
        self.session = session
        self.api = json.loads((registry_dir / "api_registry.json").read_text())
        self.ops_raw = json.loads((registry_dir / "operation_registry.json").read_text())
        self.policy = json.loads((registry_dir / "execution_policy.json").read_text())
        self.allow_write = allow_write
        self.snapshot = snapshot          # callable(cv) -> dict
        self.max_retries = max_retries
        self.contract = contract          # callable(op_name, args) -> None / raise
        self._lock = threading.Lock()
        self.ops = {n: Operation(n, v["skill"], v["risk"], v.get("params", []))
                    for n, v in self.ops_raw.items()}

    # -- 查询 ------------------------------------------------------------- #
    def describe(self, name: str) -> dict:
        if name not in self.ops:
            raise PolicyDenied(f"{name} 不在白名单（operation_registry）")
        op = self.ops[name]
        spec = self.api.get(op.skill, {})
        return {"operation": name, "skill": op.skill, "risk": op.risk,
                "params": op.params, "syntax": spec.get("syntax", ""),
                "description": spec.get("description", ""),
                "family": spec.get("family"), "contexts": spec.get("contexts", []),
                "evidence": spec.get("evidence", [])}

    def search(self, family: str = "", risk: str = "", prefix: str = "") -> list[dict]:
        out = []
        for n, op in self.ops.items():
            if family and self.api.get(op.skill, {}).get("family") != family:
                continue
            if risk and op.risk != risk:
                continue
            if prefix and not n.startswith(prefix):
                continue
            out.append(self.describe(n))
        return sorted(out, key=lambda d: d["operation"])

    # -- 校验 ------------------------------------------------------------- #
    def _check(self, op: Operation, args: dict) -> None:
        for pat in DENY_PATTERNS:
            if pat in op.skill.lower():
                raise PolicyDenied(f"{op.skill} 命中禁止模式 {pat!r}")
        if op.skill.startswith("_") or op.skill.startswith("__"):
            raise PolicyDenied(f"{op.skill} 是私有函数，默认禁止")
        if op.risk != "read" and not self.allow_write:
            raise PolicyDenied(f"{op.skill} 是写操作，需 allow_write=True 且隔离库")
        for p in op.params:
            name, typ = p.get("name"), p.get("type", "unknown")
            if name is None:
                continue
            if name not in args:
                if p.get("optional"):
                    continue
                raise PolicyDenied(f"{op.skill}: 缺少参数 {name}")
            check = TYPE_CHECKS.get(typ, TYPE_CHECKS["unknown"])
            if not check(args[name]):
                raise PolicyDenied(f"{op.skill}: 参数 {name} 类型应为 {typ}"
                                   f"，实际 {type(args[name]).__name__}")

    # -- 执行 ------------------------------------------------------------- #
    def call(self, name: str, **args) -> dict:
        if name not in self.ops:
            raise PolicyDenied(f"{name} 不在白名单")
        op = self.ops[name]
        self._check(op, args)
        if self.contract is not None and op.risk != "read":
            self.contract(name, args)
        before = None
        cv = args.get("cv")
        with self._lock:                       # 会话串行化，避免并发冲突
            if cv is not None and self.snapshot and op.risk != "read":
                before = self.snapshot(cv)
            last = None
            for attempt in range(self.max_retries + 1):
                try:
                    result = getattr(self.session, "call")(op.skill, *args.values())
                    break
                except Exception as exc:       # noqa: BLE001
                    last = exc
                    if attempt >= self.max_retries:
                        raise
            after = None
            if cv is not None and self.snapshot and op.risk != "read":
                after = self.snapshot(cv)
        diff = None
        if before is not None and after is not None:
            diff = {k: (before.get(k), after.get(k))
                    for k in set(before) | set(after)
                    if before.get(k) != after.get(k)}
        return {"operation": name, "skill": op.skill, "risk": op.risk,
                "result": result, "before": before, "after": after, "diff": diff}
