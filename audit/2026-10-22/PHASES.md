# 六阶段执行结果（2026-10-22，只读 + 隔离库）

## 阶段一：数据审计（完成）

| 项 | 值 |
|---|---|
| 符号总数 | 200,690（唯一 200,683） |
| 函数 | 74,785（与运行时并集**逐项相等**） |
| 变量 | 22,367 |
| 未绑定 | 103,531 |
| 文档（.fnd） | 9,563（其中 7,586 在函数集内、1,977 未加载） |
| 签名（arglist） | 24,267（全部落在函数集内） |
| 特殊符号 | 12,137（非标识符：`#include`、`$` 等，已隔离不入 API） |
| 跨类型重复 | 0 |

产物：`phase1/phase1_audit.json`；原始数据未覆盖。

## 阶段二：语义恢复（完成）

| 项 | 结果 |
|---|---|
| .fnd 解析 | 9,563 条（syntax + description + 手册 + 工具） |
| arglist 关联 | 24,267 条（与 .fnd 合并） |
| 参数来源 | fnd 6,352 / arglist 17,668 / **unknown 50,765（不猜）** |
| 家族归类 | simulation 2,162 · schematic 1,636 · ui 1,009 · database 950 · editor 935 … |
| 证据级 | CALLABLE-only 67,171 · +DOCUMENTED 7,237 · +EXECUTED 306 · +READBACK 43 |

产物：`registry/api_registry.json`。

## 阶段三：能力注册（完成）

六个数据结构全部生成于 `registry/`：

| 结构 | 内容 |
|---|---|
| `api_registry.json` | 74,785 条：id/家族/参数/上下文/证据/置信 |
| `symbol_vocabulary.json` | 22,367 变量 + 103,537 未绑定 + OA 属性 |
| `operation_registry.json` | 377 个已验证操作（risk=read 370 / write 7） |
| `version_capability_matrix.json` | IC2310 文档/载入/执行分层统计 |
| `pdk_capability_matrix.json` | PDK 经 tech/CDF 数据面体现 |
| `execution_policy.json` | 默认拒绝、风险分级、禁止模式、快照要求 |

## 阶段四：Bridge 执行适配（完成，离线+真机验证）

`vskill/gateway.py`：

- 白名单：只允许 `operation_registry` 内条目（377）
- 风险：read 自动；write 需 `allow_write=True`；delete/exit 类**硬禁止**
- 类型检查：按参数类型（object/string/number/list/layerPurpose…）
- 私有函数：`_` 前缀默认拒绝
- 快照：写操作前后 `snapshot(cv)`，返回 diff
- 并发：会话级锁；超时重试可配
- **不接受任意 SKILL 字符串**

## 阶段五：Golden Case（真机通过，隔离库）

流程与证据见 `phase5/evidence.txt`：

PDK 库/单元/CDF 读取 → 建沙箱库 → 打开 schematic → **放置 PDK 器件 tsmcN65/nch** →
设置 w/l → 读回 → `schCheck`(0 err/4 warn) → `dbSave` → 重开读回（1 inst / 4 nets / 属性保留）。

未执行：网表导出对比、Spectre 仿真、Layout XL（列在缺口）。

## 阶段六：对抗验证（部分完成）

| 用例 | 结果 | 判定 |
|---|---|---|
| 错误参数类型（bbox=123） | 返回 nil（被 errset 捕获） | ✅ 拒绝 |
| nil 对象操作 | 返回 nil | ✅ 拒绝 |
| 重复打开（append） | 成功 | 说明 open 是幂等的，非错误 |
| 未加载函数 | `leHiCreateRect` 在 schematic.cxt 加载后**变为可用** | 修正认知：上下文随 schematic 打开而加载 |

未执行：超时、会话并发冲突、仿真失败误判（需要常驻会话与 Spectre）。

## 证据等级对照（本任务）

| 等级 | 数量 | 来源 |
|---|---|---|
| DISCOVERED | 74,785 | symbols.txt |
| DOCUMENTED | 9,563 | .fnd |
| CALLABLE | 74,241（有上下文） | getd 载入矩阵 |
| EXECUTED | 377 | verified.json（真机调用） |
| READBACK_VERIFIED | 43 + Golden Case 6 步 | 返回值/重开读回 |
| ENGINEERING_VALIDATED | Golden Case 数据面链路 | 本阶段 |

## 缺口

1. 网表导出与对比（`createNetlist`/`hnl*`）未跑通；
2. Spectre 运行与 PSF 解析未纳入；
3. ADE/Maestro（`mae*`）未验证；
4. Layout XL（schematic→layout 实例生成）未开始；
5. 常驻会话（daemon）未接入，超时/并发用例未覆盖；
6. 50,765 个函数仍无签名（unknown）。
