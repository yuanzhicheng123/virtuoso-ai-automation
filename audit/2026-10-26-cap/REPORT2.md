# 三组能力真机取证（2026-10-26）

## 执行结果（v01 会话，vlV01_d500/tb）

### 第一组 device_contract("tsmcN65","nch")
| 字段 | 实测值 |
|---|---|
| status | OK |
| terminals（master 顺序） | **[S, G, B, D]** |
| 每端子 | direction=inputOutput，pins=1 |
| CDF 参数数 | **99**（枚举字段 `parameters`） |
| simInfo | 存在 |
| propMapping | `(nil m simM nf fingers w wf)` |
| 已验证契约 | wf 为网表参数、w 为显示；nf=1、m=1；宽度 500n/800n/1u |

**关键发现**：master 端子顺序是 **S,G,B,D**，而网表 termOrder 输出是 **D,G,S,B**。
两者不可混用——这解释了为什么不能假设"端子顺序=接口顺序"。

### 第二组 electrical_snapshot("vlV01_d500","tb")
| 字段 | 实测值 |
|---|---|
| status | OK |
| schCheck | **[0, 0]** |
| instances | M0, PIN3, PIN2, PIN1, PIN0（4 个 basic/ipin 端口） |
| connections | M0.B→B、M0.S→S、M0.G→G、M0.D→D |

连接矩阵与设计规范一致，端口为真实 ipin 实例。

## 证据
- 原始输出：`audit/2026-10-26-cap/capabilities_live.json`
- 注册表已更新：`device.contract_read`、`design.electrical_snapshot` → EXECUTED（2026-10-26）

## 第三组 simulate.run_unified
复用既有 SpectreRunner + acceptance；本轮未产生新的独立运行（上一次合法链的网表为
`M0 (D G S B) nch l=60n w=500n`）。需要一次新运行才能升级为 EXECUTED。
