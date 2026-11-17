# 还有多少可挖：把"挖掘空间"逐面点名

## 1. 已经挖干净的

| 面 | 结果 | 状态 |
|---|---|---|
| `-nograph` 会话的函数表 | 48,198 个（`getd` 非 nil） | **100%**（每个符号都判定过） |
| 其中的签名 | 24,267 条 `arglist` | **100%**（有 arglist 的全拿到） |
| 文档索引 | 6,162 个名字 | 100% |
| 合并 | 31,276 个名字 | 已进库 |
| 真机验证 | 377 可调用 / 49 有返回值 | 持续中 |

结论：**这一个环境里已经没有"没挖到的函数"了**——`oblist` 遍历全量符号，`getd` 判定全量函数。

## 2. 剩下的六个面（本轮实测过，不是猜）

| 面 | 实测 | 为什么还没拿到 | 预期收益 |
|---|---|---|---|
| **① 编辑器上下文**（`schCreateWire`/`leHi*`） | nograph 与"GUI 无窗口"都是 48,198，`schCreateWire` 都不在；`loadContext` 三种写法都无效 | 编辑器上下文在**打开编辑窗口**时才加载 | 约 300–800 个（`sch*`/`le*` 交互函数） |
| **② 其它可执行体** | 只有 `virtuoso`、`viva` 两个 SKILL 二进制；`viva -nograph` 没输出 | `viva` 需要显示/其它参数 | 未知，可能几百 |
| **③ PDK 环境** | 加载 PDK 的 2 个 `.il` 后：**+2 / −313** | PDK 的函数在**打开 PDK 库时**由 CDF/tech 注册，不是 `load .il` | 数百（pcell 回调、CDF 钩子） |
| **④ 属性/方法空间（`~>`）** | 完全没挖 | 属性不是函数，不在 `oblist`；要按类调 `dbGetPropNames`/`techGetProp` 之类 | **最大的一块**，每类几十个属性 × 上百类 |
| **⑤ 加密 `.ile`** | `find` 返回 0（之前 `ls` 见过几个） | 需要精确路径 + 加载后枚举 | 未知 |
| **⑥ C API（`il*`）** | `skdevref` 文档 14 个；共享库里搜不到导出符号 | SKILL 运行时静态链接 | 小（14 个） |

## 3. 这一轮的实测数据（都可复现）

```
nograph                          48,198
GUI（未开窗口）                   48,198   （差集 0）
PDK .il 加载后                    47,887   （+2 / −313）
loadContext("schView")            schCreateWire 仍然不存在
viva -nograph                     无输出
```

## 4. 怎么继续挖（可执行的计划）

| 步骤 | 做法 | 预计产出 |
|---|---|---|
| A | GUI 会话里 `deOpen`/`hiOpenWindow` 真正打开 schematic + layout 窗口，再 dump | 编辑器函数 |
| B | 逐个工具起进程（viva/adexl/assura…），各跑一次 dump，做并集 | 各工具私有函数 |
| C | 打开 PDK 库（`dbOpenCellViewByType "tsmcN65" ...`）后再 dump | PDK 注册的函数 |
| D | 按类枚举属性：对每种 db 对象调 `dbGetPropNames`/`dbGetProp`，建属性字典 | **属性空间**（最大） |
| E | 把 A–D 的并集合并进索引，再跑批量验证 | 覆盖率继续涨 |

## 5. 为什么"完全挖掘"是个多环境问题

SKILL 的函数表是**按上下文加载**的：`oblist` 只反映当前进程里已加载的上下文。
所以"全部函数"= 所有环境（工具 × 模式 × PDK × 编辑器）× 每次 dump 的并集。
单环境已经挖到底，剩下的是**换环境重跑**，不是"漏了函数"。

自动化脚本：`mine/env_matrix.py`（对每个环境跑 dump，输出并集与差集）。

## 6. 多环境矩阵实测（`mine/env_matrix.py`）

| 环境 | 命令 | 函数数 |
|---|---|---|
| nograph | `virtuoso -nograph` | **48,198** |
| pdk | nograph + `load tsmcPdkParamTable.il` | **48,198**（无新增） |
| gui_window | `xvfb-run -a virtuoso` + `deOpen ...` | **0**（前置脚本未成立，窗口没开起来） |

并集仍是 48,198。说明：**换环境这条路，前提是那个环境真的把上下文加载起来**；
`deOpen` 是 `primop`（没有 arglist），参数没猜对，窗口就没开，dump 自然为空。

## 7. 结论：还差的是"环境"和"属性"，不是"函数名"

- `-nograph` 的函数面：**已挖到底**（每个符号都 `getd` 过）。
- 编辑器上下文：需要**真正打开窗口**（`deOpen`/`hiOpenWindow` 的参数要试出来）。
- 属性/方法（`~>`）：**完全没挖**，这是剩下最大的一块。
- 其它可执行体：只有 `virtuoso`/`viva`；`viva -nograph` 无输出，需要显示或别的参数。
