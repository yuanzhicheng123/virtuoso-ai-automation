# SKILL API 能力普查（只读）— Cadence Virtuoso IC2310 @ eda-220

日期：2026-10-22　范围：只读　产物目录：`audit/2026-10-22/`

## 证据标记

| 标记 | 含义 |
|---|---|
| **D** 文档确认 | 来自 Cadence 安装内的文档/索引（`.fnd`、`doc/`） |
| **R** 运行时确认 | 在真实会话里用无副作用查询（`getd`/`arglist`/`isCallable`）得到 |
| **E** 实际执行验证 | 真机执行并读回结果（有副作用但限沙箱库） |
| **U** 未经验证 | 仅有推断，未取得证据 |

> 本报告不把"函数存在"当作"能力通过"。**D 只说明文档里有；R 只说明会话里加载了；E 才是跑通。**

## 1. 环境识别（D/R）

| 项 | 值 | 证据 |
|---|---|---|
| Virtuoso 版本 | **IC2310**（`/eda/cds/IC2310`） | D（安装路径 + 文档 IC23.1） |
| License | `Virtuoso Framework License (111)` 可 checkout（0.05 s） | R（会话日志） |
| 文档目录 | `/eda/cds/IC2310/doc`（含 `finder/SKILL/**.fnd` 40 个） | D |
| SKILL Finder | 40 个 `.fnd`，共 **9,563** 个函数条目 | D（本目录 `fnd.json`） |
| CDS_FINDER_PATH | **未设置** | R（`echo ${CDS_FINDER_PATH}`） |
| 运行会话 | 46 个 `virtuoso` 进程（多用户），**无监听端口 7000/65432** | R（`ps`/`ss`） |
| Bridge 安装 | `virtuoso-bridge 0.7.0`（editable → `/tmp/virtuoso-bridge-lite/src`） | R（`pip show`） |
| Bridge 可用性 | **不可用**：包被识别为 namespace（`__file__=None`，无 `VirtuosoClient`/`cli`），CLI 报 `ModuleNotFoundError: virtuoso_bridge.cli` | R |
| 备用桥 | `skillbridge 1.5.1`（conda py3.6 env），`client/channel.py` 有 `send()` 与 `'<timeout>'` 处理 | R |

**结论（R）**：当前没有活着的 Bridge 会话；virtuoso-bridge 的安装是坏的（缺 `__init__.py`）。

## 2. `.fnd` 解析（D）

- 位置：`/eda/cds/IC2310/doc/finder/SKILL/<Tool>/<manual>.fnd`
- 格式：Lisp 三元组 `("name" "syntax" "description")`，语法含 `[...]` 可选参数与 `?keyword`
- 解析器：`audit/2026-10-22/tools/fnd_parse.py`（纯只读，s-expression 分词）
- 结果：**40 个文件 / 9,563 个函数**，其中带完整语法的 9,563

对照 deanyou/virtuoso-cli：其 `skill_finder/parser.rs` 面向 `.fnd` 的 `(name "syntax")` 形态；
本解析器额外处理了 `[ optional ]`、`{ enum }`、`?keyword` 与多手册合并。

## 3. 分类（D+R）

| 类别 | 文档数 | 已载入（任一环境） | isCallable |
|---|---|---|---|
| schematic/editor（`sch/hi/ge/de/wsp`） | 1,434 | 1,411 | 1,430 |
| layout/pcell（`le/rod/pc/db/xa/xst`） | 1,432 | 1,396 | 1,411 |
| simulation（`mae/asi/ocn/sim/spectre`） | 751 | **242** | 736 |
| library/tech/cdf（`tech/cdf/dd/dm/tf`） | 494 | 469 | 494 |
| xl/abstract（`axl/lmg/abs/vpm/vfo`） | 461 | **107** | 383 |
| netlist（`hnl/sv/vlog/cdl/spice`） | 125 | **47** | 58 |
| verification（`drc/lvs/erc/diva/assura`） | 17 | 4 | 17 |
| other | 4,849 | 3,910 | 4,773 |

明细：`categories.json`；逐函数：`api_registry.json`。

## 4. `isCallable` 普查（R）——**重要发现**

对 9,563 个文档函数逐一无副作用调用 `(isCallable 'name)`：

| 会话 | 可调用 |
|---|---|
| GUI（schematic 窗口） | **9,303 / 9,563（97.3%）** |
| headless（`-nograph`） | **9,303 / 9,563**（与 GUI **完全一致**） |

**差异为 0**，且 `schCreateWire` 在 headless 里 `isCallable=t`，但同一会话 `getd('schCreateWire)=nil`。

> 结论（R）：`isCallable` 回答的是"这个符号在本版本里是不是一个已知函数"，
> **不是**"本次会话里是否已加载/可执行"。判断可执行性必须用 `getd`，判断能力必须用 E 级执行。

不可调用 260 个，集中在 `hnl*`(67)、`abs*`(51)、`pcU*`(14)、`axl*`(13)、`lmg*`(12)、`sim*`(12)
（工具未安装/许可未勾选/版本裁剪）。清单：`not_callable.txt`。

## 5. 载入矩阵（R，`getd`）

| 环境 | 全部函数 | 其中文档内函数（9,563） |
|---|---|---|
| headless `-nograph` | 48,198 | 6,250 |
| + schematic 窗口（`deOpenCellView`） | 56,512 | 6,941 |
| + layout 窗口 | 71,647 | — |
| **三环境并集** | **74,785** | **7,586** |
| 任何环境都未载入 | — | **1,977** |

`deOpenCellView(lib cell view viewType winSpec mode)` 是加载编辑器上下文的**唯一有效入口**（R+E）。
`deOpen` 是"打开文件"、`hiOpenWindow` 是"打开应用窗口"，都不加载编辑器。

## 6. Bridge 审查（D+R）

| 维度 | 结论 | 证据 |
|---|---|---|
| 执行入口 | `virtuoso_bridge.cli:main`（console script）**不可用**；包内无 `__init__.py` | R |
| 数据序列化 | `skillbridge.client.channel.send(str)->str`，`'<timeout>'` 作为超时哨兵；`translator/objects/remote` 负责句柄 | D（读包源码） |
| 上下文依赖 | 任何调用都依赖"会话里已加载的上下文"——`getd` 为 nil 的函数不能调 | R |
| 超时 | skillbridge 由服务端 `pyStartServer ?timeout X` 控制；客户端只识别哨兵 | D |
| 错误处理 | 远端错误以字符串回传；本地需按哨兵/异常分类 | D |
| 权限控制 | 无内建 ACL；以 Virtuoso 进程所属用户身份执行 | D（无 auth 代码） |
| 安全边界 | **必须在沙箱库操作**；写操作会直接落到 OA | R |

## 7. 不可调用原因（R）

| 原因 | 数量 | 例子 |
|---|---|---|
| 工具未启动/未安装 | 多数 | `hnl*`（网表工具）、`abs*`（abstract） |
| 需要 GUI 编辑器上下文 | 8,314（仅 schematic 窗口解锁） | `schCreate*`、`schHi*` |
| 需要 layout 编辑器上下文 | 23,449（layout 窗口解锁） | `leHi*` |
| 版本/许可裁剪 | 260 | `pcU*`、`axl*`、`lmg*` |
| 任何环境都未载入 | 1,977 | ADE/XL/Assura 相关 |

## 8. 版本兼容矩阵（D，初步）

| 组件 | 版本 | 兼容结论 |
|---|---|---|
| Virtuoso | IC2310 (IC23.1) | 本报告基线 |
| skillbridge | 1.5.1 | 可作为桥（需 GUI 会话 + `pyStartServer`） |
| virtuoso-bridge | 0.7.0（坏） | 修复 `__init__.py` 后可用 |
| deanyou/virtuoso-cli | git main | 仅作参考实现（.fnd 解析、JSON 协议） |

## 9. 优先封装的 30 个高价值操作（D+R；已载入 27/30）

见 `top30.json`。分组：

- **库/数据**：`ddCreateLib` `ddGetObj` `dbOpenCellViewByType` `dbGetOpenCellViews` `dbSave` `dbClose` `dbIsId` `dbCheck`
- **原理图**：`dbCreateInstByMasterName` `dbCreateNet` `dbCreateConnByName` `schCreateWire` `schCreatePin` `schCreateWireLabel` `schCheck`
- **版图**：`dbCreateRect` `dbCreatePath` `dbCreateLine` `dbCreateLabel` `leHiCreateRect` `rodCreateRect`
- **属性/CDF/工艺**：`dbGetPropByName` `dbReplaceProp` `dbDeletePropByName` `cdfGetInstCDF` `techGetTechFile` `techDumpLayerMapping`
- **仿真**：`maeSetAnalysis` `asiRunSimulation` `ocnPrint`

## 10. 后续隔离测试方案（未执行）

| 面 | 测试 | 通过判据 |
|---|---|---|
| 原理图创建 | 沙箱库建 cell → 2 实例 + 网络 + 连接 + 存盘 | 重开计数一致（E） |
| CDF 参数回调 | `cdfGetInstCDF` 读参数；`dbReplaceProp` 改 W/L；再读回 | 值与 CDF 一致 |
| Check & Save | `schCheck` + `dbSave` | 0 error；重开一致 |
| 网表对比 | 导出网表 vs 期望 netlist | 器件/网络集合相等 |
| ADE/Spectre | `mae*` 建分析 → 跑 → 读 PSF | 结果文件存在且数值合理 |
| Layout | `dbCreateRect/Path` + `leHi*` | 图形数/bbox 一致 |
| DRC/LVS | Assura/Calibre 外部跑（SKILL 仅触发） | 违规数与预期一致 |

全部在 `/tmp/vl_audit/<date>/` 沙箱库进行；不写生产库。

## 11. 复现与产物

```bash
python3 audit/2026-10-22/tools/fnd_parse.py /eda/cds/IC2310/doc/finder/SKILL fnd.json
# isCallable 扫描：audit/2026-10-22/tools/sweep_gui.il（GUI）/ sweep_headless.il
```

产物：`fnd.json`（9,563）、`api_registry.json`、`coverage.json`、`categories.json`、
`iscallable_gui.txt` / `iscallable_headless.txt`、`not_callable.txt`、`top30.json`、`REPORT.md`。

## 12. 结论（每句带标记）

1. 文档面 9,563 个函数可从 `.fnd` 完整提取（**D**）。
2. `isCallable` 97.3% 为真，但**与环境无关**，不能当"会话可用"（**R**）。
3. 真实可执行面取决于会话上下文：headless 6,250 / +schematic 6,941 / 并集 7,586（**R**）。
4. 1,977 个文档函数在三种环境里都没加载，需要对应工具/许可（**R**）。
5. 当前 **没有可用的 Bridge 会话**：virtuoso-bridge 0.7.0 安装损坏（**R**）。
6. 真机执行验证过的能力仅 377 个函数 + 17 个场景（**E**），其余为 D/R。
7. 编辑器函数只有在 `deOpenCellView` 打开窗口后才可用（**R+E**）。
