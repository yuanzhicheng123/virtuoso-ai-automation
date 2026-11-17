"""vskill 命令行：查函数、看签名、直接求值。

    python -m vskill list --filter dbCreate
    python -m vskill doc dbCreateInst
    python -m vskill eval 'car(list(1 2 3))'        # 需要桥（默认 mock）
    python -m vskill eval --bridge virtuoso 'plus(1 2)'
"""
from __future__ import annotations

import argparse
import sys

from . import api


def _bridge(name: str):
    from .bridge import MockBridge, SubprocessSkillBridge
    if name == "virtuoso":
        from .bridges.virtuoso import VirtuosoBridge
        return VirtuosoBridge()
    if name == "skillbridge":
        from .bridge import SkillbridgeAdapter
        return SkillbridgeAdapter()
    if name == "subprocess":
        return SubprocessSkillBridge()
    return MockBridge()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vskill", description="SKILL 函数表的 Python 入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出函数")
    p_list.add_argument("--filter", default="")
    p_list.add_argument("--manual", default="")
    p_list.add_argument("--limit", type=int, default=50)

    p_doc = sub.add_parser("doc", help="看签名")
    p_doc.add_argument("name")

    p_eval = sub.add_parser("eval", help="求值")
    p_eval.add_argument("code")
    p_eval.add_argument("--bridge", default="mock",
                        choices=["mock", "virtuoso", "skillbridge", "subprocess"])

    args = ap.parse_args(argv)
    if args.cmd == "list":
        rows = [(n, m) for n, m in api.FUNCTIONS.items()
                if args.filter.lower() in n.lower()
                and (not args.manual or m.get("manual") == args.manual)]
        for name, meta in rows[: args.limit]:
            print(f"{name:34s} {' '.join(meta.get('args') or [])[:60]:60s} "
                  f"{meta.get('manual','')}")
        print(f"-- {len(rows)} 个匹配")
        return 0
    if args.cmd == "doc":
        fn = api.get(args.name)
        print(fn.__doc__)
        return 0
    if args.cmd == "eval":
        from .session import Session
        with Session(_bridge(args.bridge)) as s:
            print(s.eval(args.code))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
