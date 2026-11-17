# V0.1 发布判定：**V0.1_NOT_READY**

范围：单 PDK（tsmcN65）/ 单器件（nch）/ 单入口；本报告只记录本轮实际执行。

## A 包：参数契约接入（PARTIAL）

已实施（代码）：`vskill/phase2/v01.py::validate_config` + `guarded_write`
- 库名必须 `vlV01_` 前缀；器件必须 `tsmcN65/nch`；仿真器 spectre；
- 宽度仅 `500n/800n/1u`；`nf/m/fingers/simM` 只允许 1；
- 参数集合只允许 `l/w/wf`；
- 写入后回读比对（wf、w）。

已执行（真机）：`/tmp/v01w/a500/evidence.json` 中
`width True {'wf': '500n', 'w': '500n'}`、`save True`、`reopen True`。
本地拒绝测试：非隔离库、2u、nf=2、fingers=2、pch —— 5/5 被拒。

未完成：**尚未把校验挂进 `vskill/gateway.py` 的写路径**（当前在 V0.1 入口层生效）。
因此 A 不能记 PASS。

## B 包：默认句柄保护 + 单入口（NOT PASS）

- 自动包装：未实现（仅 `SessionManager.guard()` 显式包裹）。
- 旧 epoch 拒绝：已实现并验证（`Handle`/`StaleHandle`）。
- 跨进程单入口：**未实现**（无 flock/租约）。
- 超时冻结：已实现（FROZEN + 拒绝后续写）。

## C 包：检查取证与仿真验收（PARTIAL）

- schCheck 会话内取证：已实现（计数 + markers 的 msg/objects 写入 evidence）。
  真机结果：`counts=[0,4]`，markers 含 `Pin "B"/... floating`。
- **问题**：已连接 D/G/S/B 后仍报 4 条浮空，需进一步定位（未解释完 → 完整性未通过）。
- 三类仿真验证（重复性/独立参考/收敛性）：**未执行**。

## D 包：可重复任务入口（NOT PASS）

入口：`tools/v01_run.py --config cfg.json --workdir DIR`（薄封装→`v0.1.main`）。
真机执行到：建库→开图→建实例→4 网络→4 连接→写宽度→schCheck→保存→重开→关闭（全 True）。
**失败点**：`netlist` 步返回 None（OCEAN `ADE-5726 Unable to open config`），
因此没有器件行、没有 Spectre、没有验收。修了 cds.lib 复制与工作目录后仍未在本轮内跑通。

## 结论

**V0.1_NOT_READY**：A 部分、B 未过、C 部分、D 未过。
按判据不得宣称 READY。
