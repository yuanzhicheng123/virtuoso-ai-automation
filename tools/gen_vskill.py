"""gen_vskill —— 从挖矿索引生成 Python API（数据表 + 类型存根）。

    python3 tools/gen_vskill.py
产出：
    vskill/_index.json        运行期用的函数表
    vskill/api.pyi            类型存根（IDE 补全）
    out/vskill_manifest.json  统计
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "vlight" / "_data" / "skill_index.json"
OUT = ROOT / "vskill"

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def main() -> int:
    data = json.loads(SRC.read_text())
    funcs = {}
    for name, meta in data["functions"].items():
        if not IDENT.match(name):
            continue
        args = [a for a in (meta.get("args") or []) if IDENT.match(a) or
                a in ("...",)]
        entry = {"args": args[:12], "syntax": meta.get("syntax", "")[:200],
                 "manual": meta.get("manual", "")}
        if meta.get("verified"):
            entry["verified"] = True
        if meta.get("returns_value"):
            entry["returns_value"] = True
        funcs[name] = entry
    OUT.mkdir(exist_ok=True)
    (OUT / "_index.json").write_text(json.dumps(
        {"count": len(funcs), "functions": funcs}, ensure_ascii=False, indent=0))

    lines = ['"""自动生成的类型存根（do not edit）。"""',
             "from typing import Any, List, Tuple", "", "class SkillFunction:",
             "    name: str", "    args: List[str]", "    def __call__(self, *a: Any, **kw: Any) -> Any: ...",
             "    def skill(self, *a: Any, **kw: Any) -> str: ...", ""]
    for name in sorted(funcs):
        args = funcs[name]["args"]
        if args:
            sig = ", ".join(f"{a}: Any" for a in args)
            lines.append(f"def {name}({sig}) -> Any: ...")
        else:
            lines.append(f"def {name}(*args: Any, **kwargs: Any) -> Any: ...")
    (OUT / "api.pyi").write_text("\n".join(lines) + "\n")

    per = {}
    for m in funcs.values():
        per[m["manual"]] = per.get(m["manual"], 0) + 1
    man = {"functions": len(funcs), "per_manual": per,
           "index_bytes": (OUT / "_index.json").stat().st_size,
           "stub_lines": len(lines)}
    (ROOT / "out" / "vskill_manifest.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print(json.dumps(man, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
