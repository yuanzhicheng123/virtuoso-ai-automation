# 从 Virtuoso 里挖什么、怎么挖：KLayout 全兼容 + SKILL 100%

两个目标，一个方法论：**不逆向二进制，逆向"语义源"**——文档、上下文、工艺文件、规则 deck、
真实脚本。凡是能从安装里以文本/接口形式拿到的，就不去猜。

先给结论：

| 目标 | 可行路径 | "100%" 的含义 |
|---|---|---|
| KLayout ↔ Virtuoso 完全兼容 | 工艺 + 层映射 + 规则 deck + 器件提取规则的**语义转换** | 同一份版图，两边的 DRC/LVS 结果一致；数据可双向交换 |
| SKILL 100% 覆盖 | **本地解释器 + RPC 代理**双层 | 名字级 100%（生成 stub + 转发）；行为级按调用频次收敛 |

---

## 1. 挖什么：清单、位置、产物（已跑出数字）

| 目标物 | 在哪儿 | 怎么挖 | 产出 |
|---|---|---|---|
| **SKILL 函数索引** | `doc/{sklangref,skdfref,sklayoutref,skuiref,sktechfile,skpcellref,rodskillref,oceanref,vivaxlskill,...}` 共 12 本手册 | HTML → 去标签 → 抓 `name( 参数 ) => 返回` | `out/mine/skill_index.json`：**5,488 个函数** |
| SKILL 运行时真实集合 | `tools/dfII/etc/context/64bit/*.cxt`（221 个） | `strings` + 符号表，与文档求差 | 未文档化函数清单 |
| 真实用法/频次 | 3,519 个 `.il`、537 个用户过程 | 正则抽 `name(`，统计调用次数 | 优先级排序（已用于决定实现顺序） |
| 数据模型语义 | `doc/skdfref`（1,683）、`doc/sklayoutref`（1,799） | 同上 | 对象/属性/方法表 |
| **工艺 techfile** | `pdk/*/techfile`（ASCII `.tf`） | s-expression 解析 `layerDefinitions` / `techPurposes` / `viaDefs` | `out/mine/tech.json`：**94 purposes / 200 layers / 17 vias** |
| 层映射 | `pdk/*/*.layermap` | 4 列文本 | 481 条 `(layer,purpose)→(gds num,datatype)` |
| 显示资源 | `display.drf`（174 KB） | 解析 drf → 颜色/线型 | KLayout `.lyp` |
| **DRC/LVS 规则** | `Calibre/drc/calibre.drc`、`Calibre/lvs/calibre.lvs`、`Assura/**/*.rul`（45 个） | 语法树/语句抽取 | 规则清单 + KLayout DRC 脚本骨架 |
| PCell | `pdk/skill/*.il`、`doc/rodskillref` | SKILL 源码 + 文档 | pcell 参数表/几何语义 |
| ADE 状态 | `webdisplay_state1/*.state` | 当 SKILL 执行（已实现） | analyses/variables/outputs |
| 仿真模型 | PDK `models/spectre/*.scs`（单文件 135k 行 / 131 subckt） | 解析 subckt 签名 | 器件-参数映射表 |

> 挖矿的实测难度：`skdfref` 的页面是"每函数一页"，`rodskillref` 却是"整本一页、靠锚点"。
> 所以解析器必须有两种模式（单页/多函数页），否则 ROD 的 35 个函数一个都抓不到。
> 这就是为什么"挖"本身要写成工程，而不是一次 grep。

---

## 2. 怎么挖：四类手段

### 2.1 静态挖（能覆盖 80%）
- 文档 HTML/PDF（`doc/`，189 个 PDF + 数万 HTML）
- `.cxt` 符号表（`strings`）
- `.il` / `.ile` 源码（PDK、context）
- 工艺文件（`.tf`、`.layermap`、`.drf`）
- 规则 deck（SVRF / Assura `.rul`）
- 安装清单（`tools/dfII/etc/tools` 的 119 个模块）

### 2.2 运行时反射（补文档没有的）
`virtuoso -nograph` + `.cdsinit` 里 load 脚本。实测：
- `apropos()` 在 dfII 上下文返回 `nil`（它是 SkillIDE 的功能，不是运行时 API）→ 不能靠它列函数
- 改用**错误探测**：用 0..N 个参数调用目标函数，解析报错文本里的 `Wrong number of arguments`
  和类型错误，得到 arity 与参数类型
- `getVersion()` / `techGetTechFile()` / `cdfGetBaseCellCDF()` 这类可以拿到运行时对象，直接 dump

### 2.3 行为录制（拿语义，不猜语义）
- 用 SKILL 的 `trace` / `advise` 包住函数，跑真实脚本，记录 `(函数, 参数, 返回值)`
- 产出"行为样本库"，用来做本地实现的回归（也用来生成测试）

### 2.4 差分验证（判断"像不像"）
已有的三套：
- `conformance/`（21 项，数值/网表/版图/验证/自动化）
- `compat/`（13 项，与 KLayout 逐项对数）
- `sim_align/`（9 项，与 Spectre 逐点对数）

新挖出来的每个函数/规则，都落一条用例，**通过率就是进度条**。

---

## 3. 目标 A：KLayout 完全兼容 Virtuoso

不是"改 KLayout"，而是**把 Virtuoso 的语义翻译成 KLayout 的输入**。五个工件：

| 工件 | 来源 | 产物 | 验收 |
|---|---|---|---|
| A1 数据 | 版图 | GDS/OASIS 双向（现状 13/13；缺属性记录、BOX/NODE、OASIS 原生） | KLayout 读回层/图形/文字/阵列一致 |
| A2 工艺 | `techfile.tf` | KLayout Tech：layer/purpose/颜色、via 定义（17 个） | 版图打开后层名/颜色/via 正确 |
| A3 规则 | Calibre SVRF / Assura `.rul` | KLayout DRC 脚本（语法映射表：`LAYER/AND/OR/NOT/SIZE/ENCLOSE/...`） | 同一版图，违规数一致 |
| A4 提取/LVS | `Calibre/lvs/*.lvs`、`Assura/lvs_rcx/*.rul` | KLayout `extract_devices` + `netlist` 脚本 | 器件数/网络数与 Virtuoso LVS 一致 |
| A5 数据回流 | KLayout 产出 | GDS/OASIS → OA（经 SKILL dump/skillbridge 反向导入） | 双向往返不丢层/不丢属性 |

关键点：**规则 deck 是厂商资产**，转换器只做"语法 → 语法"的映射，不复制规则内容；
规则值仍从原 deck 读。

---

## 4. 目标 B：SKILL 100% 覆盖

### 4.1 为什么"重写 5,488 个函数"不是路
- 文档级 5,488 个；我们现在按名字覆盖 102 个（**1.9%**）
- 但真实脚本的**内置调用**覆盖 **99.1%**（349,867 / 352,909）
- 长尾是 `hi*`（GUI）、`le*`（版图编辑器）、`db*` 的冷门分支、数字/验证器 API

结论：**名字级 100% 靠"生成 + 代理"，行为级 100% 靠"按频次收敛"。**

### 4.2 双层架构

```
SKILL 脚本
   │
   ▼
vlight.skill 解释器
   ├─ 命中本地实现（99.1% 的调用）──► 直接执行（vdb/几何/仿真）
   └─ 未命中 ──► RPC 代理 ──► 真实 Virtuoso（skillbridge / 自建 JSON-RPC）
                              └─ 返回结果 / 对象句柄（handle 映射）
```

- **句柄映射**：远端返回的 OA 对象（cellview/inst/net）映射成本地 proxy 对象，
  `~>` 访问属性时按需回源；写操作转发。
- **录制-回放**：代理会话可录制为 `(call, args, result)` 轨迹，离线回放做回归。
- **stub 生成**：从 `skill_index.json` 给每个函数生成 Python stub（默认转发），
  保证"任何脚本都能跑"，再逐个把高频函数改成原生实现。

### 4.3 验收口径（可测）
1. **名字级 100%**：`skill_index.json` 里每个函数在 `Session` 里可调用（本地或代理）
2. **行为级**：真实脚本语料 100% 跑通；关键函数与 Virtuoso 的返回值逐值一致（差分）
3. **离线可用**：本地实现覆盖的脚本不依赖远端；其余可回放

---

## 5. 分阶段（含估算）

| 阶段 | 内容 | 产出 | 估算 |
|---|---|---|---|
| P0（已完成） | 文档索引、techfile、layermap、状态文件、GDS 兼容 | `out/mine/*.json`、`compat/` 13/13 | — |
| P1 | stub 生成 + RPC 代理 + 录制回放 | 名字级 100%，真实脚本全跑通 | 2–3 周 |
| P2 | 高频语义本地化（db*/ge*/sch*/rod*） | 离线覆盖 99%+ 调用 | 2–3 月 |
| P3 | 规则转换器（Calibre/Assura → KLayout DRC/LVS） | 同一版图违规数一致 | 2–4 月 |
| P4 | PDK 认证（每套 PDK 回归） | 认证矩阵 | 每 PDK 2–4 周 |

---

## 6. 边界与风险

| 风险 | 说明 | 对策 |
|---|---|---|
| 文档与实现漂移 | 文档滞后于版本 | 以 `.cxt` 符号表 + 运行时探测为准，文档只做语义参考 |
| 未文档化函数 | `.cxt` 有、文档无 | 错误探测 + 录制补齐 |
| 代理依赖 | 需要 license/机器 | 本地优先；录制回放兜底 |
| 规则 deck 版权 | 厂商资产 | 只做语法映射，不内嵌规则值 |
| "完全兼容"的范围 | GUI/性能不在内 | 明确以"数据/规则/结果"为准 |

---

## 7. 现在就能复现的命令

```bash
# 1) 挖函数索引（在 EDA 机器上）
python3 mine/doc_mine.py /eda/cds/IC2310/doc/skdfref /tmp/api.json

# 2) 挖工艺（本地）
python3 mine/tech_mine.py recon/tsmcN65.techfile out/mine/tech.json
#   -> purposes=94 layers=200 vias=17

# 3) 对照已有的三套验收
python3 -m compat.check          # 13/13 与 KLayout
python3 -m conformance.run       # 21/21 端到端
VLIGHT_HOST=eda-220 python3 -m sim_align.run   # 8/9 与 Spectre
```

---

## 8. 一句话总结

- **KLayout 全兼容**：挖的是**工艺、层映射、规则、器件提取**四类语义，做成可回归的转换器；
- **SKILL 100%**：挖的是**函数索引 + 行为样本**，用"本地实现 + RPC 代理"两层达成名字级 100%，
  行为级按调用频次逼近；
- 两者共用一套方法论：**静态挖 + 运行时反射 + 录制 + 差分**，每一步都有可测的数字。
