# R2 修复报告（2026-10-26）

## 结论：**R2 PASS（单实例 R0 朝向）**

| 项 | 结果 |
|---|---|
| 初始 | `schCheck=[0,4]`（D/G/S/B 单引脚网络） |
| 首次尝试 | 加 pin + 导线 → `[2,0]`，错误为 `Short between 'D' and 'B'`、`'S' and 'B'`（端口坐标重叠） |
| 修复 | 顶层端口按索引错开（x+3, y+3+2i） |
| 最终 | **`schCheck=[0,0]`，markers 为空** |

## 关键实现
1. 端子几何：`term~>pins[0]~>fig~>bBox` → 中心 → `dbTransformPoint(inst~>transform)`；
2. 端口：`schCreatePin(cv, basic/ipin, name, "input", nil, point, "R0")`；
3. 导线：`schCreateWire(cv, "draw", "full", [pin, port], 0, 0, 0.125)`；
4. 保存 → 重开 → 复查。

## 说明
- 早期 `[0,4]` 的语义是**单引脚网络**，不是绑定失败；
- 之前“加 pin 后变 8”的原因是 pin 未接导线；
- 短路错误的根因是**多个端子的 pin 中心重合**（同一坐标），错开后消除。

## 限制
- 仅验证 R0 朝向单实例；旋转/镜像未验证；
- 未做断线/错接的负向副本（本轮未完成）。
