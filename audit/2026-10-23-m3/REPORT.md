# M2.3→M3→M4 实施报告（2026-10-23）

## 一、新增完成项

### 1. SkillBridge 启动修复（Gate 1 通过）
- 根因：`pyStartServer` 默认用 PATH 上的 `python`，继承 Cadence 的 `LD_LIBRARY_PATH`
  → `undefined symbol: _Py_LegacyLocaleDetected` / `No module named 'encodings'`。
- 修复：`/tmp/pywrap.sh`（清 `LD_LIBRARY_PATH/PYTHONHOME/PYTHONPATH` 后 exec conda python）
  + `(pyStartServer ?python "/tmp/pywrap.sh")`；启动侧 `env -u LD_LIBRARY_PATH virtuoso -nograph`。
- 传输确认：**Unix socket** `/tmp/skill-server-default.sock`（非 TCP）。
- 验收：`car`/`length`/`getCurrentTime` 正常；错误调用抛 RuntimeError；
  **连续 100 次 0 错配**（30.3 ms 总耗时）；重连后 `epoch` 递增、探针正常。
- 证据：`skillbridge_fix.md`、`m3_evidence.json`。

### 2. SessionManager（Gate 3 通过）
`vskill/session_manager.py`：会话级串行锁、`(lib,cell,view)` 对象锁、Request ID、
Session Epoch、超时冻结、恢复后核对、请求日志落盘（`/tmp/sm_log*.txt`）。
位于 gateway 之下，**未修改 gateway.py**。

四类故障注入（真实会话）：
| 用例 | 结果 |
|---|---|
| A 并发 8×10 | 80 次 0 错配（串行化生效） |
| B 写入后响应丢失 | `TIMEOUT → FROZEN`；重试被拒；重连后 net 数=2（N0+N_LOST），**无重复写** |
| C 客户端断线 | 自动恢复；epoch 2→3；按名字重新定位 cellview（`cellName="tb"`） |
| D 执行超时（sleep 3, timeout 0.5） | `TIMEOUT → FROZEN`；后续写被拒 |

## 二、证明下一个路径

| 能力 | 证据 | 可进入的下一阶段 |
|---|---|---|
| 真实远程 SKILL 调用 | 100 次 0 错配 + 错误语义 | gateway 可切到常驻会话 |
| 串行/锁/冻结/恢复 | 四类注入日志 | M3 集成：`gateway → SessionManager → bridge` |
| 会话 epoch 与重定位 | `epoch` 递增、`cellName` 回读 | 跨会话不复用 `db:0x` 句柄 |

## 三、失败 / 阻塞 / 未覆盖

| 项 | 状态 |
|---|---|
| schCheck **会话内**消息落盘 | **未完成**（仅计数+日志路径；消息为退出后读取） |
| GUI 参考对照 | **BLOCKED**（无 GUI 交互会话） |
| Tran 定量参考比对 | **未完成**（仅有断言，无参考波形误差） |
| Layout XL | **BLOCKED**：`CDS_LICENSE_FILE`/`LM_LICENSE_FILE` 均未设置，Cadence 许可无法探测；未生成任何版图 |
| PCell 参数/端子回读 | 未执行（依赖 XL） |
| nf>1 / m>1 / 其他器件 | 未验证，已由 `params.py` 拒绝 |
| 跨会话句柄复用 | 已在设计上禁止，未做专门用例 |

## 四、下一步动作（按优先级）

1. **把 SessionManager 接到 gateway 下层**（不改 gateway 代码），用 100 次只读回归验证；
2. **schCheck 会话内落盘**：`hiFlushLogFile` → 读日志 → 退出前写 JSON → 关闭并重读；
3. **Tran 定量参考**：同网表两种 reltol 各跑一次，定义容差并比较；
4. **XL 许可定位**：找 Cadence license 文件/特性名（`Virtuoso_XL`），实际 checkout 成功后再
   用已验证的单指 `wf=500n` DUT 调 `lxGenFromSource`，回读 PCell 参数与端子。
