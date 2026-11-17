# SP 可行性验证（SKILL/OCEAN 框架内，2026-10-27）

## 结论：**框架内可行**（SP 可实现，读取接口待定）

| 步骤 | 结果 |
|---|---|
| `analysis('sp ?start 10 ?stop "1M" ?dec 10 ?ports list("P1" "P2"))` | ✅ OCEAN 接受，`analysis()` 显示 `(ac(sp))` |
| 缺少 port 实例时 | ❌ `SFE-1997: parameter 'ports': invalid component name 'P1'`（说明端口必须是 OA 实例名） |
| 用 SKILL 建测试台（M0 + 4×analogLib/vsource + 2×analogLib/port） | ✅ 全部实例创建成功，`schCheck=(0 16)`，保存成功 |
| OCEAN `run()` | ✅ 返回 `/tmp/sbTB/vlSPTB/simulation/tb/spectre/schematic` |
| `openResults()` / `results()` | ✅ `(sp model instance output designParamVals primitives subckts variables)` |
| 结果文件 | ✅ `psf/sp.sp`（**二进制 PSF**，非 ASCII） |
| `outputs()` | ✅ `("/s11" "/s21" "/s12" "/s22")` —— 四元素齐全 |
| `getData(...)` | ❌ `eval: not a function`（nograph OCEAN 无此函数） |

## 关键结论
1. **不需要手写网表**：SP 完全可以在 OA + OCEAN 里完成；
2. 前置条件明确：schematic 里必须有 **`analogLib/port` 实例**（P1/P2），
   端口名即实例名；缺少时报 SFE-1997；
3. 读取路径要改用 OCEAN 支持的访问器（候选：`ocnGetData`、`v()`、`drGetWaveformYVec`），
   `outputs()` 已证明数据存在且命名规范。

## 下一步（框架内，最小）
1. 在 OCEAN 里试 `ocnGetData("s11" ?result "sp")` / `v("/s11" ?result "sp")`；
2. 确认后，把「SKILL 建 TB（含 port）→ OCEAN 跑 SP → OCEAN 读 S 参数」固化进
   `simulate.run_unified` 的 SP 路径，替换当前手写网表方式；
3. 届时再与 AC-Y 做交叉验证（同一框架内）。

## 证据
`sp.sp`（二进制，已归档）、`/tmp/sp_tb.il`（建台脚本）、`/tmp/ocn_sp.ocn`、`/tmp/ocn_read.ocn`
