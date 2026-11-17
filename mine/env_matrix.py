"""env_matrix —— 在多个 SKILL 环境里各 dump 一次函数表，做并集。

    python3 mine/env_matrix.py --host eda-220 --out out/mine/env_matrix

环境由 ENVS 定义；每个环境给出启动命令与可选的 SKILL 前置语句。
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

DUMP = r'''
(let (p n s)
  (setq p (outfile "%OUT%"))
  (setq n 0)
  (foreach s oblist (when (getd s) (fprintf p "%s\n" s) (setq n (add1 n))))
  (close p)
  (printf "@@count=%d@@" n)
  (exit))
'''

ENVS = {
    "nograph": {"cmd": "virtuoso -nograph", "pre": ""},
    "gui_window": {"cmd": "xvfb-run -a virtuoso",
                   "pre": '(ddCreateLib "vlPy" "./vlPy")\n'
                          '(deOpen (dbOpenCellViewByType "vlPy" "inv" '
                          '"schematic" "schematic" "w"))'},
    "pdk": {"cmd": "virtuoso -nograph",
            "pre": '(load "/eda/1P9M/TSMC065_1P9M/skill/tsmcPdkParamTable.il")'},
}


def run_env(host: str, name: str, cfg: dict, out: str, timeout: int = 420) -> int:
    script = cfg["pre"] + "\n" + DUMP.replace("%OUT%", out)
    remote = f'''
T=$(mktemp -d); cd $T
echo "DEFINE analogLib \\$CDSHOME/tools/dfII/etc/cdslib/artist/analogLib" > cds.lib
cat > s.il <<'IL'
{script}
IL
printf '(setShellEnvVar "CDS_LIB" "./cds.lib")\\nload("%s/s.il")\\n' $T > .cdsinit
unset DISPLAY
timeout {timeout} {cfg["cmd"]} 2>&1 | grep -E "@@count" | head -1
wc -l {out} 2>/dev/null
'''
    r = subprocess.run(["ssh", host, remote], capture_output=True, text=True,
                       timeout=timeout + 120)
    print(f"[{name}] {r.stdout.strip()[:120]}")
    return 0 if "@@count" in r.stdout else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="eda-220")
    ap.add_argument("--out", default="out/mine/env_matrix")
    args = ap.parse_args()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    sets = {}
    for name, cfg in ENVS.items():
        path = f"/tmp/vl_env_{name}.txt"
        run_env(args.host, name, cfg, path)
        data = subprocess.run(["ssh", args.host, f"cat {path}"],
                              capture_output=True, text=True).stdout
        (outdir / f"{name}.txt").write_text(data)
        sets[name] = {l.strip() for l in data.splitlines() if l.strip()}
        print(f"[{name}] {len(sets[name])} functions")
    union = set().union(*sets.values()) if sets else set()
    (outdir / "union.txt").write_text("\n".join(sorted(union)) + "\n")
    summary = {k: len(v) for k, v in sets.items()}
    summary["union"] = len(union)
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
