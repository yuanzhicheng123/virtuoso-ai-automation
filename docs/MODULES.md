# 模块覆盖矩阵（IC2310 的 119 个 tool → vlight）

`/eda/cds/IC2310/tools/dfII/etc/tools` 下 119 个目录就是 Virtuoso Studio 的功能模块清单。
下表按「设计者真正会碰到的」排序，标出复用到什么程度、代码在哪、怎么验证。

图例：**✅ 复用**（能替代）/ **🟡 简化**（核心语义在，精度/覆盖面缩水）/ **⬜ 未覆盖**

## 1. 原理图侧

| 模块 | 状态 | 实现 | 验证 |
|---|---|---|---|
| `schematic`（原理图编辑器） | ✅ | `vdb.Schematic`：inst/net/pin/wire、`connect()`、层次、`check()`；`skill` 的 `dbCreate*` 可直接建图 | 真实 LNA 4 cell 导入 + 网表；SKILL 脚本建分压器 |
| `symbol` | 🟡 | 复用 `Schematic` 的 pins 作为符号端子，无图形符号绘制 | 层次网表按引脚顺序出线 |
| `cdf`（参数引擎） | ✅ | `cdf.CDF/CDFdb`：默认值、实例覆盖、`iPar`/`pPar`、`netlistProcedure` 分派 | 从真实 dump 建 7 个 master / 88 参数；只给 `l` 也能补出 `w=10u mf=1` |
| `pinCheck` / `schCheck` | 🟡 | `Schematic.check()`：悬空/单端/重复名/引脚未连；`skill.schCheck` | 抓出真实工程 `M1.G` 悬空 |
| `calculator` | ✅ | `calc`：白名单 AST，`dB20/phase/deriv/integ/cross/at` | 与解析解比对（3e-3 内） |
| `cdsLibManager` / `cdsLibEditor` / `ddserv` / `dcdf` | 🟡 | `vdb.Library`（`LIB/CELL/VIEW/`）+ `cds.lib` 解析（DEFINE/INCLUDE/`$VAR`） | 真实工程往返存取 |
| `designEditor` / `textedit` / `graphic` | ⬜ | 无 GUI，SVG/HTML 视图替代 | — |

## 2. 仿真侧（ADE 家族）

| 模块 | 状态 | 实现 | 验证 |
|---|---|---|---|
| `ade` / `adle`（ADE-L 状态） | ✅ | `ade.ADELState`：analyses/variables/outputs/model files；**能直接读真实 `*.state`**（当 SKILL 执行） | 导入 CSPA 状态：`dc+sp`、`temp=85`、13 变量、4 个 output 表达式（`ymin(b1f(sp(1,1,...)))`） |
| `adexl` / `maestro` / `adexlDM`（多测试台/角/回归） | 🟡 | `sim.run_sweep`：笛卡尔积 + 多线程 + CSV + `best()` | 分压器扫 R2 → 1.5/2.0/2.25 V |
| OCEAN（`ocean` 命令 / ASE） | ✅ | `ocean.Ocean`：`analysis/desVar/run/selectResult/ocnPrint/ymax/cross/dB20`，脚本就是 SKILL | `foreach` 扫三个阻值，逐点打印 |
| `spectre` / `cdba` / `hspiceD`（仿真器接口） | 🟡 | `tb`+`netlist`+`sim`：spectre 网表、local/ssh 执行、PSF 回读 | 真实 PDK 模型跑通，0 error；分压器/RC 数值对得上 |
| `spectreVerilog` / `ams` / `UltraSim*`（混合信号） | ⬜ | 无 | — |
| `viva`（波形） | 🟡 | `psf.Plot` + `calc` + `quickplot`/SVG；无交互式示波器 | npz/csv 导出、PNG |
| `asimenv` / `mmsimenv` / `license` | 🟡 | 环境拼装（`pdk.apply` 五个 section）、ssh 远端执行 | tsmcN65 TT 角跑通 |

## 3. 版图侧

| 模块 | 状态 | 实现 | 验证 |
|---|---|---|---|
| `layout`（版图编辑器） | 🟡 | `vdb.Layout`：layer/purpose 多边形 + 实例 + label | pcell 生成、SVG 渲染 |
| `layoutXL`（连接性驱动） | 🟡 | `connect.extract`：同层接触 + via 栈并网 + label 命名 → 节点表；短路告警 | 合成版图提取出 `IN/VDD` 节点，检出错接 |
| `rod` / `pcellide` / `xpcell`（pcell） | 🟡 | `pcell.SpiralInd/MimCap/PolyRes/Mos4`：参数→几何，Wheeler 电感公式 | 58 图形 / 0.63 nH / Dout=120 µm |
| `xstream`（GDS 流） | ✅ | `gdsio`：自写 GDSII 读写（SREF 层次、excess-64） | KLayout 0.30.5 读回 1 cell / 58 shapes / 真实层号 |
| `spiceIn`（网表导入） | ✅ | `spicein`：spectre/spice 解析（续行/注释/subckt/analysis/params） | 真实 VCO(10 器件) 与 OTA 网表往返；导入后能直接仿真 |
| `auCdl`（CDL 网表） | ✅ | `Netlister.cdl`：`.SUBCKT/.ENDS` + 器件卡 | 与 spectre 网表同源，LVS 交接 |
| `techfileIDE` / `techcheck` | 🟡 | `TechFile` + `layermap` 解析 + `stream()` 映射 | tsmcN65.layermap 484 行/158 层 |
| `layoutMigrate` / `layoutOptimize` / `layoutDP` / `layoutEAD` | ⬜ | 无（工艺迁移/DP） | — |

## 4. 验证与提取

| 模块 | 状态 | 实现 | 验证 |
|---|---|---|---|
| `Diva` / `apkDrc` / `drdChecker` / `drdEdit` | 🟡 | `drc.Deck/check`：min_width / min_space / min_area / grid / enclosure | 合成版图三条违规全中；pcell 干净 |
| `auLvs` / `verifier` / `vfp` | 🟡 | `connect.lvs`：按 label 对 net，报 missing/extra/short/open | 短路与缺网都能报出 |
| `qrc` / `rcm`（寄生提取） | 🟡 | `extract.py`：方块电阻 + 面积/周长/耦合电容 + 2D 有限差分场求解 | 100×1µm 走线 R=10.000Ω；1×1µm 微带 4.1e-10 F/m |
| `emir`（IR drop/EM） | ⬜ | 无（有提取网络后可自己算 IR） | — |
| `constraint` / `cpf` / `csm`（约束/低功耗） | ⬜ | 无 | — |

## 5. 语言与脚本

| 模块 | 状态 | 实现 | 验证 |
|---|---|---|---|
| `SkillIDE` / `sklint` / `slt`（SKILL） | 🟡 | `skill`：读取器（`f(x)` vs `f (x)`、`a~>b`、`a->b`、`x = e`、`'quote`）+ 求值器（`procedure/let/foreach/cond/…`）+ `db*` + `defstruct` 替身 | 建图脚本、执行真实 ADE 状态文件 |
| `hdl` / `vhdl*` / `ihdl` / `spectreVerilog` | ⬜ | 无数字/Verilog 流程 | — |

## 6. 统计

- 覆盖：**✅ 11 个模块的核心**（含自研 SPICE、LVS 图同构、网表导入、寄生提取），**🟡 14 个简化版**，其余 ⬜。
- 代码：`vlight/` 30 个模块，约 8.6k 行 Python。
- 测试：72 个离线单测 + 4 个真跑 spectre 的集成测试。
- 算法细节与验证数据见 [ALGORITHMS.md](ALGORITHMS.md)。
- 真实数据验证过的输入：`tsmcN65_LNA2G4`（4 cell）、`CSPA_DM_lvt4t/webdisplay_state1`（23 个 ADE state）、`vco_core_v1_final.scs`、`diff_amp.scs`、`tsmcN65.layermap`。
