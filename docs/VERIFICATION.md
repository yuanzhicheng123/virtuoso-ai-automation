# 到底验证了什么：真机证据 vs 纸上声明

你测不过是对的——上一轮我把"名字级覆盖"当成了"功能可用"。这里把账摊开。

## 1. 已在真机（eda-220，Virtuoso IC2310）验证的

测试脚本：`tests/live/smoke.il`，跑法：

```bash
python3 tests/live/run_smoke.py          # VLIGHT_HOST=eda-220
```

**11/11 通过**（2026-10-21 实测）：

| 操作 | SKILL 调用 | 结果 |
|---|---|---|
| 建库 | `ddCreateLib("vlLiveLib" "/tmp/vl_live/lib1")` | ✅ `t` |
| 开 schematic（写） | `dbOpenCellViewByType(..., "w")` | ✅ `t` |
| 建实例 | `dbCreateInstByMasterName(cv "analogLib" "nmos4" "symbol" "M0" (list 0 0) "R0")` | ✅ `t` |
| 检查 | `schCheck(cv)` | ✅ `(0 4)`（0 错 4 警） |
| 存 schematic | `dbSave(cv)` | ✅ `t` |
| 开 layout（写） | `dbOpenCellViewByType(..., "maskLayout" "w")` | ✅ `t` |
| 画矩形 | `dbCreateRect(cv (list "text" "drawing") (list 0:0 2:1))` | ✅ `t` |
| 存 layout | `dbSave(cv)` | ✅ `t` |
| 读回图形数 | `length(cv~>shapes)` | ✅ `1` |
| 读回实例数 | `length(cv~>instances)` | ✅ `1` |
| 收尾 | — | ✅ `done` |

结论：**"SKILL 操作 OA 库"这条数据通路是真的**——建库、建实例、画版图、存盘、读回，
全部在真 Virtuoso 上跑通。

## 2. 还没验证的（必须说清楚）

| 项 | 状态 | 原因 |
|---|---|---|
| `vskill` 的 RPC 层（`VirtuosoBridge` → 真 virtuoso-bridge） | ❌ 未测 | 机器上没装 `virtuoso_bridge`，只用假的 client 测过调用形状 |
| 6,151 个函数逐个可用 | ❌ 未测 | 只验证了 11 个数据面函数；其余是名字 |
| GUI 函数（`hi*` / `leHi*`） | ❌ 未测 | 需要带显示的交互会话 |
| `NographBridge`（无 daemon 批处理） | ✅ 真机通过 | `call: plus(1,2)→3`；`batch: [3, 1]`；`eval: strcat→ab` |
| 本地实现（vlight）与真机结果一致 | 🟡 部分 | 前仿/版图对齐是另一条线（8/9、13/13），不是 SKILL 这条 |

## 3. "SKILL + OA 能实现 Virtuoso 全部功能吗"

**不能"全部"。"数据面"几乎全覆盖，"控制面"部分，"引擎"不行。**

| 类别 | 覆盖 | 说明 |
|---|---|---|
| 库/单元/视图 CRUD | ✅ 全 | `dd*` / `db*`（已实测） |
| 原理图/版图对象 | ✅ 全 | `dbCreate*` / `sch*` / `le*`（非交互） |
| 工艺、CDF、参数 | ✅ 全 | `tech*` / `cdf*`（我们已从 techfile 解析 94/200/17） |
| 网表、仿真启动 | ✅ 全 | `netlist` / OCEAN `ocn*` / `asi*` |
| 批量自动化 | ✅ 全 | `virtuoso -nograph` + `load`（已实测） |
| GUI 交互 | 🟡 部分 | `hi*` 需要事件循环；**模态框会阻塞 SKILL 通道**（virtuoso-bridge 文档也这么说） |
| ADE / XL 的自动布局布线 | 🟡 部分 | 引擎跑在 GUI 会话里，只能"发命令 + 等结果" |
| 仿真/EM/DRC/LVS 内核 | ❌ 不能 | 不是 SKILL 实现，只能调外部可执行（spectre/calibre/assura） |
| license、渲染、性能 | ❌ 不能 | 与 SKILL 无关 |

一句话：**SKILL 是"控制面 + 数据面"的 API，不是"引擎"的 API。**
想把"全部功能"都做出来，等于把引擎也重写——那是另一条线（我们已经在做仿真/DRC/提取的替代实现）。

## 4. 直接可复现的两条验证

```bash
# A. 真机数据通路（11 项，已验证）
python3 tests/live/run_smoke.py

# B. 无 daemon 的批处理桥（起 virtuoso -nograph 跑一段 SKILL）
VLIGHT_HOST=eda-220 python3 - <<'PY'
from vskill.bridges.nograph import NographBridge
b = NographBridge(host="eda-220")
print(b.call("plus", [1, 2]))
PY
```

### 4.1 踩到的真机坑（说明"验证"不是走过场）

| 现象 | 根因 | 修法 |
|---|---|---|
| `ddCreateLib` 返回 nil | 参数顺序是 `(libName path)`，我写反 | 改顺序 |
| `dbCreateInstByMasterName` 报 "argument #6 should be a list" | 坐标必须是 `list(x y)`，不是两个数 | 按索引签名 `l_origin` 修 |
| `dbCreateRect` 报 "Invalid layer/purpose" | 新库挂的是默认 techfile，没有 M1 | 用默认 tech 里的层 |
| `nograph` 报 X IO error | 登录 shell 带 `DISPLAY=:6550` | `unset DISPLAY` |
| `nograph` 找不到目录 | 临时目录建在本地，命令在远端跑 | 远端 `mktemp -d` |
| `car((1 2 3))` 报 "not a function - 1" | SKILL 里 `(1 2 3)` 是**函数调用**，不是列表 | 编码器改成 `list(1 2 3)` |
| `dbCreateInst...` 坐标类型不对 | `(0 0)` 是列表，点必须是 `0:0` | 加 `Point(x, y)` 类型 |
| `nograph.eval` 报格式化参数不足 | SKILL 的 `printf` 里也有 `%s`，和 Python `%` 冲突 | 用占位符替换而不是 `%` 拼接 |

这五条就是"文档签名正确 ≠ 调用正确"的实证：索引里 `l_origin` 写了 list，我第一次还是传了两个数。

## 5. 修正上一轮的说法

| 上一轮说的 | 实际 |
|---|---|
| "6,151 个函数 → Python API" | 名字表 100%；**可用性**只验证了 11 个 |
| "封装成库" | 包结构/CLI/批量是真的；RPC 层没在真桥上证过 |
| "100% 覆盖" | 名字级 100%；**行为级** 98.8% 是"真实脚本调用"的统计，不是"每个函数都跑通" |

要把它变成事实，下一步不是继续加名字，而是**按 smoke 的方式给每个高频函数补一条真机用例**，
让覆盖率从"文档统计"变成"真机通过数"。
