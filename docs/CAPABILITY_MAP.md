# 能力地图：哪些 SKILL 操作在真机上跑通了

环境：eda-220 / Virtuoso IC2310 / `virtuoso -nograph`（每次调用起一个进程）。
全部结果来自 `tests/live/` 的实际运行，不是推断。

## 1. 已验证（真机返回对象 / 数值）

| 能力 | 调用 | 结果 |
|---|---|---|
| 建库 | `(ddCreateLib "vlPy" "./vlPy")` | ✅ `dd:0x…` |
| 取库对象 | `(ddGetObj "vlPy")` | ✅ |
| 开 schematic（写） | `(dbOpenCellViewByType "vlPy" "inv" "schematic" "schematic" "w")` | ✅ `db:0x…` |
| 开 layout（写） | `(dbOpenCellViewByType "vlPy" "inv" "layout" "maskLayout" "w")` | ✅ |
| 放实例 | `(dbCreateInstByMasterName cv "analogLib" "nmos4" "symbol" "MN" 0:0 "R0")` | ✅ `db:0x…` |
| 建网络 | `(dbCreateNet cv "IN")` | ✅ `db:0x…` |
| 存盘 | `(dbSave cv)` | ✅ `t` |
| 关闭 | `(dbClose cv)` | ✅ |
| 读回数量 | `length(cv~>instances)` / `length(cv~>nets)` / `length(cv~>shapes)` | ✅ 1 / 1 / 1 |
| 画矩形 | `(dbCreateRect cv (list "text" "drawing") (list 0:0 2:1))` | ✅ |
| 路径/标签 | `dbCreatePath` / `dbCreateLabel` | ✅（版图保存成功） |
| 检查 | `(schCheck cv)` | ✅ `(0 4)` |

## 2. 还没跑通（真机报错/未执行）

| 能力 | 尝试的调用 | 现象 | 下一步 |
|---|---|---|---|
| 实例端子连网 | `(dbCreateInstTerm net inst "G")` | 报错（第三个参数要 **term 对象**，不是字符串） | 从 `inst~>master~>terminals` 里取同名 term |
| 同上（按名） | `(dbCreateConnByName net inst "G")` | 未验证——同一脚本里 `mapcar` 先报 "unbound variable" 中止 | 先取 term 对象再连 |
| 画线 | `(schCreateWire cv "draw" "full" (list 0:0 1:0))` | 未执行到 | 需要确认第 2 个参数是网络对象还是名字 |
| 加引脚 | `(schCreatePin cv "IN" "input" nil -1:0 "R0")` | 未执行到 | 用文档里的 6 参形式逐个试 |

## 3. 关键发现：`virtuoso -nograph` 的 SKILL 环境是**裁剪过的**

实测 `mapcar` 报 `unbound variable: mapcar`，`errset` 的行为也和交互态不一致。
这意味着：

- 不能假设"文档里的函数在 nograph 里都能用"；
- 需要一份**环境能力探测**：启动时跑一遍探测脚本，记录哪些函数可用；
- 生产用法应该是"常驻 daemon（skillbridge/virtuoso-bridge）+ nograph 批处理"两条腿，
  而不是只靠 nograph。

## 4. 所以"通过 SKILL 操作 OA 实现全部功能"现在到哪一步

| 层 | 状态 |
|---|---|
| 库/单元/视图 CRUD | ✅ 真机通过 |
| 实例、网络、存盘、读回 | ✅ 真机通过 |
| 版图图元（矩形/路径/标签） | ✅ 真机通过 |
| 端子连接（电气连通性） | ❌ 未通过（缺 term 对象获取） |
| 线/引脚/标签（原理图绘制） | ❌ 未到 |
| GUI、ADE、XL、仿真内核 | ❌ 不在 SKILL 数据面内 |

**结论：数据面（建库、放器件、画图形、存盘、读回）已通；连通性（instTerm/wire/pin）是下一个坎。**
把这块补齐，"用 Python 画原理图/版图"才算真正可用；在那之前，`vskill.design` 只是**能生成脚本**，
不能保证脚本里的每条都成功。

## 5. 复现

```bash
python3 tests/live/design_live.py     # 真机：建库→实例→网络→存盘→版图
python3 tests/live/run_smoke.py       # 真机：11 项基础操作
```

失败时的错误原文会打到 stderr（`*Error* ...`），这就是继续修的依据。
