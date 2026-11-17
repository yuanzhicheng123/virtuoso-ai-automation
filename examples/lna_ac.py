"""从 220 上的真实 Virtuoso 工程导出 -> 本工具网表 -> spectre 跑 AC。

真实工程 tsmcN65_LNA2G4 的顶层有一个遗留问题：M1 的栅极挂在 `n_gate` 上，
而输入匹配网络的输出是 `n_mid`，两者没连。vlight 的 check() 会报出来，
`--fix-gate` 把它接上之后 AC 才有信号。

用法:
    python3 examples/lna_ac.py                        # 远端 eda-220 + 修复栅极
    python3 examples/lna_ac.py --host "" --fix-gate 0
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from vlight import Testbench, ac, dc, pdk, read_dump
from vlight.sim import Simulator


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="eda-220", help="跑 spectre 的机器（空=本地）")
    ap.add_argument("--dump", default="tests/fixtures/tsmcN65_LNA2G4.dump.txt")
    ap.add_argument("--spectre", default="/eda/cds/SPECTRE201/tools/bin/spectre")
    ap.add_argument("--fix-gate", type=int, default=1, help="把 M1.G 接到输入匹配的 n_mid")
    ap.add_argument("--vdd", type=float, default=1.2)
    ap.add_argument("--vb", type=float, default=0.85)
    args = ap.parse_args()

    design = read_dump(args.dump)
    top = design.schematic("tsmcN65_LNA2G4/lna_2g4_top")
    print("实例", len(top.insts), "网络", len(top.nets), "引脚", top.pin_names())
    print("连通性检查:", top.check() or "OK")
    if args.fix_gate:
        top.connect("M1", "G", "n_mid")
        print("已把 M1.G 改接 n_mid ->", top.check() or "OK")

    tb = Testbench(design, "tsmcN65_LNA2G4/lna_2g4_top", flatten=True,
                   title="LNA 2.4G AC (vlight)")
    pdk.apply(tb, pdk.tsmc65("tt"))
    tb.var(vdd=args.vdd)
    tb.option(temp=27)
    tb.element("VDD (vdd 0) vsource dc=vdd",
               f"VB (vb 0) vsource dc={args.vb}",
               "VG (n_gate 0) vsource dc=0",       # 改接后 n_gate 只做偏置/探针
               "VGND (gnd 0) vsource dc=0")
    tb.port("P1", "rf_in")                      # 50Ω，mag=1
    tb.port("P2", "rf_out", mag=0)              # 50Ω 负载，不注入波
    tb.analysis(dc("op"), ac("ac1", start=0.5e9, stop=10e9, dec=20))

    sim = Simulator(spectre=args.spectre, host=args.host or None,
                    workdir="sim", timeout=900)
    res = sim.run(tb, name="lna_ac")
    print("netlist ->", res.workdir / "netlist.scs")
    print("plots:", res.summary()["plots"])

    p = res.plot("ac1")
    s21 = 2 * p.v("rf_out")
    s11 = 2 * p.v("rf_in") - 1
    i = int(np.argmin(np.abs(p.x - 2.4e9)))
    print(f"@2.4GHz  S21={20*np.log10(abs(s21[i])):+.2f} dB   "
          f"S11={20*np.log10(abs(s11[i])):+.2f} dB")
    print(f"峰值 |S21|={20*np.log10(abs(s21).max()):+.2f} dB @ "
          f"{p.x[int(np.argmax(abs(s21)))] / 1e9:.2f} GHz")
    print("指标:", {k: round(v, 3) for k, v in res.metrics({
        "out_db": "dB20(2*V(rf_out))",
        "headroom": "value(V(vdd))",
    }, "ac1").items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
