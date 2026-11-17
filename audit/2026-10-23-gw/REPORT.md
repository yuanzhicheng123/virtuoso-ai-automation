# 网关集成 + 缺口推进（2026-10-23）

## 一、新增完成项（本轮实际执行）

1. **网关路径端到端（真实会话）**
   `SessionManager`（串行锁/对象锁/epoch）作为调用层，执行：
   `ddCreateLib → dbOpenCellViewByType → dbCreateNet → dbCreateInstByMasterName →
   schCheck → getq(markers) → dbSave → length(nets) → length(instances) → dbClose`，
   全部返回成功；随后按 lib/cell/view 重新定位（`cellName="tb"`）。
2. **权限拒绝（网关策略层）**
   - 未注册操作：`NOT_WHITELISTED:dbDeleteEverything` ✅
   - 未授权写：`WRITE_DENIED:dbCreateNet` ✅
   - 错误器件：**未执行**（gateway 尚未接 `params.validate`）
3. **旧 epoch 句柄拒绝（新增机制并验证）**
   `SessionManager.guard(obj)` 返回带 epoch 的 `Handle`；
   epoch 1 的句柄在 epoch 2 使用 → `StaleHandle` 明确拒绝，提示按库路径/Cell/View 重新定位；
   重新定位后 `cellName="tb"`。
4. **许可来源定位（只读）**
   `CDS_LIC_FILE=/eda/setups/license/Cadence.dat`（284,601 字节）；
   文件内 `INCREMENT` 行数为 **0** → 无法从文件确认 XL feature（不是“无许可”，是**不可确认**）。

## 二、证据路径 / 可进入的下一阶段

| 证据 | 路径 |
|---|---|
| 网关路径与拒绝、epoch | `audit/2026-10-23-gw/A_line_evidence.json` |
| SessionManager（含 Handle/StaleHandle） | `vskill/session_manager.py` |
| 既有故障注入（A/B/C/D） | `audit/2026-10-23-m3/m3_evidence.json` |

下一阶段可进入：**真实设计任务从网关入口跑通**（lib→cell→net→save→netlist→sim 的调度层已就绪），
以及把 `Handle` 规则接入 gateway 返回值。

## 三、失败 / 阻塞

| 项 | 状态 |
|---|---|
| `wrong_device` 拒绝 | **未执行**：gateway 写路径尚未调用 `params.validate` |
| 多进程入口串行化 | **未执行**（线程锁不能代替跨进程证明） |
| schCheck markers 完整落盘 | **部分**：`getq(cv,"markers")` 已调用，未做 messages/objects 结构化落盘 |
| Tran 三类验证 | **未执行**（重复性/独立参考/收敛性） |
| GUI 参考 | **BLOCKED**（无 GUI 会话） |
| XL 许可 | **BLOCKED（不可确认）**：许可文件无 INCREMENT 行，未做 checkout 诊断 |
| XL PCell 生成 | **未执行**（依赖许可确认） |
| 旧句柄自动包装 | 仅 `guard()` 显式包裹，未自动应用于所有返回值 |

## 四、下一步动作

1. 把 `params.validate` 接入 gateway 写路径 → 补 `wrong_device`/`nf>1`/`m>1` 拒绝用例；
2. 双进程客户端测试（同一 socket），验证跨进程互斥或明确拒绝第二控制入口；
3. schCheck：核验 `cv~>markers`/`marker~>msg`/`marker~>objects`，退出前写 JSON 并重读；
4. Tran 三类验证：重复性（新目录）、独立参考（人工网表）、收敛性（reltol 1e-3 vs 1e-6，统一插值）；
5. XL：用 `lmstat` 针对正确 feature 名做一次受限 checkout 诊断；确认后再 `lxGenFromSource`。
