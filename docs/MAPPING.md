# 功能映射矩阵（Virtuoso → vlight）

所有结论都来自 220 机器上的 IC23.1 安装与真实工程，证据路径可复核。

环境：
- `/eda/cds/IC2310`（Virtuoso Studio IC23.1，SKILL 38.00）
- `/eda/cds/SPECTRE201`（Spectre 20.1.0.068）
- PDK：`/eda/1P9M/TSMC065_1P9M`（CRN65GPLUS，1P9M）
- 真实工程：`/root/cadence_projects/tsmcN65_LNA2G4`（4 cell）

---

## 1. 数据模型

| Virtuoso 对象 | 本实现 | 证据 |
|---|---|---|
| `dd` library（`ddGetObj("lib")~>cells`） | `Library`，`LIB/.oalib` 标记 | `ddGetObj` 返回 cell 列表，dump 里 `LIB ... ncells=4` |
| cell | `Cell`，`LIB/CELL/` | — |
| cellview（`dbOpenCellViewByType`） | `Schematic` / `Layout`，`CELL/VIEW/{sch.json,master.tag}` | Virtuoso 同目录下是 `sch.oa` + `master.tag` |
| instance（`cv~>instances`） | `Instance`（name/lib/cell/view/xy/orient/numInst） | dump 行 `I Ls tsmcN65/spiral_std_mu_z/symbol xy=(-20.0 -80.0) orient="R0" n=1` |
| CDF 参数（`cdfGetInstCDF(inst)~>parameters`） | `Instance.params`（保留原始 SKILL 字面量） | 每实例 30~60 个参数，值形如 `"62.0u"`、`"spiral_std_mu_z"`、`t` |
| net（`cv~>nets`） | `Net`（name/sigType/terms） | `N n_mid sig="signal"` |
| instTerm（`net~>instTerms`） | `Net.terms: [(inst, term)]` | `T Cb.PLUS` |
| terminal / pin（`cv~>terminals`） | `Pin`（name/direction/net） | `PIN rf_in dir="input" net="rf_in"` |
| techfile layer（`techGetTechFile(cv)~>layers`） | `TechFile.layers` | 216 层；但**层号在 SKILL 侧是 `nil`**，真实层号只能从 `tsmcN65.layermap` 拿 |
| stream 层映射 | `TechFile.gds_map[(layer,purpose)] = (layer,datatype)` | `tsmcN65.layermap` 484 行 / 158 层，列：`LayerName Purpose StreamLayer StreamDatatype` |
| 库搜索路径（`cds.lib`） | `LibPath.parse`（DEFINE/INCLUDE/SOFTINCLUDE/`$VAR`） | `DEFINE tsmcN65 ./tsmcN65` |
| 层次拓扑 | `Design.topo()`（后序，叶子在前） | subckt 定义顺序必须如此，否则 spectre 报未定义 |

## 2. SKILL API 覆盖

已实现（`vlight/skill.py`），语义按 Virtuoso 参考手册对齐：

| 类别 | 函数 |
|---|---|
| 视图 | `dbOpenCellViewByType`（schematic/layout，r/w/a）、`dbSave`、`dbClose` |
| 原理图 | `dbCreateInst`、`dbCreateNet`、`dbCreateTerm`、`dbCreateConnByName`、`dbCreateInstTerm`、`dbGetNet`、`schCheck` |
| 版图 | `dbCreateRect`、`dbCreatePolygon`、`geAddInst` |
| 参数 | `dbReplaceProp`（+ `vlightSetParam` 别名） |
| 访问 | `obj~>prop`、`getq`（`instances/nets/terminals/instTerms/name/cellName/libName/xy/orient/numInst/direction/net`） |
| 语言 | `procedure`/`lambda`/`return`、`let`/`let*`、`setq`/`setf`、`a = b`、`if`/`when`/`unless`/`cond`、`progn`/`prog`、`foreach`/`while`/`for`、`and`/`or` |
| 数据 | `car/cdr/cadr/caddr/cddr/cons/list/append/length/nth/reverse/member/assoc/mapcar/mapc/apply`、`eq/equal/neq/null/not/atom/numberp/stringp/symbolp` |
| 算术 | `+ - * / mod abs min max sqrt exp log floor round int float fix pow`、比较运算 |
| 字符串 | `strcat/strlen/substring/atof/atoi/lowercase/upper` |
| 输出 | `printf/sprintf`（`%L` 按 SKILL 字面量、`%s %d %f`）、`print/println/pprint` |

未实现：`dbCreate*` 全量（shape/label/instHeader/…）、`hi*`（GUI）、`envSetVal/envGetVal`、
`tech*` 写接口、宏（`define`/`defmacro`）、`sstatus`/`errset` 的完整语义、SKILL++（`class`/`package`）。

解析器特殊处理（都是真实 SKILL 语法）：
- `f(x)` 与 `f (x)` 区分——前者是调用，后者是「符号 + 列表」（`procedure(foo (a b) ...)` 必须走后者）。
- `x = expr` 等价 `setq`；`a~>b~>c` 链式访问；`'list` 引用。
- `t` 是保留常量，不能当变量名（踩过：`lambda((t) t~>name)` 会静默变成 `True~>name`）。

## 3. 器件映射（CDF → 网表）

证据：`/eda/1P9M/TSMC065_1P9M/models/spectre/crn65gplus_2d5_lk_v1d0.scs` 的 subckt 签名
+ 工程实例的 CDF 参数名。

| master（Virtuoso） | 端序 | 网表模型 | 参数映射 |
|---|---|---|---|
| `nmos_rf_25ud18` | D G S B | `nmos_rf_18` | `lr←lr, wr←wr, nr←nr`，`m←CDF m 或 numInst` |
| `pmos_rf_25ud18` | D G S B | `pmos_rf_18` | 同上 |
| `nch_25` / `pch_25` | D G S B | 同名 | `l, w, nf` |
| `rppolywo` | PLUS MINUS | `rppolywo` | `l←l, w←w, mf←m` |
| `mimcap` | PLUS MINUS | `mimcap_sin` | `lt, wt, mf←m, mimflag` |
| `spiral_std_mu_z` | PLUS MINUS BULK | `spiral_std_mu_z` | `rad, w, nr, spacing, lay, gdis` |
| `basic/ipin|opin|iopin` | — | 跳过（纯符号，Virtuoso netlist 也忽略） |
| 其它有原理图的 master | 引脚表顺序 | 同名 subckt | 位置传参 |

注意：`Instance.num_inst`（OA `numInst`）与 CDF `m` 是两回事；本实现优先 CDF `m`，
没有再退到 `numInst`，避免重复乘。

## 4. 模型装配（必须按组合来）

| 现象 | 原因 | 正确做法 |
|---|---|---|
| `referencing an undefined subckt` | 只 include `section=tt` | 还要 `tt_rfmos_18 / tt_res / tt_mim / tt_rfind` |
| `Unknown parameter 'l_indfac'` | 缺 `tt_rfind` | `tt_rfind` 内含 `rf_ind` 与角参数 |
| `subcircuit ... already defined` | `tt_rfind` 与 `rf_ind` 同时 include | 只留 `tt_rfind` |
| `Unknown parameter 'par_res'` | 缺 `tt_res` | 加 `tt_res`（内含 `res`） |
| `Unknown parameter 'par_mim'` | 缺 `tt_mim` | 加 `tt_mim`（内含 `mim`） |
| `Use of the comma character in node lists` | 网表写成 `(a, b)` | 空格分隔 |
| `No master specified for instance 'options'` | spectre 的 options 要名字 | `vlightOptions options temp=27` |

## 5. 文件格式（已实测）

| 格式 | 结构 | 实现 |
|---|---|---|
| PSF ASCII | `HEADER`（`"k" v`）/ `TYPE`（`"name" FLOAT\|COMPLEX ...`）/ `TRACE` / `VALUE` / `END`；DC 的 VALUE 带类型串 `"in" "V" 1.0`，AC 不带、复数写 `(re im)` | `psf.parse_psfascii` |
| nutascii rawfile | 一个文件多段 `Plotname:` + `No. Variables/Points` + `Values:`，复数为 `re,im` | `psf.parse_rawfile` |
| spectre raw 输出 | `-format psfascii -raw DIR` → `DIR/<analysis>.<type>`；`-format nutascii -raw FILE` → 单文件 | `sim.Simulator` |
| layermap | 4 列文本，`#` 注释 | `skilldump.import_tech` |
| cds.lib | `DEFINE/INCLUDE/SOFTINCLUDE`，`$VAR` | `vdb.LibPath` |
| 库目录 | `LIB/.oalib` + `CELL/VIEW/{master.tag,sch.json}` | `vdb.Library.save/open` |
| GDSII | 记录头 `len(2)+rectype(1)+datatype(1)`，8 字节 excess-64 浮点，SREF 层次 | `gdsio.write_gds/read_gds` |

## 6. 验证证据

| 项 | 数据 |
|---|---|
| dump 规模 | 917 行，含 4 个 cell 的全部实例/网络/引脚/CDF |
| 网表 | LNA 顶层 9 个器件 + 3 个电阻 + 1 个 MIM + 2 个电感，spectre 0 error 0 warning |
| 分压器 | `V(out) = 2.0 V`（1k/2k，3 V 输入） |
| RC | `|H(fc)| = 0.7071`（fc=159 MHz，误差 3e-3） |
| GDS | KLayout 0.30.5 读出 1 cell / 58 shapes / 3 层 `M9(39,60) M8(38,40) VIA8(58,40)`，dbu=1 nm |

## 7. 模块覆盖（第二轮补齐）

IC2310 在 `tools/dfII/etc/tools` 下有 119 个 tool 模块。逐项清单见
[MODULES.md](MODULES.md)。这一轮补进来的核心模块：

| 模块 | 实现 | 关键验证 |
|---|---|---|
| `cdf` | `cdf.py`：CDF 参数表、`iPar/pPar`、`netlistProcedure` | 真实 dump → 7 master / 88 参数；缺省值补齐 `w/mf` |
| `ade`/`adle` | `ade.py`：读写 `*.state`（真文件是 SKILL，直接执行） | CSPA 状态：`dc+sp`、`temp=85`、13 变量、4 输出 |
| OCEAN | `ocean.py`：`analysis/desVar/run/selectResult/ocnPrint` | `foreach` 扫阻值 → 1.5/2.0/2.25 |
| `spiceIn` | `spicein.py`：spectre/SPICE → vdb/Testbench | VCO 10 器件、OTA 网表往返一致，导入即仿真 |
| `layoutXL` | `connect.py`：同层接触 + via 栈 + label → 节点表 | 3 节点提取、短路告警 |
| `auLvs` | `connect.lvs` | 3 net 对账 PASS；缺网/短路能报 |
| `Diva`/`drdChecker` | `drc.py`：width/space/area/grid/enclosure | 三条违规全中，pcell 干净 |
| `rod`/pcell | `pcell.py`（Python 类替代 SKILL rod） | 电感 0.63 nH / 58 图形 / GDS 可读 |

## 8. 明确没做

- Virtuoso 图形编辑器（窗口/菜单/拖拽）；命令行 + SVG/HTML 是替代品
- OpenAccess 二进制读写（`sch.oa`）；导入必须经 SKILL dump
- Assura / Calibre / PVS 的 DRC-LVS
- 寄生提取（RCX）、EM 求解、混合信号（AMS）、Verilog-A 编译
- ADE-XL / Assembler 的 UI 与数据库；只做了 sweep + corner 的执行与收集
- SKILL 全语言与 `hi*` GUI API
