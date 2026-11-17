"""真机：通过工具层（可发现/可调用/可验证）画出一个反相器。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vskill.bridges.nograph import NographBridge
from vskill.session import Session
from vskill.tools_builtin import BUILTIN


def main() -> int:
    with Session(NographBridge(host="eda-220")) as s:
        BUILTIN.session = s
        ev = BUILTIN.verify("vl.design.inverter", lib="vlTools", cell="inv")
        print(("OK " if ev.ok else "ERR"), ev.tool, "|", "verify=" + str(ev.verified),
              "|", ev.detail[:120])
    rep = BUILTIN.report()
    print(f"调用 {rep['calls']}，成功 {rep['ok']}，验证 {rep['verified']}/{rep['verifiable']}")
    (ROOT / "out" / "tools_live.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if ev.ok and ev.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
