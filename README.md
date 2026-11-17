# RFIC-GPT · Virtuoso AI 自动化能力库

把 Cadence Virtuoso / SKILL / OpenAccess / Spectre 自动化，整理成**可被 AI 发现、安全调用、可度量**的能力库。

## 组成

| 目录 | 作用 |
|---|---|
| `virtuoso_bridge/` | 修复并扩展的 bridge 包（0.8.0）：`Skill` / `GenericSkill` / `ApiRegistry` / 策略 / 可用性判定 / 按需挖签名 |
| `vskill/` | 平台层：`gateway`（白名单+风险）、`session_manager`（串行/epoch/冻结）、`phase2/`（参数契约、网表、仿真、验收、通用原理图操作） |
| `tools/` | `v01_run.py`（V0.1 任务入口）、`skill_cli.py`（受控 SKILL 调用）、`capability_run.py` / `capability_query.py`（能力分发与查询） |
| `registry/` | 工程操作注册表（含输入/输出契约、证据等级） |
| `tests/` | 单元与负向测试 |
| `audit/<date>/` | 真机证据：报告、网表、仿真结果、覆盖率统计 |

## 快速开始

```bash
pip install -e .                      # 安装 virtuoso_bridge
python3 tools/skill_cli.py --why schCreateWire   # 查询可用性
python3 tools/skill_cli.py --fn plus --args 1 2  # 受策略约束的调用
python3 tools/v01_run.py --config cfg.json --workdir /tmp/run  # V0.1 任务入口
```

环境变量：`VB_WORKSPACE`（skillbridge 会话 id）、`V01_SESSION`、`V01_CDS_DIR`。

## 当前能力（可度量）

| 指标 | 数值 |
|---|---|
| SKILL 符号总数 | 74,785 |
| 有签名（可通用调用） | 61,979（82.9%） |
| headless 可调用口径 | 43,341 / 48,198 = **89.9%** |
| 有文档函数 | 99.4% |
| 需 GUI 窗口上下文 | 26,587（已标记 `needs_window`） |
| 抽样实调用成功率 | 97.5%（39/40） |

## 安全模型

- 写操作**默认拒绝**，需显式 `allow_write` 且在白名单内；
- 会话级串行 + 对象锁 + epoch 校验（跨会话句柄一律拒绝）；
- GUI-only 函数自动识别，headless 下明确回报 `NEEDS_WINDOW`；
- `arglist` 不可得的函数进 skip list，不盲目重试。

## 文档

- 证据与结论：`audit/`（按日期分目录，含 `REPORT*.md`）
- 与上游对比：`audit/2026-11-17-compare/REPORT2.md`

## License

内部项目，未附开源许可证（如需公开请先确认）。
