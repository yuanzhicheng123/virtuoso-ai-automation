# SP 夹具：网表已生成但连接未合并（2026-11-12）

## 已确认
1. **网表生成成功**（修复 analogLib 路径 + `connectivityLastUpdated` 置 int 后）：
   `@@ret="/tmp/sbTB5/simulation/tb/spectre/schematic/netlist/input.scs"@@`，文件真实存在。
2. **但连接没有合并**：网表中
   ```
   M0 (net4 net2 net1 net3) nch l=60n w=500n ...
   VD (net7 net8) vsource dc=1.2
   VG (net5 net6) vsource dc=0.6
   P2 (net15 net16) port r=50
   P1 (net13 net14) port r=50
   ```
   7 个实例共 16 个独立 net —— 每个端子一个网络，
   说明 `dbCreateConnByName` 建出的"InTerm→Net"绑定 **没有被网表器当作连通**。
3. 手工把 `connectivityLastUpdated` 置 1 只让网表器"愿意跑"，**不计算真实连通性**。

## 两个未解决的技术点
| 项 | 状态 |
|---|---|
| SKILL 画线脚本 `pinPt` 过程 | **解析错误未解决**（line 9，位置在 `(list (/ (+ (caar bb) ...` 处；内容看起来合法，怀疑读入器对该表达式/字符处理异常） |
| GUI 上下文 `deOpenCellView`（Xvfb :91） | 运行后**无输出**，未得出结论 |

## 根因判断（有证据支撑，但未最终验证）
R2 的经验：**必须在编辑器上下文（deOpenCellView）+ 真实导线** 下，
连通性才会被计算并写进 cellview；纯 nograph + `dbCreateConnByName` 不足以让网表器合并网络。
本轮网表的分裂现象与此完全一致。

## 下一步（唯一）
1. 用 **skillbridge 会话 + `deOpenCellView`**（R2 已验证可行）打开 `vlSPTB5/tb`，
   在会话内 `dbCreateConnByName` + `schCreateWire`（R2 配方），`schCheck` 至浮空为 0；
2. 再用 OCEAN `createNetlist` 验证端口与 M0 合并到同一网络；
3. 之后跑 SP 并用已通过的 `spm` 读取器取 S 参数。
