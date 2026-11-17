"""extensions —— Virtuoso 没有、但我们的栈能提供的新函数。

命名一律 `vl*`，避免与 SKILL 命名空间冲突；登记在 registry 的 extension 层，
既能在 SKILL 脚本里直接调，也能反向导出成 Virtuoso 端的 SKILL 包装
（`vlExportSkill()` 会打印对应的 `procedure(...)`）。

    vlNetlist(master)          直接出 spectre 网表（不经过产品 GUI）
    vlLvs(ref cmp)             两个网表的图同构比对
    vlRun(spec_json)           按 JSON 规格跑仿真（自研引擎或 spectre）
    vlExportSkill()            把扩展导出成 SKILL 包装源码
"""
from __future__ import annotations

import json

from .registry import DEFAULT


def _netlist(master: str) -> str:
    """master 形如 "lib/cell"，返回该单元的 spectre 网表。"""
    from .netlist import DeviceMap, Netlister
    design = getattr(DEFAULT, "design", None) or _active_design()
    return Netlister(design, DeviceMap(), strict=False).subckts(master)


def _active_design():
    from .vdb import Design
    from .skill import _LAST_SESSION
    if _LAST_SESSION is not None:
        return _LAST_SESSION.design
    return Design()


def _lvs(ref: str, cmp: str) -> str:
    from . import lvs, spicein
    rep = lvs.compare(lvs.Graph.from_netlist(spicein.read(ref)),
                      lvs.Graph.from_netlist(spicein.read(cmp)))
    return rep.text()


def _run(spec_path: str) -> str:
    from .sim import Simulator
    from .cli import build_tb, load_design
    from types import SimpleNamespace
    spec = json.loads(open(spec_path).read())
    design = _active_design()
    tb = build_tb(design, spec)
    sim = Simulator(engine=spec.get("engine", "vlight"),
                    host=spec.get("host"), workdir=spec.get("workdir", "sim"))
    res = sim.run(tb)
    return json.dumps(res.summary(), ensure_ascii=False)


def _export() -> str:
    lines = [";; vlight 扩展的 SKILL 包装（粘到 Virtuoso 里即可用）"]
    for name, meta in sorted(DEFAULT.extensions.items()):
        args = meta.get("args") or []
        body = "vlProxyCall(\"%s\" list(%s))" % (name, " ".join(args))
        lines.append("procedure( %s(%s) %s )" % (name, " ".join(args), body))
    return "\n".join(lines)


EXPORTS = {
    "vlNetlist": (_netlist, {"master": "lib/cell"}),
    "vlLvs": (_lvs, {"ref": "scs", "cmp": "scs"}),
    "vlRun": (_run, {"spec": "json"}),
    "vlExportSkill": (_export, {}),
}


def install(registry=None) -> int:
    reg = registry or DEFAULT
    for name, (fn, sig) in EXPORTS.items():
        reg.extension(name, fn, {"args": list(sig)}, doc=(fn.__doc__ or ""))
    return len(EXPORTS)


__all__ = ["install", "EXPORTS"]
