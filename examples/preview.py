"""出两张图：原理图 + 电感版图（报告/评审用）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle

from vlight import pcell, render
from vlight.skilldump import read_dump

ROOT = Path(__file__).resolve().parents[1]
d = read_dump(ROOT / "tests/fixtures/tsmcN65_LNA2G4.dump.txt")
sch = d.schematic("tsmcN65_LNA2G4/lna_2g4_top")
lay, met = pcell.make("spiral_ind", cell="IND", nr=2, rad=60e-6, w=10e-6, spacing=3e-6)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), dpi=150)
pos = {i.name: i.xy for i in sch.insts}
for net in sch.nets:
    pts = [pos[i] for (i, _) in net.terms if i in pos]
    for a, b in zip(pts[:-1], pts[1:]):
        ax1.plot([a[0], b[0]], [a[1], b[1]], color="#9aa7bd", lw=0.8, zorder=1)
    if len(pts) == 1:
        ax1.plot(pts[0][0], pts[0][1], "o", ms=2.5, color="#d06060")
for i in sch.insts:
    x, y = i.xy
    ax1.add_patch(Rectangle((x - 26, y - 12), 52, 24, fc="white", ec="#2f3b52", lw=1, zorder=2))
    ax1.text(x, y, i.name, ha="center", va="center", fontsize=7, zorder=3)
ax1.set_aspect("equal"); ax1.axis("off")
ax1.set_xlim(min(x for x, _ in pos.values()) - 60, max(x for x, _ in pos.values()) + 60)
ax1.set_ylim(min(y for _, y in pos.values()) - 40, max(y for _, y in pos.values()) + 40)
ax1.set_title(f"{sch.cell}: {len(sch.insts)} insts / {len(sch.nets)} nets (from Virtuoso via SKILL)", fontsize=8)

for s in lay.shapes:
    if len(s.points) >= 3:
        ax2.add_patch(Polygon(s.points[:], closed=True, fc=render.layer_color(s.layer),
                              ec="none", alpha=0.6))
bb = lay.bbox()
ax2.set_aspect("equal"); ax2.axis("off")
ax2.set_xlim(bb[0], bb[2]); ax2.set_ylim(bb[1], bb[3])
ax2.set_title(f"spiral_ind pcell  L={met['L_H']*1e9:.2f} nH  Dout={met['D_out']*1e6:.0f} um", fontsize=8)
fig.tight_layout()
out = ROOT / "out" / "preview.png"
out.parent.mkdir(exist_ok=True)
fig.savefig(out)
print("->", out)
