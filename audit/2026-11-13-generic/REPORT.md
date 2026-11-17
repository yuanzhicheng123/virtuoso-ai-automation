# 通用性验证：声明式 spec → OA 测试台 → OCEAN SP → 复数导出（2026-11-13）

## 一、结论
**通用链路打通**：同一套代码，用一份 JSON spec 生成**全新库/单元**的 SP 测试台
（11 实例、24 条连接、15 条导线），`schCheck=(0,0)`，网表电气正确，
OCEAN 原生 SP + `spm` 导出 51 点复数，数值与手写网表参考一致。

## 二、通用实现（不针对具体 cell 硬编码）
`vskill/phase2/schematic_ops.py`
| 函数 | 作用 |
|---|---|
| `check_spec` | 结构校验：必填字段、实例重名、引用不存在的实例 |
| `terminal_point` | **任意** master/terminal：`dbFindTermByName` → `pins` → `fig~>bBox` → 中心 → `dbTransformPoint` |
| `build` | 建实例（含参数）→ 建网络 → `dbCreateConnByName` → `schCreateWire`；支持 `create_lib` |
| `verify` | `schCheck` 计数 + markers + 每实例端子→网络表 + 导线数 |

spec 驱动示例：`audit/2026-11-13-generic/spec.json`（bias-tee：`VG→Lg→G`、`P1→Cg→G`，漏极同理）。

## 三、真机结果
网表（`/tmp/sbGEN/.../input.scs`）：
```
M0 (net4 net1 net7 net8) nch l=60n w=500n ...
VG (net2 net9) vsource dc=0.6     Lg (net2 net1) inductor l=100
VD (net5 net9) vsource dc=1.2     Ld (net5 net4) inductor l=100
P1 (net3 net9) port r=50          Cg (net3 net1) capacitor c=1
P2 (net6 net9) port r=50          Cd (net6 net4) capacitor c=1
```
→ 端口经电容耦合到 G/D，偏置经电感馈入，源/体接公共地 net9，**拓扑正确**。

SP（10 Hz，51 点 10 Hz–1 MHz）与手写网表参考对比：
| 量 | 通用 OA 路径 | 手写网表参考 | 绝对差 |
|---|---|---|---|
| S11 | 0.999873+0.0159145j | 0.999873+0.0159145j | **1.3e-7** |
| S21 | −0.0601784−0.00095642j | −0.0601762−0.000956385j | **2.2e-6** |

（两条独立生成路径 —— OA/SKILL 与手写网表 —— 在 1e-6 量级一致，
说明"连接语义"与"SP 读取"都正确，不是靠某条路径的巧合。）

## 四、通用性边界（如实）
- 仅验证 **2 端口、50 Ω 实数参考阻抗**；N 端口/复数阻抗未做；
- 仅验证 `analogLib/{vsource,port,ind,cap}` 与 `tsmcN65/nch`；
- `verify` 的导线计数依赖 `getq(cv,"wires")`，本轮实测返回 0（该属性名可能不对），
  改为以网表连通性为准 —— **这是已知缺口**；
- 偏置/端口参数目前按 spec 传入 `dbReplaceProp ... "string"`，未做单位校验。

## 五、证据
`spec.json`、`netlist.txt`、`sp_gen.csv`、`compare.json`
脚本：`/tmp/genb3.py`（构建驱动）、`/tmp/ocn_gen.ocn`（SP 导出）
