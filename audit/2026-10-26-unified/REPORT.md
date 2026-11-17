# simulate.run_unified 真实执行（2026-10-26）

## 一、结论：**UNIFIED_RUN_VERIFIED**

| 项 | 结果 |
|---|---|
| 经过注册入口 | ✅ `tools/capability_run.py --operation simulate.run_unified` |
| 真实启动 Spectre | ✅ 新 Run 目录、新进程、退出 OK |
| 结果解析 | ✅ op1.dc / sw1.dc / tr1.tran.tran |
| 现有验收 | ✅ 数值/来源/完整性检查通过 |

## 二、本次实测
- Run ID：`unified_500n_20261026`
- DUT：`M0 (D G S B) nch l=60n w=500n m=1 nf=1`
- OP：D=1.2V、G=0.6V、S=0、B=0
- 电流：`VD:p = -126.835 µA`，`VS:p = +126.831 µA`（方向一致、量级匹配）
- evidence_id：`runs/unified_500n_20261026`

## 三、防误报测试
| 测试 | 结果 |
|---|---|
| T1 交换 D/G 绑定 | **REJECTED**（`PORT_BINDING_MISMATCH`，Spectre 未启动） |
| T2 删除 tr1 后按 op+tran 验收 | **EVALUATION_INVALID**（`analyses=False`，缺 tr1 被检出） |

## 四、限制
- 仅 OP/DC/Tran；未做 AC/SP/PSS/HB。
- 未做独立参考与收敛性（R5 仍未完成）。
- 未改变 V0.1 总体状态（仍 NOT_READY）。
