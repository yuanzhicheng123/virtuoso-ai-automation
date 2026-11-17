"""coverage —— 覆盖率报告：名字级 / 分层 / 调用级 / 下一步该实现谁。

    python3 tools/coverage.py [--freq recon/skill_fns.txt]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import extensions, skill                     # noqa: E402
from vlight.registry import DEFAULT                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", default=str(ROOT / "recon" / "skill_fns.txt"))
    ap.add_argument("--userdef", default=str(ROOT / "recon" / "skill_userdef.txt"))
    args = ap.parse_args()

    extensions.install(DEFAULT)
    sess = skill.Session()          # 这一步会把已有实现标成 native
    index_total = 5550
    data = json.loads((ROOT / "vlight" / "_data" / "skill_index.json").read_text())
    index_total = data.get("count", index_total)
    cov = DEFAULT.coverage(index_total=index_total)

    print("== 名字级 ==")
    print(f"  索引函数 {cov['index_total']}，已登记 {cov['registered']} "
          f"= {cov['name_level']:.1%}")
    print(f"  分层：{', '.join(f'{k}={v}' for k, v in sorted(cov['tiers'].items()))}")
    print(f"  扩展（Virtuoso 没有的）：{', '.join(cov['extensions']) or '-'}")

    # 按手册看 native 覆盖
    per = defaultdict(lambda: [0, 0])
    for name, e in DEFAULT.entries.items():
        manual = (e.sig or {}).get("manual", "?")
        per[manual][1] += 1
        if e.tier in ("native", "extension"):
            per[manual][0] += 1
    print("\n== 分手册（本地实现 / 该手册函数数） ==")
    for m, (a, b) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"  {m:14s} {a:5d}/{b:5d} = {100*a/b:5.1f}%")

    # 真实脚本调用覆盖率
    freq = Path(args.freq)
    if freq.is_file():
        user = set()
        up = Path(args.userdef)
        if up.is_file():
            user = {l.strip() for l in up.read_text().splitlines() if l.strip()}
        rows = []
        for line in freq.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                rows.append((int(parts[0]), parts[1]))
        builtin = [(n, f) for n, f in rows if f not in user]
        total = sum(n for n, _ in builtin)
        covered = sum(n for n, f in builtin
                      if DEFAULT.has(f) and DEFAULT.entries[f].tier != "emulated")
        print("\n== 真实脚本调用（内置函数） ==")
        print(f"  调用总数 {total:,}，本地覆盖 {covered:,} = {covered/total:.1%}")
        # 下一步该实现谁：按调用频次排，当前还是 emulated 的
        todo = [(n, f) for n, f in builtin
                if DEFAULT.has(f) and DEFAULT.entries[f].tier == "emulated"]
        todo.sort(reverse=True)
        print("\n== 下一步该实现（按真实调用频次） ==")
        for n, f in todo[:15]:
            print(f"  {f:32s} {n:6d}")
        miss = [(n, f) for n, f in builtin if not DEFAULT.has(f)]
        if miss:
            print(f"\n  索引里没有的名字 {len(miss)} 个（挖矿缺口），例如："
                  + ", ".join(f for _, f in sorted(miss, reverse=True)[:8]))

    out = ROOT / "out" / "coverage.json"
    out.write_text(json.dumps(cov, indent=1, ensure_ascii=False))
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
