"""Circuit IR：器件/参数/端子/网络（从 Spectre 网表或 OA 读回构建）。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from vlight import spicein


@dataclass
class Device:
    name: str
    model: str
    terminals: tuple           # (d, g, s, b) 或任意顺序
    params: dict = field(default_factory=dict)

    def json(self) -> dict:
        return {"name": self.name, "model": self.model,
                "terminals": list(self.terminals),
                "params": {k: str(v) for k, v in sorted(self.params.items())}}


@dataclass
class Circuit:
    source: str
    devices: list[Device] = field(default_factory=list)
    nets: set = field(default_factory=set)
    subckts: list = field(default_factory=list)

    def json(self) -> dict:
        return {"source": self.source,
                "devices": [d.json() for d in self.devices],
                "nets": sorted(self.nets),
                "subckts": self.subckts}

    def hash(self) -> str:
        import json
        return hashlib.sha256(json.dumps(self.json(), sort_keys=True).encode()).hexdigest()[:16]


def from_scs(path: str | Path) -> Circuit:
    nl = spicein.parse(Path(path).read_text())
    c = Circuit(source=str(path))
    for e in nl.elements:
        if e.master in ("capacitor", "resistor", "inductor", "vsource", "isource"):
            continue
        c.devices.append(Device(e.name, e.master, tuple(e.nodes), dict(e.params)))
        c.nets.update(e.nodes)
    c.subckts = [s.name for s in getattr(nl, "subckts", [])]
    return c


def from_oa(instances: list[dict], nets: list[str]) -> Circuit:
    """instances: [{name, master, terminals:[...], props:{...}}]（来自 SKILL 读回）。"""
    c = Circuit(source="oa")
    for i in instances:
        c.devices.append(Device(i["name"], i.get("master", ""),
                                tuple(i.get("terminals", [])),
                                dict(i.get("props", {}))))
        c.nets.update(i.get("terminals", []))
    c.nets.update(nets)
    return c
