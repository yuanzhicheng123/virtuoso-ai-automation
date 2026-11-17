"""lvs —— 网表对网表的图同构（LVS 的核心算法）。

不做器件识别时，LVS 就是**两张图的同构判定**：
节点 = 电网络，边 = 器件端子。名字不能信（版图提取出来的名字是随机的），
所以要用"颜色细化 + 回溯"：

    1) 给每个网络算签名：连了哪些器件类型、在第几个端子
    2) Weisfeiler-Lehman 式迭代细化颜色（邻居颜色也进签名）
    3) 颜色唯一的直接配对；剩下的用回溯，边试边检查器件一致性
    4) 输出映射 + 差异（多/少器件、网络错连、器件类型不符）

输入可以是 `spicein.Netlist`、`spice.Circuit` 或 vdb 原理图。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from . import spicein


@dataclass
class Device:
    name: str
    kind: str
    nets: tuple[str, ...]

    def key(self) -> tuple:
        return (self.kind, len(self.nets))


@dataclass
class Graph:
    ports: set[str] = field(default_factory=set)
    devices: dict[str, Device] = field(default_factory=dict)
    nets: dict[str, set[tuple[str, int]]] = field(default_factory=dict)

    # -- 构建 ------------------------------------------------------------- #
    @classmethod
    def from_netlist(cls, nl: spicein.Netlist) -> "Graph":
        g = cls()
        g.ports = {n for n in nl.globals if n != "0"}
        for e in nl.elements:
            g.add_device(Device(e.name, _kind(e.master), tuple(e.nodes)))
        for lib_cell in nl.subckts:
            for e in lib_cell.elements:
                g.add_device(Device(f"{lib_cell.name}.{e.name}", _kind(e.master),
                                    tuple(e.nodes)))
        for s in nl.subckts:
            g.ports |= set(s.pins)
        return g

    @classmethod
    def from_circuit(cls, ckt) -> "Graph":
        g = cls()
        for e in ckt.elements:
            g.add_device(Device(e.name, e.kind, tuple(e.nodes)))
        return g

    def add_device(self, d: Device) -> None:
        self.devices[d.name] = d
        for i, n in enumerate(d.nets):
            self.nets.setdefault(n, set()).add((d.name, i))

    # -- 统计 ------------------------------------------------------------- #
    def signature(self) -> dict:
        kinds = defaultdict(int)
        for d in self.devices.values():
            kinds[d.kind] += 1
        return {"devices": len(self.devices), "nets": len(self.nets),
                "ports": sorted(self.ports), "kinds": dict(kinds)}


def _kind(master: str) -> str:
    m = master.lower()
    for k in ("nmos", "pmos", "resistor", "capacitor", "inductor", "diode",
              "vsource", "isource", "vcvs", "vccs", "port"):
        if k in m or m.startswith(k[:2]):
            return k
    if m.startswith(("nch", "nmos", "mn")):
        return "nmos"
    if m.startswith(("pch", "pmos", "mp")):
        return "pmos"
    return master


# --------------------------------------------------------------------------- #
# 颜色细化
# --------------------------------------------------------------------------- #

def _initial_colors(g: Graph) -> dict[str, str]:
    colors = {}
    for net, terms in g.nets.items():
        sig = sorted((g.devices[d].kind, i) for d, i in terms)
        colors[net] = ("P" if net in g.ports else "N") + "|" + \
            ",".join(f"{k}:{i}" for k, i in sig)
    return colors


def _refine(g: Graph, colors: dict[str, str], rounds: int = 4) -> dict[str, str]:
    for _ in range(rounds):
        new = {}
        for net in g.nets:
            neigh = sorted(colors[n] for d, _t in g.nets[net]
                           for n in g.devices[d].nets if n != net)
            new[net] = colors[net] + "#" + ",".join(neigh)
        colors = new
    return colors


@dataclass
class LVSResult:
    ok: bool = False
    mapping: dict[str, str] = field(default_factory=dict)     # g1 net -> g2 net
    matched_devices: dict[str, str] = field(default_factory=dict)
    only_in_ref: list[str] = field(default_factory=list)
    only_in_cmp: list[str] = field(default_factory=list)
    kind_mismatch: list[str] = field(default_factory=list)
    net_mismatch: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"ok": self.ok, "matched_devices": len(self.matched_devices),
                "only_in_ref": self.only_in_ref[:10],
                "only_in_cmp": self.only_in_cmp[:10],
                "kind_mismatch": self.kind_mismatch[:10],
                "net_mismatch": self.net_mismatch[:10],
                "ambiguous_nets": len(self.ambiguous)}

    def text(self) -> str:
        s = self.summary()
        head = "LVS MATCH" if self.ok else "LVS MISMATCH"
        lines = [f"{head}: 器件 {s['matched_devices']} 个配对, "
                 f"网络映射 {len(self.mapping)} 个"]
        for key, label in (("only_in_ref", "只在参考网表"),
                           ("only_in_cmp", "只在待比较网表"),
                           ("kind_mismatch", "器件类型不符"),
                           ("net_mismatch", "连接不符")):
            for item in s[key]:
                lines.append(f"  {label}: {item}")
        if s["ambiguous_nets"]:
            lines.append(f"  颜色有歧义的网络: {s['ambiguous_nets']} 个")
        return "\n".join(lines)


def compare(ref: Graph, cmp: Graph, mapping: dict[str, str] | None = None,
            max_backtrack: int = 200_000) -> LVSResult:
    """ref 为参考（原理图），cmp 为待比较（提取结果）。

    等价性判据：存在一个网络双射，使得两边器件多重集完全相同
    （器件类型 + 每个端子接到的网络）。
    """
    res = LVSResult()
    sig_r, sig_c = ref.signature(), cmp.signature()
    if sig_r["kinds"] != sig_c["kinds"]:
        res.kind_mismatch.append(f"器件统计不同: {sig_r['kinds']} vs {sig_c['kinds']}")

    # 1) 端口同名先钉住（提取出来的 label 通常和原理图 pin 同名）
    seed: dict[str, str] = dict(mapping or {})
    for p in ref.ports & cmp.ports:
        if p not in seed:
            seed[p] = p
    # 端口数量对不上，直接判不符
    if len(ref.ports) != len(cmp.ports):
        res.kind_mismatch.append(f"端口数不同: {len(ref.ports)} vs {len(cmp.ports)}")

    # 2) 颜色细化
    cr = _refine(ref, _initial_colors(ref))
    cc = _refine(cmp, _initial_colors(cmp))
    by_color: dict[str, list[str]] = defaultdict(list)
    for n, c in cc.items():
        by_color[c].append(n)

    cands: dict[str, list[str]] = {}
    for n, c in cr.items():
        if n in seed:
            continue
        opts = [x for x in by_color.get(c, []) if x not in seed.values()]
        cands[n] = opts
        if not opts:
            res.net_mismatch.append(f"{n} 找不到同构网络（颜色 {c[:24]}…）")
        elif len(opts) == 1:
            seed[n] = opts[0]

    # 3) 回溯（按候选数升序，最紧的先试）
    order = sorted((n for n in cands if n not in seed), key=lambda n: len(cands[n]))
    ref_pairs = [(( d.kind, d.nets), d.name) for d in ref.devices.values()]

    def ok_partial(m: dict[str, str]) -> bool:
        """已完整映射的器件，其指纹在 cmp 里的出现次数不能超过实际次数。"""
        need: dict[tuple, int] = defaultdict(int)
        for d in ref.devices.values():
            if all(n in m for n in d.nets):
                need[(d.kind, tuple(m[n] for n in d.nets))] += 1
        have: dict[tuple, int] = defaultdict(int)
        for d in cmp.devices.values():
            have[(d.kind, d.nets)] += 1
        return all(have.get(k, 0) >= v for k, v in need.items())

    budget = [max_backtrack]

    def search(i: int, m: dict[str, str]) -> dict[str, str] | None:
        budget[0] -= 1
        if budget[0] <= 0:
            return None
        if not ok_partial(m):
            return None
        if i == len(order):
            need = defaultdict(int)
            for d in ref.devices.values():
                need[(d.kind, tuple(m[n] for n in d.nets))] += 1
            have = defaultdict(int)
            for d in cmp.devices.values():
                have[(d.kind, d.nets)] += 1
            return m if need == have else None
        net = order[i]
        for cand in cands[net]:
            if cand in m.values():
                continue
            m2 = dict(m)
            m2[net] = cand
            got = search(i + 1, m2)
            if got is not None:
                return got
        return None

    solved = search(0, seed)
    if solved is None:
        res.mapping = seed
        res.ambiguous = order
        res.net_mismatch.append("回溯未能找到一致映射")
        return res

    # 4) 器件配对
    res.mapping = solved
    used: set[str] = set()
    for d in ref.devices.values():
        mapped = tuple(solved.get(n, f"?{n}") for n in d.nets)
        for cd in cmp.devices.values():
            if cd.name in used:
                continue
            if cd.kind == d.kind and cd.nets == mapped:
                used.add(cd.name)
                res.matched_devices[d.name] = cd.name
                break
        else:
            res.only_in_ref.append(f"{d.name}({d.kind}) -> {mapped}")
    res.only_in_cmp = [d.name for d in cmp.devices.values() if d.name not in used]
    res.ok = not (res.only_in_ref or res.only_in_cmp or res.kind_mismatch
                  or res.net_mismatch)
    return res


def compare_files(a: str, b: str) -> LVSResult:
    return compare(Graph.from_netlist(spicein.read(a)),
                   Graph.from_netlist(spicein.read(b)))


__all__ = ["Graph", "Device", "LVSResult", "compare", "compare_files"]
