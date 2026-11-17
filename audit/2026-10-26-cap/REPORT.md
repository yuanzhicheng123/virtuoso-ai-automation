# 能力库增量（2026-10-26）

## 一、新增可调用能力

| 类型 | 内容 | 状态 |
|---|---|---|
| 目录补充 | D01–D15 覆盖矩阵（`coverage_matrix.json`） | 已生成 |
| 注册表增量 | `device.contract_read`、`design.electrical_snapshot`、`simulate.run_unified`（10 项） | 已写入 |
| 查询入口 | `tools/capability_query.py`（按需求/类别/风险筛选 + `--explain`） | 已运行 |
| 真实只读能力 | `capabilities.device_contract` / `electrical_snapshot` | 已实现，**本轮真机执行证据未捕获** |
| 真实执行能力 | `simulate`（复用 SpectreRunner + acceptance） | 复用既有，未新增执行 |
| 正式可发布 | 无新增（沿用既有权重） | — |

## 二、证据与调用方式

- 矩阵：`audit/2026-10-26-cap/coverage_matrix.json`
- 注册表：`registry/engineering_operations.json`（10 项）
- 查询：
  ```
  python3 tools/capability_query.py --requirement D03
  python3 tools/capability_query.py --explain device.contract_read
  ```
- 三组能力实现：`vskill/phase2/capabilities.py`

## 三、缺口与阻塞（按功能域）

| 域 | 缺口 |
|---|---|
| D01 需求/拓扑 | 无实现（领域知识 + 算法，非 SKILL） |
| D03 器件契约 | 代码已实现；**真机输出未采集成功**（本轮远端脚本无输出） |
| D07/D08 RF 分析 | 仅候选项；未验证本机 Spectre 许可与选项 |
| D10 PVT/MC | 无统计模型与许可确认 |
| D12 版图/PCell | 许可不可确认（BLOCKED） |
| D13 DRC/LVS/PEX | 无 rule deck（BLOCKED） |
| D14 EM | 无工具（BLOCKED） |
| 其他 | 自动句柄、单入口、R2 对抗、R5 仍 NOT_RUN（沿用旧状态） |

## 四、下一批优先任务

**D03 + D02 的真机取证**：在同一 v01 会话中跑 `device_contract(tsmcN65/nch)` 与
`electrical_snapshot(vlV01_g500/tb)`，把输出写入 audit 并升级证据等级；
完成后即可支撑 D07（AC/SP）的第一批候选验证。
