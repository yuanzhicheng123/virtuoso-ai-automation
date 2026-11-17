"""pdk —— 各家 PDK 的 spectre 模型装配配方（逆向整理）。

TSMC 的模型文件是一个大 library，工艺角不是单个 section，而是**一组** section：
基座参数 + 每类器件的 definition + 对应的 corner 参数。少了任何一个，
spectre 会报 "Unknown parameter `xxx_indfac'" 或 "subckt already defined"。

以 tsmcN65（CRN65GPLUS，1P9M）为例，TT 角正确组合是：

    tt          基座参数（tox/parn1/parp1/...）+ core mos
    tt_rfmos_18 RF MOS 的 corner 参数，内部 include rf_mos_18（定义 nmos_rf_18 等）
    tt_res      电阻 corner 参数，内部 include res（定义 rppolywo 等）
    tt_mim      MIM 电容 corner 参数，内部 include mim（定义 mimcap_sin）
    tt_rfind    电感 corner 参数，内部 include rf_ind（定义 spiral_std_mu_z）

这一步是最容易卡住的地方：只 include `section=tt` 会得到一堆
"referencing an undefined subckt"，而 `tt_rfind` 和 `rf_ind` 同时 include 会得到
"subcircuit with the same name has already been defined"。
"""
from __future__ import annotations

from dataclasses import dataclass

TSMC65_ROOT = "/eda/1P9M/TSMC065_1P9M/models/spectre"
TSMC65_MODELS = f"{TSMC65_ROOT}/crn65gplus_2d5_lk_v1d0.scs"
TSMC28_MODELS = "/eda/pdk/Tsmc28/PDK/PDK1.8_2P2A/models/spectre/crn28ull_1d8_elk_v1d8_2p2.scs"


@dataclass
class ModelSetup:
    path: str
    sections: list[str]
    corner: str = "tt"

    def includes(self) -> list[tuple[str, str]]:
        return [(self.path, s) for s in self.sections]


def tsmc65(corner: str = "tt", models: str = TSMC65_MODELS) -> ModelSetup:
    """tsmcN65 1P9M 的 TT/FF/SS 角装配（1.8V RF MOS + poly 电阻 + MIM + 螺旋电感）。"""
    return ModelSetup(models, [corner, f"{corner}_rfmos_18", f"{corner}_res",
                               f"{corner}_mim", f"{corner}_rfind"], corner)


def tsmc65_sections(corner: str = "tt", devices: tuple[str, ...] = (
        "mos", "res", "mim", "ind")) -> list[str]:
    """按器件子集装配（只要 MOS 时不用拉电感和 MIM 的 section）。"""
    out = [corner]
    if "mos" in devices:
        out.append(f"{corner}_rfmos_18")
    if "res" in devices:
        out.append(f"{corner}_res")
    if "mim" in devices:
        out.append(f"{corner}_mim")
    if "ind" in devices:
        out.append(f"{corner}_rfind")
    return out


def apply(tb, setup: ModelSetup) -> None:
    """把模型装配挂到 Testbench 上（保持顺序，顺序错了会报缺参数）。"""
    for path, section in setup.includes():
        tb.include(path, section)


__all__ = ["ModelSetup", "tsmc65", "tsmc65_sections", "apply",
           "TSMC65_MODELS", "TSMC65_ROOT", "TSMC28_MODELS"]
