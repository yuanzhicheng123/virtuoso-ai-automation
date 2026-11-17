# 通用调用层覆盖整个 Virtuoso 函数库（2026-11-13）

## 一、实现
`vskill/skillcall.py` —— **注册表驱动**的通用调用层，不写单函数封装：
| 能力 | 说明 |
|---|---|
| 参数还原 | 按 `api_registry.json` 的 arglist 顺序，把 args/kwargs 排成位置参数 |
| 类型转换 | 依据 registry 的 type（`symbol` → `skillbridge.Symbol`，`number/string/list` 原样） |
| 可变参数 | 识别 `...` 形参，不参与必填检查 |
| 可选/择一 | 识别 `{a | nil}` 形式（如 `sprintf`），不误报缺参 |
| 风险分级 | 名称前缀判定 `read`/`write`（`dbCreate/schCreate/ddCreate/...` = write） |
| 批量执行 | `batch()` 逐项记录 ok/value/error |

## 二、覆盖统计（本地 registry 实测）
| 指标 | 数值 |
|---|---|
| 符号总数 | **74,785** |
| 有签名（可直接通用调用） | **24,020（32.1%）** |
| 只读且有签名 | **23,489** |
| 仅 GUI 上下文（无 headless） | **26,587** |
| 无签名（需先补 mining） | **50,765** |

## 三、真机批量验证（session `gen2`）
**第一批 27 个跨族函数：25 成功（92.6%）**
- 数学：`plus/difference/times/quotient/sqrt/abs/max/min` ✅
- 列表：`length/car/cdr/nth/reverse/append/member` ✅
- 字符串：`strlen/upperCase/lowerCase` ✅
- 转换：`atoi/atof/numberp/stringp/listp` ✅
- 环境：`getCurrentTime/getShellEnvVar` ✅
- 失败 2 项：`getpid`（**registry 未收录**）、`sprintf`（`{s_Var|nil}` 择一参数，已修）

**第二批（对象/数据库族）：5/6 成功**
- `ddGetObj("tsmcN65")` ✅、`ddGetLibList` ✅、`cdfGetCellCDF` ✅、
  `envGetVal` ✅、`getVersion` ✅（返回 `IC23.1-64b`）
- `getq` 失败：参数第 2 位应为 symbol（属性名），当前传入被当作 string —— 待修类型推断

## 四、通用性的真实边界（不夸大）
1. **32.1% 覆盖**：只有 24,020 个函数有签名；其余 50,765 个**不能**安全地通用调用
   （参数未知 → 不能猜），需要从 `.fnd` 或运行期 `arglist()` 补 mining。
2. **26,587 个仅 GUI 上下文**：需要示意图/版图窗口，headless 会话不能用；
   不是"不支持"，是"缺上下文"，需在带窗口的会话里验证。
3. registry 有缺项（如 `getpid`），说明 mining 尚未穷尽。
4. 类型推断依赖 registry 的 type 字段，个别函数（`getq`）标注与实际不符 → 需校正。

## 五、证据
- 实现：`vskill/skillcall.py`
- 批量结果：本文件第三、四节（session `gen2`，真实调用）
- 统计：第二节（由 `coverage()` 计算）
