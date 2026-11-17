# R0–R5 推进（2026-10-26）

## R0 归档与代码一致性：**PASS**（前次）
- 远端/仓库 `v01.py` 哈希一致（`11f03734…`），23 个文件归档于 `audit/2026-10-23-r0/`。

## R1 网表库可见性：**PASS**
- 根因：相对 DEFINE + 网表进程 cwd 不一致。
- 修复：网表进程 cwd=库目录、`HOME=workdir`。
- 证据：`createNetlist` 返回 `input.scs`，`Errors: 0 Warnings: 0`。

## R2 四条浮空：**FAIL（已定位语义，未完成修复）**
- 实测：每个网络 `net~>instTerms` = **1**，即"单端网络"；`dbCreateConnByName` 绑定成功。
- 推论（有证据）：schCheck 的 "Pin … floating" 指**单引脚网络**，不是绑定缺失。
- 反证：用 `schCreatePin(basic/ipin)` 加 4 个顶层引脚但未连导线 → 警告由 4 变 **8**（每个新引脚也浮空）。
- 结论：必须"引脚 + 导线（连到实例端子）"才算连通；本轮未完成几何计算与导线创建。

## R3 500n 电气链：**PASS**
- 新生成网表：`M0 (net4 net2 net1 net3) nch l=60n w=500n m=1 nf=1 sd=200n ad=3.5e-14`（71 行）。
- Spectre 新运行（新目录 `runs/r3_500n`）：`status OK`，`op1.dc / sw1.dc / tr1.tran.tran` 全部生成。
- OP：net4=1.2 V、net2=0.6 V、net1=0、net3=0、`VD:p=-127 µA`。
- 独立验收：`METRICS_MET`，6/6 检查通过（DUT/参数/分析/来源/完整性/数值）。
- 证据：`dut.scs`、`tb.scs`、`runs/r3_500n/`、`acceptance.json`。

## R4 网关安全接线：**PARTIAL**
- 已实现：
  - `Gateway(..., contract=callable)`：写操作在分发前调用契约钩子；
  - `SingleEntry`（flock）：跨进程独占，第二进程实测返回 **BUSY**，释放后可再获取。
- 未完成：V0.1 入口尚未把 `validate_config` 作为 contract 传入并在真实设计写路径上验证；
  `wrong_device/nf>1/fingers` 等拒绝尚未在 gateway 层跑真机用例。

## R5 三类验证：**NOT_RUN**

## 结论
**V0.1_NOT_READY**（R2 FAIL、R4 部分、R5 未做）；R1、R3 已通过。
