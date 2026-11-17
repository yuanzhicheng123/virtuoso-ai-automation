"""在真机上跑 smoke.il，收集 @@k=v@@ 结果。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HOST = os.environ.get("VLIGHT_HOST", "eda-220")
ROOT = Path(__file__).resolve().parents[2]
IL = ROOT / "tests" / "live" / "smoke.il"


def main() -> int:
    script = IL.read_text()
    cmd = ("rm -rf /tmp/vl_live && mkdir -p /tmp/vl_live && "
           "cd /tmp/vl_live && cat > .cdsinit <<'EOF'\n"
           "(setShellEnvVar \"CDS_LIB\" \"./cds.lib\")\n"
           "load(\"/tmp/vl_live/smoke.il\")\n"
           "(exit)\nEOF\n"
           "cat > cds.lib <<'EOF'\nDEFINE analogLib $CDSHOME/tools/dfII/etc/cdslib/artist/analogLib\nEOF\n"
           "cat > smoke.il <<'EOF'\n" + script + "\nEOF\n"
           "timeout 300 virtuoso -nograph 2>&1 | tail -80")
    out = subprocess.run(["ssh", HOST, cmd], capture_output=True, text=True,
                         timeout=420)
    text = out.stdout + out.stderr
    print(text[-3000:])
    res = dict(re.findall(r"@@(\w+)=([^@]*)@@", text))
    print("\n== 结果 ==")
    for k, v in res.items():
        print(f"  {k:18s} {v}")
    return 0 if res.get("done") == "t" else 1


if __name__ == "__main__":
    raise SystemExit(main())
