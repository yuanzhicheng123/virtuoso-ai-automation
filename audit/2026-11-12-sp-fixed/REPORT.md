# SP 夹具修复进展（2026-11-12）

## 一、已完成
| 步骤 | 结果 |
|---|---|
| 会话绑定 | 新会话 `sp2`（`?id "sp2"`），库用**绝对路径** DEFINE |
| 连线（9 对实例端子） | ✅ 全部 ok |
| 端口回流（P1/P2 MINUS→地） | ✅ 补 2 条后 `schCheck = [0, 0]`（**零错零警**） |
| 网表合并 | ✅ 修复前：16 个独立 net；修复后：`VG(net2 net6)`、`VD(net4 net6)`、`M0(net4 net2 net1 net3)`、`P1(net2 …)`、`P2(net4 …)` —— 端口与器件同网 |
| OCEAN SP + spm 读取 | ✅ 51 点，四个复数波形齐全，CSV 已归档 |

## 二、新发现（关键）
修复连接后 S 参数变为 **S11 = −1+0j、S21 = 0**（10 Hz），
而不是之前的 +1：说明端口看到的是**短路**。
原因：`VG`/`VD` 是**理想电压源**，AC 幅值为 0 时在交流域把 G/D 直接钳到地。

这正是任务里预警的"理想偏置源钳住交流"问题。
正确夹具必须：偏置经 **DC feed（大电感）** 进入 G/D，
端口经 **AC 耦合（大电容）** 接 G/D。

## 三、证据
- `sp_export.csv`（当前夹具的真实 S 参数，DIAGNOSTIC_ONLY）
- 网表（远端）：`/tmp/sbTB5/simulation/tb/spectre/schematic/netlist/input.scs`
- 脚本：`/tmp/wire_tb.py`、`/tmp/wire_pmin.py`、`/tmp/ocn_sp2.ocn`

## 四、下一步（唯一）
在 OA 里把偏置改成 bias-tee：
`VG → ind(L 大) → G`、`P1 → cap(C 大) → G`（漏极同理），
重建后 `schCheck=(0,0)` → OCEAN SP → 与之前手写网表结果（S11≈0.9999+j0.016、S21≈−0.060）对照。
