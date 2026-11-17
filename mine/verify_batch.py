"""verify_batch —— 批量真机验证：对只读族函数按签名试调用，记录成功/错误。

原理：errset 的正确用法是 `(errset expr)`：
    成功 → 返回 (value)（列表，非 nil）
    失败 → 返回 nil
所以一次探测就能区分"能调用"和"不能调用"。

用法（在 EDA 机器上或通过 NographBridge）：
    python3 mine/verify_batch.py --host eda-220 --limit 300
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PREFIXES = ("dbGet", "dbIs", "dbFind", "dbCheck", "dbConvert", "dbQuery",
            "techGet", "cdfGet")


def candidates(path: Path) -> list[tuple[str, list[str]]]:
    al = json.loads(path.read_text())
    out = []
    for name, meta in al.items():
        if not name.startswith(PREFIXES):
            continue
        args = [a for a in meta["args"] if a not in ("@optional", "@rest", "@key")]
        if not args:
            continue
        out.append((name, args))
    return out


SAMPLE = {
    "dbobject": "cv", "d_cellView": "cv", "d_cellview": "cv", "d_net": "net",
    "d_inst": "inst", "t_string": '"MN"', "t_name": '"MN"', "t_libName": '"vlPy"',
    "t_cellName": '"inv"', "t_viewName": '"schematic"', "t_termName": '"G"',
    "g_general": "nil", "l_point": "0:0", "l_points": "(list 0:0 1:0)",
    "l_bbox": "(list 0:0 1:1)", "l_origin": "0:0", "t_orient": '"R0"',
    "txl_layerPurpose": '(list "text" "drawing")',
}


def sample_for(arg: str) -> str:
    if arg in SAMPLE:
        return SAMPLE[arg]
    if arg.startswith(("d_", "db")):
        return "cv"
    if arg.startswith("t_"):
        return '"MN"'
    if arg.startswith(("l_",)):
        return "(list 0:0 1:1)"
    if arg.startswith(("x_", "n_", "s_")):
        return "1"
    return "nil"


SETUP = """(ddCreateLib "vlPy" "./vlPy")
(let (cv inst net)
  (setq cv (dbOpenCellViewByType "vlPy" "inv" "schematic" "schematic" "w"))
  (setq inst (dbCreateInstByMasterName cv "analogLib" "nmos4" "symbol" "MN" 0:0 "R0"))
  (setq net (dbCreateNet cv "IN"))
"""


def build_script(items: list[tuple[str, list[str]]], out_path: str) -> str:
    lines = [SETUP, '  (let (p r)', '    (setq p (outfile "%s"))' % out_path]
    for name, args in items:
        vectors = []
        for target in ("cv", "inst", "net"):
            vec = [sample_for(a).replace("cv", target, 1) if sample_for(a) == "cv"
                   else sample_for(a) for a in args]
            vectors.append(vec)
        seen = set()
        for vec in vectors:
            key = tuple(vec)
            if key in seen:
                continue
            seen.add(key)
            call = "(" + name + (" " + " ".join(vec) if vec else "") + ")"
            lines.append('    (setq r (errset %s))' % call)
            lines.append('    (fprintf p "%%s|%%L|%%L\\n" "%s" (if r "ok" "err") '
                         '(if r (car r) nil))' % name)
    lines += ["    (close p)", '    (printf "@@done=t@@")', "  )", ")"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="eda-220")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--out", default="/tmp/vl_verify.txt")
    ap.add_argument("--local-out", default="out/mine/verify_results.txt")
    args = ap.parse_args()

    items = candidates(ROOT / "out" / "mine" / "arglists.json")[: args.limit]
    script = build_script(items, args.out)

    from vskill.bridges.nograph import NographBridge
    bridge = NographBridge(host=args.host)
    res, text = bridge._run(script)
    print("markers:", res)

    import subprocess
    data = subprocess.run(["ssh", args.host, "cat %s" % args.out],
                          capture_output=True, text=True).stdout
    (ROOT / args.local_out).write_text(data)
    rows = [l.split("|") for l in data.splitlines() if l.count("|") >= 2]
    def clean(x):
        return x.strip().strip('"')
    ok = [r for r in rows if len(r) >= 3 and clean(r[1]) == "ok"]
    print(f"探测 {len(rows)} 条，成功 {len(ok)} 条，覆盖 {len({r[0] for r in ok})} 个函数")
    for r in ok[:15]:
        print("  ok:", r[0], "->", clean(r[2])[:60])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
