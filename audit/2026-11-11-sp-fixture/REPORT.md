# SP 夹具重建（2026-11-11）

## 背景
距上次会话 13 天，主机 `/tmp` 已被清理：`/tmp/cds_audit.lib`、`/tmp/sb*`、会话 socket 全部消失。
`/eda`、`/tmp/pywrap.sh` 仍在。

## 本轮完成
1. **重建库配置** `/tmp/cds_audit.lib`（路径已实测存在）：
```
DEFINE cdsDefTechLib /eda/cds/IC2310/tools/dfII/etc/cdsDefTechLib
DEFINE basic        /eda/cds/IC2310/tools.lnx86/dfII/etc/cdslib/basic
DEFINE analogLib    /eda/cds/IC2310/tools/dfII/src/artist/analogLib
DEFINE tsmcN65      /eda/1P9M/TSMC065_1P9M/tsmcN65
```
2. **重建 SP 测试台** `vlSPTB3/tb`（M0 + 4×vsource + 2×port，全部用 `dbCreateConnByName` 连接，
   `vg.vdc=0.6`、`vd.vdc=1.2`）。
   结果：**`schCheck = (1 0)`** —— 相比上一轮 `(0 16)`，16 条浮空警告已消失，但出现 **1 条 error**。

## 未完成
| 项 | 状态 |
|---|---|
| `(1 0)` 中那条 error 的消息 | **未读取**（本轮未抓 marker） |
| 网表生成 `input.scs` | **未产出**：OCEAN `createNetlist` 运行后目录里只有 `artSimEnvLog/control/designInfo/ihnl`，没有 `input.scs`；原因未定位 |
| SP 重跑（G2） | **未执行**（夹具未通过） |
| SP 读取器 | 保持已验证（`audit/2026-10-28-sp-reader/`，51 点复数 CSV） |

## 结论
- 环境已恢复可运行（Virtuoso/OCEAN 正常启动，建库建图成功）；
- SP 夹具从"16 条浮空"改善到"0 警告 + 1 error"，但仍**未达到可用**；
- 本轮不宣称 SP 工程有效性，不更新 SP 能力状态。

## 下一步（唯一）
1. 读取 `vlSPTB3/tb` 的 marker 消息，定位那 1 条 error；
2. 修好后再跑 `createNetlist` 确认能产出 `input.scs`；
3. 然后才跑 OCEAN SP 并与读取器对接。
