"""真机：用 vskill.design 生成脚本，建库 → 原理图 → 版图 → 存盘 → 读回。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vskill.bridge import Point
from vskill.bridges.nograph import NographBridge
from vskill.design import Design


def wrapped(ops, prefix):
    """逐条执行并打印结果（不用 errset：真机里它对某些调用不生效）。"""
    out = []
    for i, op in enumerate(ops):
        out.append('(printf "@@%s%d=%%L@@" %s)' % (prefix, i, op.code)
                   if not op.code.startswith("(") else
                   '(printf "@@%s%d=%%L@@" %s)' % (prefix, i, op.code))
    return "\n".join(out)


def main() -> int:
    b = NographBridge(host="eda-220")
    d = Design(b)
    lib = "vlPy"
    d.library(lib, "./vlPy")

    sch = d.schematic(lib, "inv")
    sch.inst("analogLib", "nmos4", "symbol", "MN", Point(0, 0))
    sch.inst("analogLib", "pmos4", "symbol", "MP", Point(0, 2))
    sch.net("IN").net("OUT").net("VDD").net("VSS")
    sch.term("IN", "MN", "G").term("IN", "MP", "G")
    sch.term("OUT", "MN", "D").term("OUT", "MP", "D")
    sch.term("VSS", "MN", "S").term("VDD", "MP", "S")
    sch.wire([(0, 0), (1, 0)])
    sch.check()

    lay = d.layout(lib, "inv")
    lay.rect(("text", "drawing"), [(0, 0), (2, 1)])
    lay.rect(("text", "drawing"), [(3, 0), (4, 1)])
    lay.path(("text", "drawing"), [(0, 0), (4, 0)], 0.1)
    lay.label(("text", "drawing"), (0, 0), "VDD")

    setup = [op for op in sch.ops[1:] if op.note in ("inst", "net", "conn")]
    extra = [op for op in sch.ops[1:] if op.note not in ("inst", "net", "conn")]
    code = "\n".join([
        "(let (cv)",
        '  (printf "@@lib=%%L@@" (ddCreateLib "%s" "./vlPy"))' % lib,
        "  " + sch.ops[0].code,
        wrapped(setup, "s"),
        '  (printf "@@sch_insts=%L@@" length(cv~>instances))',
        '  (printf "@@sch_nets=%L@@" length(cv~>nets))',
        '  (printf "@@sch_save=%L@@" dbSave(cv))',
        wrapped(extra, "x"),
        "  " + lay.ops[0].code,
        wrapped(lay.ops[1:], "l"),
        '  (printf "@@lay_shapes=%L@@" length(cv~>shapes))',
        '  (printf "@@lay_save=%L@@" dbSave(cv))',
        "  (dbClose cv)",
        "  (printf \"@@done=t@@\")",
        ")",
    ])
    res = b.run_script(code)
    for k in sorted(res):
        print(f"{k:12s} {res[k]}")
    return 0 if res.get("done") == "t" else 1


if __name__ == "__main__":
    raise SystemExit(main())
