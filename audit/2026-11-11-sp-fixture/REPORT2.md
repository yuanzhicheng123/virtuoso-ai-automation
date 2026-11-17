# SP 夹具：两个真实缺陷已定位（2026-11-11 续）

## 缺陷 1：analogLib 路径错误（已修复）
现象：`schCheck=(1 0)` 且无 marker 消息；netlist 报
`DB-270337: Failed to open cellview 'port symbol' from library 'analogLib'`。
根因：`cds.lib` 里 `analogLib` 指向 `tools/dfII/src/artist/analogLib`（**不含** `port`）。
实测正确路径：`/eda/cds/IC2310/tools.lnx86/dfII/etc/cdslib/artist/analogLib`（含 `port`）。
修复后：`schCheck=(0 16)`（无 error，只剩 16 条浮空警告）。

## 缺陷 2：`connectivityLastUpdated` 非整数（已修复）
现象：`OSSHNL-108: property 'connectivityLastUpdated' ... does not have an integer value`，
netlist 失败 `OSSHNL-514`。
修复：`dbDeletePropByName` + `dbReplaceProp cv "connectivityLastUpdated" "int" 1`
→ 回读 `value=1`，`schCheck=(0 16)`，`dbSave=t`。

## 缺陷 3（已记录，未处理）：panic log
`vlSPTB3` 目录存在 `panic.log.ytkj.*`，说明该 cellview 曾被崩溃会话持有；
本轮改用新库 `vlSPTB4/5` 绕开，未删除 panic 文件。

## 当前状态
| 项 | 状态 |
|---|---|
| 端口 master 可加载 | ✅ 修复 |
| schCheck error | ✅ 归零（余 16 条浮空 warning） |
| netlist 生成 | 待复验（最后一次 OCEAN 输出未捕获） |
| SP 运行 / 读取 | 未重跑；读取器维持已验证 |

## 下一步
1. 复跑 `createNetlist` 确认 `input.scs` 生成（property 已修）；
2. 用 `schCreateWire` 清掉 16 条浮空（沿用 R2 配方）；
3. 再跑 OCEAN SP → 用已验证的 `spm` 读取器取 S 参数。
