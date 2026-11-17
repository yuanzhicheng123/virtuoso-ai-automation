# 验证成果巩固 + AC 入口闭环（2026-10-27）

## 一、本轮新增
| 项 | 结果 |
|---|---|
| AC 经注册入口执行 | ✅ `simulate.run_unified`（run_id=`unified_ac_20261027`） |
| 复数指标 | ✅ `gm_external_ac_S = 6.03725e-4 ∠ -8e-10°`，`Vgs = 1∠0°`，51 点 10 Hz–1 MHz |
| 入口级负向 | ✅ T1 零激励 → REJECTED；T2 非法频率 → REJECTED（均未启动 Spectre） |
| 代码修复 | ✅ `out["metrics"]` 被覆盖的缺陷（改为 `update`） |

## 二、已验证能力总表（截至本轮）
| 能力 | 状态 | 证据 |
|---|---|---|
| 器件契约读取 | EXECUTED | `audit/2026-10-26-cap/capabilities_live.json` |
| 电气快照（D/G/S/B） | EXECUTED | 同上（schCheck=[0,0]） |
| OP/DC/Tran 统一入口 | EXECUTED | `audit/2026-10-26-unified/result.json` |
| AC 统一入口（复数） | EXECUTED | `audit/2026-10-27-ac/result_ac.json` |
| AC A/B 归一化一致 | VERIFIED（1.01e-6） | `ac_compare.json` |
| AC vs DC 斜率一致 | VERIFIED（7.44e-7） | `gm_dc.json` |

## 三、仍未关闭（不计入本轮）
自动句柄、正式单入口接入、R2 对抗、R5 独立参考与收敛性、SP/NF/PSS/HB、
XL/DRC/LVS/PEM；V0.1 总体仍 NOT_READY。

## 四、下一批建议
双端口 SP 表征 + 参考阻抗契约（明确端口定义、参考面与阻抗，区分 S21 与现有 gm 链路）。
