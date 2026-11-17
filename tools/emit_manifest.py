"""emit_manifest —— 输出 MCP 形状的工具清单（给 AI/客户端发现用）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vskill.tools_builtin import BUILTIN


def main() -> int:
    m = BUILTIN.manifest()
    out = ROOT / "out" / "vskill_tools.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=1))
    print(f"{m['count']} tools -> {out}")
    for t in m["tools"]:
        print(f"  {t['name']:22s} {t['annotations']['risk']:11s} "
              f"{', '.join(t['inputSchema']['properties'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
