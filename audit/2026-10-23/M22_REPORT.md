# M2.2 可信单指闭环 + M3/Layout XL 状态报告

日期：2026-10-23　环境：eda-220 / IC2310 / Spectre 20.1.0.068 / tsmcN65 nch
隔离库：`/tmp/vlM22b`；证据目录：`audit/2026-10-23/`

## A. 冻结已验证能力（完成）

| 项 | 证据 |
|---|---|
| propMapping / 三实例对照 / 跨进程往返 | `m22_frozen_evidence.json` |
| 契约范围收紧 | `vskill/phase2/params.py`：仅 `tsmcN65/nch` + `spectre` + `wf/w/l` + nf=1, m=1 |
| nf>1、m>1 拒绝 | `validate()` 抛 `UnsupportedRequest`（实测 3 条拒绝） |
| CDF 枚举字段修正 | `cdf~>parameters` = **99**（原 `~>param` 为 0） |
| 不外推 | 文档与契约均注明"仅此组合" |

## B. 可信单指闭环（完成）

### B1 schCheck 证据（部分完成）
- 计数：`(0 4)`；日志路径 `hiGetLogFileName()` → `/root/CDS.log`（会话内取得并写入 `check.json`）
- 4 条警告文本：`Pin "D/B/G/S" on instance "M0": floating input/output.` → 与 `(0 4)` 一致
- **限制**：消息文本是在会话退出后从该日志读取的，未做到"退出前落盘全部消息"

### B2 Spectre 警告分类（完成）
9 行警告 → 5 类：`BENIGN_OPTION_SCOPE`(3)、`BENIGN_OPTION_TEMP`(1)、
`BENIGN_PULSE_PARAM`(2)、`BENIGN_SPECTRE_17101`(1)、`CONTEXT_HEADER`(2)。
未知：**0**（每类都有理由，见 `m22_acceptance.json`）。

### B3 GUI 参考对照（**未执行**）
无 GUI 交互会话；单指 GUI 参考未取得。→ 阻塞项。

### B4 修复后网表仿真（完成）
- 网表：`w=500n`（由修复写链生成，`m22_netlist.scs`）
- 运行：OP / DC sweep / Tran 全部完成（`runs/m22_00/`）
- OP：`net4=1.2V`、`net2=0.6V`、`Id = 126.84 µA`（w=500n，与 1u 的 275 µA 成比例）

### B5 来源/完整性/波形/行为断言（完成）
- `result_source`：网表哈希匹配 ✅
- `analyses`：op1/sw1/tr1 全部存在 ✅
- 波形：时间轴单调、全有限、40 ns、VG 变化 1.0 V、Id 变化 414 µA
- `acceptance.run_all` 状态：**METRICS_MET**，gate=ACCEPTABLE

### B6 真机流程调用 completeness/param_gate（完成）
`run_all()` 已接入：缺检查/UNKNOWN/NOT_RUN → `EVALUATION_INVALID`；参数不一致 → `DIAGNOSTIC_ONLY`（实测触发过一次）。

### B7 负向用例（完成）
`tests/test_acceptance.py` 9/9 通过（含 DUT 缺失、参数不符、缺分析、来源不符、completeness、param gate）。

### 关键新发现（E）
`cdfUpdateInstParam` **在属性写入之后**调用会把实例参数回灌为 CDF 默认值
（实测 500n → 200n）。修复：写入 `wf/w` 后**不要**调用它。`params.py` 已更新。

## C. M3 隔离实测（**阻塞，未执行**）

| 尝试 | 结果 |
|---|---|
| 加载 `python_server.il` | ✅ `(getd 'pyStartServer)` = t |
| `pyStartServer()` | 返回 `(t)` |
| 嵌入式 Python | ❌ `Fatal Python error: init_fs_encoding: No module named 'encodings'` |
| 设置 `PYTHONHOME/PYTHONPATH` 重试 | ❌ 同样失败 |
| 端口 7000/65432 | 无监听 |

→ 无法做串行调度/丢响应/断线/超时四类实验。**阻塞原因**：skillbridge 嵌入式 Python 环境与该机 Python 不匹配。

## D. Layout XL（只读核查，完成）

| 项 | 结果 |
|---|---|
| XL 上下文 | `xlUI.cxt` 存在 |
| PDK 技术库 | `/eda/1P9M/TSMC065_1P9M/Techfile/` 下多种 1p6m/1p9m 变体 |
| tsmcN65 单元 | 目录 821 项（可枚举） |
| XL 许可证 | **未验证**（未做 lmstat 授权探测） |
| 版图实例生成 | **未执行**（按约束，仅在满足条件后做） |

## 未覆盖范围（明确列出）

1. GUI 交互网表 vs 批处理网表对照；
2. `nf>1`、`m>1`、多指/并联公式；
3. 除 `nch` 外的 PDK 器件（pch、rf、无源）；
4. 层次化原理图与多实例阵列；
5. Tran 与参考结果的定量比对（当前只做定性断言）；
6. skillbridge 故障注入（阻塞）；
7. Layout XL 实例生成、PCell 参数回读、端子关系；
8. DRC/LVS；
9. ADE/Maestro；
10. schCheck 消息的"退出前落盘"（当前为退出后读日志）。

## 下一项明确动作

1. 修 skillbridge 启动（用其自带 conda 环境或修 `__init__.py` 的 virtuoso-bridge），再做 4 类故障注入；
2. 在 GUI 会话做单指参考网表对照（1 个 cell，1 次）；
3. 把 schCheck 消息在会话内落盘（`hiFlushLogFile` + 读取 + 退出前写文件）；
4. 取得 XL 许可后，用已验证的 `wf=500n` 单指配置生成隔离版图实例并回读 PCell 参数。
