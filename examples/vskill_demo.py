"""演示：用 Python 直接调 SKILL 函数表（离线 mock → 真机只换一行）。

    python3 examples/vskill_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vskill


def main() -> int:
    # 1) 离线：MockBridge 假装是一台 Virtuoso
    mock = vskill.MockBridge({"sprintf": lambda fmt, *a: fmt % a})
    cv = mock.add_object("cv1", {"cellName": "lna_2g4_top", "instances": 14},
                         otype="cellview")
    inst = mock.add_object("M1", {"width": 2e-6, "length": 60e-9}, otype="inst")
    mock.add("dbOpenCellViewByType", lambda lib, cell, view, mode, acc: cv)
    mock.add("dbGetInstByName", lambda cellview, name: inst)
    mock.add("dbCreateInst", lambda cellview, master, name, pt, orient: inst)
    vskill.set_bridge(mock)

    # 2) 函数表：6 千多个名字，全部可调用
    print("可用 SKILL 函数:", len(vskill.FUNCTIONS))
    print("分手册:", vskill.api.coverage()["per_manual"])

    # 3) 调用 + 句柄属性
    cellview = vskill.dbOpenCellViewByType("MYLIB", "lna_2g4_top", "layout", "r", "")
    print("cellview:", cellview, "| cellName:", cellview.cellName,
          "| insts:", cellview.instances)

    inst2 = vskill.dbGetInstByName(cellview, "M1")
    print("inst:", inst2, "| width:", inst2.width)

    # 4) 只生成 SKILL 代码（审计/粘贴到 CIW）
    print("将发送:", vskill.dbCreateInst.skill(cellview, "tsmcN65/nch", "M2",
                                              [0, 0], "R0"))
    print("将发送:", vskill.sprintf.skill("%.3f", 1.2345))

    # 5) 真机（取消注释即可）
    # vskill.set_bridge(vskill.VirtuosoBridgeAdapter(host="eda-220", port=65432))
    # vskill.set_bridge(vskill.SkillbridgeAdapter(host="eda-220", port=7000))
    # vskill.set_bridge(vskill.SubprocessSkillBridge(
    #     binary="/eda/cds/IC2310/tools/dfII/bin/64bit/skill"))
    # print(vskill.hiGetCurrentWindow())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
