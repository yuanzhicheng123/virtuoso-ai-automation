# 挖出 Cadence 里所有"隐匿"的 SKILL

## 1. 方法

在活的会话里：
```skill
(foreach s oblist          ;; 116,243 个符号
  (when (getd s) ...))     ;; 有函数定义的 48,198 个
```
再和文档索引（6,162）求差 → **文档里没有的隐藏函数**。

## 2. 数字（eda-220 实测）

| 指标 | 数量 |
|---|---|
| 运行时符号 | 116,243 |
| 函数（`getd` ≠ nil） | 48,198 |
| 文档覆盖 | 6,162 |
| **隐藏函数总数** | **43,611** |
| 其中公开（不带 `_`） | **10,424** |
| 内部/私有（`_ci`/`_cp`/`_vr`…） | 33,187 |

## 3. 隐藏的公开 API（按家族，节选）

| 家族 | 例子 | 用途 |
|---|---|---|
| `db*` | `dbCheck`、`dbCompactCellView`、`dbConvertDBUToUU`、`dbDebugStats`、`dbDefineDesignEnvironment`、`dbBackTo422` | 数据完整性检查、单位换算、设计环境定义 |
| `sch*` | `schAddProp`、`schAddLibToBrowser`、`schAddPinListToViewReg` | 原理图属性/浏览器 |
| `le*` | `leCreateRuler`、`leGetLSWBBox`、`leGetTotalLayerList` | 版图编辑器标尺/层选择窗 |
| `tech*` | `techCheckinTechFile`、`techDeleteLP`、`techDisplays`、`techDumpLayerMapping` | 工艺库检入/层映射导出 |
| `cdf*` | `cdfCopyCDF`、`cdfDeleteCDF`、`cdfGetBaseLibCDF`、`cdfIsCEL` | CDF 复制/删除/查询 |
| `pc*` | `pcAdjustConditionalStretches`、`pcAllStretchOffset`、`pcApplyChanges` | **PCell 拉伸引擎**（参数驱动版图） |
| `ge*` | `geAddMemToDisplay`、`geChangeHilightLayer`、`geCheckIfCorrectCV` | 图形编辑器内部 |
| `hi*` | `hiAddLayerColor`、`hiCheckAllForms`、`hiCheckBadDimensions` | UI/表单校验 |
| `alm*` | 1,078 个 | 告警/监控子系统 |

完整清单：[`out/mine/runtime_functions.txt`](out/mine/runtime_functions.txt)（48,198 行）；
隐藏公开清单见 [`out/mine/hidden_functions.json`](out/mine/hidden_functions.json)。

## 4. "能正常使用"怎么保证

挖到名字 ≠ 能用。分三步：

| 步骤 | 手段 | 现状 |
|---|---|---|
| ① 存在性 | `getd` ≠ nil | ✅ 48,198 个 |
| ② 可调用性 | 真机调用一次，看返回/报错 | 🟡 只做了 11 个（`tests/live/`） |
| ③ 正确性 | 与文档/已知结果比对 | 🟡 部分 |

②③ 的规模问题：43,611 个函数不可能都手写用例。可行的自动化：
- **参数个数探测**：对每个函数以 0/1/2… 个参数调用，读报错文本里的
  "too few arguments"/"too many arguments"，得到 arity 表（有副作用，需沙箱）；
- **类型探测**：从 `getd` 的类型（`primop:` vs `funobj:`）与文档签名交叉；
- **风险分级**：只对只读族（`dbGet*`、`techGet*`、`cdfGet*`、`*Info`）自动探测，
  写操作族只做人工用例。

## 4.1 实测：编辑器上下文是**懒加载**的

同一个枚举脚本，在 `-nograph` 和"Xvfb 下的 GUI"里各跑一次，结果**完全相同**（48,198）：

| 会话 | 函数数 | `schCreateWire` |
|---|---|---|
| `virtuoso -nograph` | 48,198 | ❌ 不存在 |
| `xvfb-run virtuoso`（未开窗口） | 48,198 | ❌ 不存在 |

原因：`sch*`/`le*` 的编辑器函数在**打开一个编辑窗口时**才 load 进来。
要收全，必须在 GUI 会话里先 `deOpen`/`hiOpenWindow` 打开一个 schematic/layout，再 dump `oblist`。
（`deOpen`、`hiOpenWindow` 都在已挖到的清单里。）

## 5. 下一步

1. 在 **GUI 上下文**（Xvfb）里再枚举一次，把 `sch*`/`le*` 的交互函数补进来
   （`-nograph` 里 `schCreateWire` 根本不存在，这是已证实的）；
2. 对只读族跑 arity 探测，生成 `out/mine/arity.json`；
3. 把每条验证过的函数写进 `vskill` 的 `verified` 标记，覆盖率从"名字"变成"可用"。

## 6. 边界

- 隐藏 ≠ 稳定：这些函数没有文档承诺，版本升级可能消失。
- 内部函数（33,187）不应暴露给业务代码，只保留在审计清单里。
- 真机验证必须有沙箱库（`/tmp/vlPy`），不要碰生产库。
