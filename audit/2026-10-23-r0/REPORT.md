# V0.1 缺陷修复（R0–R5）状态

## R0 归档与代码一致性：**PASS**
- 远端 `/tmp/v01.py` 与仓库 `vskill/phase2/v01.py` **哈希一致**（`11f037343bc73f3a`）。
- 已归档 23 个文件到 `audit/2026-10-23-r0/`（`manifest.json` 记录 size+sha256），原件保留在远端。
- 未提交改动保留（`git status` 显示大量未跟踪文件，未做任何提交）。

## R1 OCEAN 库可见性：**PASS（缺陷已定位并修复）**
- 根因：`ddCreateLib` 写入 cds.lib 的 DEFINE 是**相对路径** `./<lib>`，相对的是创建它的进程 cwd（`/tmp/sb9`）；
  而网表进程在 `workdir` 下运行，解析到 `workdir/<lib>`（不存在）→ `ADE-5726 Unable to open config`。
- 修复：网表进程 **cwd = 库所在目录**，`HOME=workdir`，`.ocn` 内容为
  `simulator('spectre)` / `design(<lib> <cell> schematic)` / `createNetlist(?recreateAll t)`。
- 实测：`createNetlist` 返回 `/tmp/v01w/a500/simulation/tb/spectre/schematic/netlist/input.scs`，
  `Errors: 0 Warnings: 0`；器件行已生成（见下）。

## R2 四条浮空：**NOT_RUN**
- 真机证据仍在：`schCheck=[0,4]`，markers 含 `Pin "B"/... floating`（`/tmp/v01w/a500/evidence.json`）。
- 本轮未完成对象定位与导线/端口修复。

## R3 500n 电气任务：**PARTIAL**
- 网表已生成并通过结构核对：
  `M0 (net4 net2 net1 net3) nch l=60n w=500n m=1 nf=1 sd=200n ad=3.5e-14`（71 行）
- 器件/模型/宽度正确；**未重跑 Spectre**，未做端序与数值断言。

## R4 网关安全接线：**NOT_RUN**
- `validate_config` 仅在 V0.1 入口层生效，未接入 `gateway.py` 写路径；
- 无跨进程单入口；无自动句柄包装。

## R5 三类验证：**NOT_RUN**

## 结论
**V0.1_NOT_READY**。已修复 R1（网表可见性），R0 归档完成；R2–R5 未完成。
