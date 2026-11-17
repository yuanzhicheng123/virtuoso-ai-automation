# R4 / 发布判定（2026-10-26）

## 结论：**V0.1_NOT_READY**

## 本轮真实结果
| 项 | 状态 | 证据 |
|---|---|---|
| 非法器件 pch | **PASS（拒绝）** | `Reject: 器件不在契约内: 'tsmcN65/pch'`；无库目录产生 |
| nf=2 | **PASS（拒绝）** | `Reject: 受保护参数 nf=2 未验证（仅允许 1）`；无库目录产生 |
| 合法 500n | **FAIL（阻塞）** | `ValueError: not enough values to unpack (expected 2, got 1)`，发生在 `ddCreateLib`/`dbOpenCellViewByType` 的返回值解码 |
| R2 连接 | PASS（前轮） | `schCheck=[0,0]`（`audit/2026-10-26-r2/`） |
| R3 新设计重跑 | NOT_RUN | 旧版本网表/仿真已有 METRICS_MET，不能替代新版本 |
| R5 | NOT_RUN | — |
| 8 项操作校准 | NOT_RUN | — |

## 失败定位（本轮新证据）
- 同一会话中所有"返回 OA 对象"的调用都抛 `ValueError`，而标量调用正常；
- 该会话由 `/tmp/sbV` 启动（cds.lib 为 audit 版本），此前 `/tmp/sb9` 会话同类调用正常；
- 因此优先怀疑**会话级差异**（库未真正创建成功 → 返回体结构异常），而非 v01 逻辑。

## 下一步（唯一）
在干净会话（`/tmp/sb9` 同款 cds.lib）重跑合法 500n，确认 `lib/open` 步骤恢复；
恢复后立即补 R2 对抗、自动句柄、单入口与 R5。
