"""参数契约（已冻结）：tsmcN65 / nch / spectre / 单指单实例。

已确认（E）：
    simInfo[spectre].propMapping = (nil m simM nf fingers w wf)   → 网表参数是 wf
    only w     -> 网表保持 CDF 默认（200n）
    only wf    -> 网表 = wf
    wf + w     -> 网表 = 写入值
本契约**只**适用于上述器件/仿真器/参数组合，不外推到多指、多实例并联或其他器件。
"""
from __future__ import annotations

CONTRACT = {
    "device": "tsmcN65/nch",
    "simulator": "spectre",
    "view": "schematic",
    "params": ["wf", "w", "l"],
    "fingers": 1,
    "multiplicity": 1,
}

UNSUPPORTED = {
    "nf>1": "未验证：w 与 wf 的多指关系未测",
    "m>1": "未验证：m 与 wf 的组合未测",
    "other_devices": "未验证：仅测过 tsmcN65/nch",
}


class UnsupportedRequest(ValueError):
    pass


def validate(device: str, simulator: str, nf: int = 1, m: int = 1,
             params: dict | None = None) -> None:
    """自动设计入口的闸门：超出已测组合直接拒绝。"""
    if device != CONTRACT["device"]:
        raise UnsupportedRequest(f"器件 {device} 不在契约内（仅 {CONTRACT['device']}）")
    if simulator != CONTRACT["simulator"]:
        raise UnsupportedRequest(f"仿真器 {simulator} 不在契约内")
    if int(nf) != 1:
        raise UnsupportedRequest(UNSUPPORTED["nf>1"])
    if int(m) != 1:
        raise UnsupportedRequest(UNSUPPORTED["m>1"])
    for k in (params or {}):
        if k not in CONTRACT["params"]:
            raise UnsupportedRequest(f"参数 {k} 不在契约内 {CONTRACT['params']}")


def set_width_skill(inst_var: str, value: str) -> str:
    """SKILL：写 wf（网表参数）与 w（显示）。

    注意（实测）：写入后**不要**再调 cdfUpdateInstParam —— 它会把实例参数
    从 CDF 默认值回灌，实测会把 500n 冲回 200n。
    """
    return (f'({inst_var}~>wf = "{value}")\n'
            f'({inst_var}~>w  = "{value}")\n'
            f';; 不要调用 cdfUpdateInstParam: 会回灌 CDF 默认值')
