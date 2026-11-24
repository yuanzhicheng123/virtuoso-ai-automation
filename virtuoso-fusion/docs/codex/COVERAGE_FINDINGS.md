# 覆盖范围审计：Virtuoso Fusion

> 本页记录0.1.1历史验收。0.2.0已新增正式物理验证API；当前接口能力和验收以 [STATE.md](STATE.md) 为准，历史测试结论不扩展为芯片签核。
审计日期：2026-11-23。对象：Fusion 0.1.0 源码及固定上游 `3f0bd7d46ca3c5d2c750cea0894bbdefc7b67b9d`。

**裁决：驳回“完全覆盖芯片设计全流程 / Cadence Virtuoso 全可调用”的声明。** 源码提供了广泛工程入口和 raw SKILL 执行通道，但存在明确的 generic 表达边界、未封装的签核阶段及尚未完成的工程验收。单项真机 PASS 只能放行该项的已测环境与输入，不自动提升其它阶段。

本文是覆盖审计，不是第二份项目状态。当前真机裁决以 [STATE.md](STATE.md) 为准；下表中的“旧实测”特指本次扩展签核开始前的 `evidence/final-live`，当前结果另行引用，不能用继承 API / 单元测试代替。

## 可复核的注册表口径

运行 `PYTHONPATH=src:../upstream-lite-reference/src python tools/audit_coverage.py --output evidence/coverage-static.json`。脚本实际使用 `values.validate_name`、`GenericSkill.call`、现有参数编排与写策略；executor 只记录生成文本，**没有调用未知 Cadence 函数**。逐名结果和源文件哈希见 [coverage-static.json](../../evidence/coverage-static.json)。

| 统计项 | 数量 | 含义 |
|---|---:|---|
| 注册项 | 74,785 | 历史环境发现的符号快照，非 Cadence 全产品全集 |
| 有签名 | 61,979（82.88%） | params / arglist_raw / zero_args 任一存在，不保证签名无歧义 |
| 不以下划线开头 | 20,954 | `coverage().public` 的实现定义，不等于厂商公开支持 API |
| 上述有签名 | 17,604（84.01%） | 不等于工程功能完成率 |
| documented=True | 7,586 | 历史文档来源标记；与 public 口径不同 |
| 含 headless 上下文 | 48,198 | 注册表提示，不是当前会话 getd 或运行结果 |
| 只有窗口上下文 | 26,587 | schematic-only 3,138；layout-only 18,273；两类窗口 5,176 |
| 名称校验拒绝 | 9 | `cons*`、`let*` 和 7 个 `_schModule (...)` 名称 |
| 名称通过，但显式 allow_write 仍拒绝 | 579 | 现有已知写前缀不在允许清单；不是 Cadence 不支持 |
| 零参数调用文本可编译 | 74,197 | 仅生成 `fn()`；不证明参数正确、运行可用、许可证足够或结果正确 |
| 编译可达且需要显式 allow_write | 10 | generic 写允许清单 |
| 编译可达且可提取 Python kwargs 固定参数名 | 47,473 | 参数编排候选；不是 SKILL `?keyword` 语法覆盖率 |
| 编译可达但无 kwargs 候选 | 26,724 | 无签名、无参数或歧义签名等混合，不可直接称不可调用 |
| 编译可达且签名含显式 object 参数 | 5,194 | Python 层尚无 OA 对象句柄值契约；这是已识别下限，不是全部对象 API 数 |

历史标签 `CALLABLE` 出现在全部 74,785 项中，而 `EXECUTED` 只有 377 项，`READBACK_VERIFIED` 只有 49 项。标签来源没有当前会话身份与本轮输入输出，因此不能升格为本轮真机通过。

## “全可调用”的实际边界

源码：`src/virtuoso_fusion/values.py:15`、`generic.py:21`、`registry.py:83`、`policy.py:7`、`client.py:57`。

1. **名字可发现 ≠ generic 可表达。** `cons*`、`let*` 被名字规则拒绝；特殊形式还需要表达式 / 求值语义。7 个 `_schModule (...)` 更不能凭名字假定为普通可调用函数。必须先查文档和当前环境，不应机械放宽正则或批量运行。
2. **值编码支持布尔、nil、数值、字符串、list/tuple、显式 Symbol。** `Symbol('cv')` 生成 `'cv`，传入的是符号，不是解引用变量 `cv` 中的 OA 对象。`db:0x...` 字符串也不会变成对象。包含 cellView、instance、window、session 等对象的动作，需要现有工程 facade 或在一个 raw SKILL 作用域内取得对象并使用；单有 `client.call('dbSave', Symbol('cv'))` 不能证明 OA 保存可用。
3. **Python kwargs 只重排位置参数。** 不能据此声称支持所有 SKILL `?key`、表达式、回调、特殊形式、嵌套或联合签名。例：`dbOpenCellViewByType` 的历史签名含 `|` / `nil`，现有实现明确要求位置参数；函数参数名 `timeout` 也被调用层保留。
4. **写策略限制 generic。** 579 个名称在 allow_write=True 时仍拒绝；原理图/版图 facade 与 raw SKILL 是其它入口。不能把写限制解除当作功能完备，也不能把名字前缀分类称为完整只读保障。
5. **raw SKILL 是扩展出口。** 上游 `virtuoso/basic/bridge.py:621` 接收代码，Fusion 包装相同执行通道和证据；可在有相应定义、上下文及授权时执行自定义表达式。它没有自动产生 PDK 合法参数、rule deck、正确激励、结果解析或验收判据，因此不是全工程覆盖的证据。
6. **运行时盘点也有边界。** `getd` 有定义只证明当前会话存在函数绑定。它不会验证函数参数、GUI对象、许可证、设计状态或输出正确性。headless 与 GUI 会话的结果必须分别标明，不能合成一个无条件百分比。

补齐动作：按真实工程调用补充命名明确的 facade / 对象作用域；对需要 SKILL 特殊语义的动作保留显式 raw 路径；为每项给出正确输入、实际输出与回读验收。无需为了漂亮覆盖数字任意放开全部函数。

## 本轮运行时绑定盘点

证据：[headless.json](../../evidence/signoff-inventory/headless.json)、[gui-contexts.json](../../evidence/signoff-inventory/gui-contexts.json)。审计重新读取全量 74,785 个 observation、核对记录集合 / 计数与静态注册表 SHA-256 一致，生成 [coverage-runtime-summary.json](../../evidence/coverage-runtime-summary.json)。版本为 `IC23.1-64b.43`。GUI 原始执行目录为 220 的 `signoff/inventory-gui2/live-kf0lz5wq`；主执行链确认 GUI / RAMIC 已清理。

| 会话阶段 | getd BOUND | UNBOUND | QUERY_ERROR | BOUND ∩ generic可生成文本 | BOUND但generic名称拒绝 | BOUND但generic写策略拒绝 |
|---|---:|---:|---:|---:|---:|---:|
| headless | 48,228 | 26,557 | 0 | 47,669 | 9 | 550 |
| GUI base | 48,228 | 26,557 | 0 | 47,669 | 9 | 550 |
| schematic已加载 | 65,370 | 9,415 | 0 | 64,782 | 9 | 579 |
| layout已加载 | 74,776 | 9 | 0 | 74,188 | 9 | 579 |
| Maestro已加载 | 74,776 | 9 | 0 | 74,188 | 9 | 579 |
| 已测阶段并集 | 74,776 | 9 | 0 | 74,188 | 9 | 579 |

这些是加载阶段快照；后续 GUI 阶段可能保留前面加载的模块，不能解读为每个独立 layout-only / Maestro-only 环境都具有同样函数表。`BOUND ∩ generic可生成文本` 仍没有执行这些函数，不能称为调用成功率。

**运行时始终未绑定的 9 项与 generic 名称拒绝的 9 项完全不同，交集为 0。** 前者全部可以生成 generic 调用文本：

- `__iaToolbarLoadWidthSpaceFromCG__`
- `_iaCongMapPluginReg`
- `seeLibrariesCategoryRegistered`
- `seeNetsCategory`
- `seePinsCategory`
- `seeSheetsCategory`
- `seeTLineCategory`
- `seeViewsCategory`
- `seeWarningsCategory`

反过来，`cons*`、`let*`、7 个 `_schModule (...)` 在当前 getd 盘点中全部有函数绑定，但仍被 generic 名称规则拒绝。由此可直接证伪“绑定存在就能经 generic 调用”与“可生成文本就有当前函数定义”两个推断。

上述定点getd表的分母是**历史注册表的 74,785 项**；它不包含快照以外的新名字。之后已补做当前会话oblist枚举，结果见下节，不能把两个分母混用。旧 `engineering-source/mine/skill_runtime.py:18` 和 `docs/GUI_UNLOCK.md:64` 给出了已在220用过的 `foreach s oblist / getd` 全符号枚举方法（`oblist` 是变量，非 `oblist()`）。`docs/SYMBOL_SPACE.md` 的200,690符号仅是当时已加载GUI环境的历史数值，也不能充当今天的分母。

复现合并：

```bash
PYTHONPATH=src:../upstream-lite-reference/src python tools/audit_coverage.py \
  --output evidence/coverage-static.json \
  --headless-inventory evidence/signoff-inventory/headless.json \
  --gui-inventory evidence/signoff-inventory/gui-contexts.json \
  --native-inventory evidence/signoff-inventory/native-contexts.json \
  --runtime-output evidence/coverage-runtime-summary.json
```

### 本次会话原生 oblist 函数全集

新增证据：[native-contexts.json](../../evidence/signoff-inventory/native-contexts.json)。主执行链在独立GUI会话 `inventory-native/live-0io3su5f` 使用 `oblist` 全符号遍历，仅对符号读取 `getd`，没有运行被枚举的函数，并完成会话清理。本审计校验各阶段名称唯一性、数量以及其与历史注册表相交后，恰好等于同阶段getd定点查询中的BOUND集合。

| 已加载阶段 | oblist内getd已绑定函数 | 其中在旧registry | 旧registry缺失 | 名称收录比例 |
|---|---:|---:|---:|---:|
| base | 52,156 | 48,228 | 3,928 | 92.4687% |
| schematic | 95,707 | 65,370 | 30,337 | 68.3022% |
| layout | 115,108 | 74,776 | 40,332 | 64.9616% |
| Maestro | 116,738 | 74,776 | 41,962 | 64.0545% |
| 本轮已加载阶段并集 | **116,738** | **74,776** | **41,962** | **64.0545%** |

**当前注册表漏掉本次已加载函数集合的 41,962 个名字（35.9455%）。** 因而不仅工程验收未覆盖全流程，连“当前会话函数名称全部收录”也不成立。新增名字示例包括 `ALIAS`、`DRCRules`、`DrcRules`、`DrcGuiConfig`、`DataTypeComplex` 等；名字本身不说明它是稳定公开API，也不能证明正确调用所需的参数/许可证/上下文。

旧registry名称、当前已绑定函数、generic可生成调用文本三者交集仍为 **74,188**，占当前已枚举集合 **63.5509%**。这是三集合交集比例，**不是实际执行成功率，也不是该库经raw SKILL/工程facade能够操作的上限**。GenericSkill可接受合法的未注册名称并尝试现场获取签名，因此“registry缺失”也不等于“所有这些函数都不能调用”。

机器可读summary新增 `native_universe`，保留全量41,962个缺失名称、9个历史名称缺失于当前全集、阶段计数、精确比例与输入文件哈希。没有将这些新增名字并入production registry，没有给它们补造签名或验收标记。

这里的116,738只代表 **IC23.1-64b.43本次会话在所测加载阶段的getd绑定全集**。它不包含尚未加载的工具/PDK环境、其它版本/产品/许可证可能提供的函数，也不覆盖私有C++ API、对象属性或全部工程动作；更不能解释为116,738项执行通过。

## 本轮专项结果更新

以下专项由主执行链反馈；本审计没有重复运行真机，正式证据、缺陷修复与最终裁决由 [STATE.md](STATE.md) 给出。其通过范围仅为对应测试条件，不改变整芯片全流程声明的驳回结论。

| 专项 | 本轮结果 | 仍然不能据此声明 |
|---|---|---|
| Maestro | 2/2 PASS，角点列丢失已修复；2 sweep × 2 corner = 4 个实际仿真点 | 所有配置、所有PDK/模型、完整Monte Carlo统计验收均通过 |
| Layout | 4/4 PASS | DRC clean、完整电气连通或可流片 |
| RF分析 | AC/noise/SP/PSS/PNoise/PAC/HB实际读出，1%收敛检查PASS | 所有RF电路、噪声定义与设计指标均已签核 |
| DRC | 29项违规，未通过 | 不能签核物理规则合格 |
| LVS | 单管测试为correct，限该最小设计 | 完整芯片或完整测试台LVS通过 |
| PEX | 产生6个C、无R；没有完成后仿真 | 完整RC提取、寄生回标与后仿指标闭环 |

## 全流程证据矩阵

`U/` 表示固定上游 `../upstream-lite-reference/src/virtuoso_bridge/`；下列文件均在该固定提交源码中可复核。`旧实测` 为 Fusion 最初发布证据；`当前待验收` 表示本次审计尚未收到该项新证据，应由 STATE 链接最终结果，不预判执行结果。

| 工程阶段 | 源码提供的入口 | 旧实测 / 当前审计结论 | 补齐或验收动作与判据 |
|---|---|---|---|
| 需求、指标、测试台与验收计划 | Maestro `set_spec`（U/virtuoso/maestro/writer.py:156）；没有需求到全流程追溯模型 | 接入；没有完整芯片需求覆盖证据 | 建立每个目标→测量表达式→角点→阈值→结果文件对应；检查没有仅配置未测量的指标 |
| PDK/工艺库/器件映射 | library tech attach（U/virtuoso/library/management.py:165）；process map（U/virtuoso/schematic/manifest.py:497） | 旧实测 N65 独立仿真；不代表其它 PDK / 节点可运行 | 核对 tech/model/PCell/CDF 来源；合法器件保存重开；model section、单位和输出网表值一致 |
| 原理图、引脚、连线、CDF | manifest import/verify（manifest.py:1334、1706）；参数（params.py:239）；Check & Save（diagnostics.py:160） | 旧实测仅新库、新原理图和 generic 交叉读；复杂电气流程当前待验收 | 构建有源器件测试台，回读拓扑/CDF有效值，Check & Save 零错误；不能只检验图形存在 |
| 层次、config 绑定、行为模型、AMS | 原理图嵌套实例及 netlist `view` 参数存在；未见专用 HED/config 绑定闭环或 AMS runner | 缺少专用完整流程；普通 schematic PASS 不涵盖 config | 双层设计建立配置，读取实际绑定树；顶层端子、stop/switch views 与原生网表对应；AMS 需单列引擎与测试 |
| 原生 OA→Spectre 网表 | U/virtuoso/schematic/netlist.py:128、191；Maestro corner netlist（writer.py:676） | 源码接入；旧实测 Spectre deck 是独立测试台，不能替代 OA 闭环 | 从本轮 OA 生成新网表，记录哈希与时间；核对实例、端子顺序、w/l/m、模型；仿真直接消费此包，不手写替代 |
| OP/DC/AC/Tran/noise/SP | SpectreSimulator（U/spectre/runner.py:678、850）；PSF读取（U/spectre/psf.py:14）；Maestro `set_analysis`（writer.py:120） | 旧实测 OP/DC/Tran；本轮 AC/noise/SP真实结果及收敛检查PASS，见上述专项更新 | 每种独立输出及可解析数据；DC工作点、AC增益/极点、Tran稳态、noise积分与单位、SP参考阻抗/端口定义分别断言 |
| PSS/PNoise/HB | 同一 Spectre / Maestro 通道；上游有 `examples/02_spectre/04_strongarm_pss_pnoise.py` | 本轮 PSS/PNoise/PAC/HB真实结果及1%收敛检查PASS；不是任意RF设计签核 | 合适周期电路、收敛日志、实际分析输出；PNoise边带/频带/单位与积分，HB求解结果；误用AC替代不放行 |
| PVT/corners/Monte Carlo | writer.py:293、304；monte_carlo.py:554、687、726 | 本轮Maestro的2 sweep × 2 corner = 4 个实际仿真点反馈PASS；Monte Carlo完整统计验收仍不能由此推出 | 保存重开配置；实际run/history含全部角点/样本、seed、process/mismatch；检查样本数、无失败/缺失、结果分布和指标 |
| Maestro作业、结果、历史 | U/virtuoso/maestro/jobs.py:411；writer.py:546；reader/、history.py | 本轮Maestro 2/2 PASS；准确用例范围见STATE | create/open→set→save/reopen→run/wait→history/result；从实际run ID取输出，不以任务提交成功代替完成 |
| layout/PCell/几何/布线 | U/virtuoso/layout/ops.py:88、138、157、196；reader.py、groups.py | 本轮Layout 4/4 PASS；没有全芯片自动布线/约束收敛闭环证据 | PDK器件与金属/via创建保存回读；核对参数、层、坐标、连接；匹配/屏蔽/寄生要求另行验证，几何存在不是 DRC clean |
| DRC/LVS/ERC | 未发现对应 runner / deck合同 / marker或报告解析与验收；源码中Calibre字样为SOS禁止修改检查 | 本轮DRC 29项违规未通过；单管LVS correct仅限该小例；专用库级完整签核闭环仍缺失 | 接入目标环境实际工具与获授权rule deck；提交、退出码、完整报告、错误分类、waiver依据；LVS必须matched，DRC/ERC符合指定门槛 |
| PEX/RC/寄生回标 | 未发现专用 extraction runner / 角点产物合同 / 寄生与原理图映射验收 | 本轮最小PEX产物6C、无R；完整RC提取和库级闭环未验收 | 使用匹配版图/网表/PDK提取；保存deck与工具版本、寄生网络、角点；证明提取产物而非预置网表 |
| 后仿真与前后对照 | Spectre可消费指定网表；没有PEX→extracted/config→post-layout指标闭环 | 本轮没有完成PEX产物后仿；后仿工程签核缺失 | 消费本轮PEX结果；绑定extracted视图；前后仿同指标/同角点差值和通过阈值；保证寄生实际进入网表 |
| GDS/OASIS与交付 | GDS结构化导出（U/virtuoso/layout/streamout.py:683）、XStream日志解析 | GDS入口接入、当前待验收；未见完整foundry交付包或OASIS专项验收 | 实际stream-out、零错误、层映射、top cell及文件完整性；交付包含规则签核、版本、哈希；GDS能打开不代表可流片 |
| EM/IR、ESD/latch-up、DFM/密度/可靠性 | 未见专用工具适配、输入合同与结果解析 | **缺失工程接口及验收** | 针对工艺与设计电流/应力条件选定流程、deck与判据，保存真实报告；不能由版图编辑或Spectre运行替代 |
| 数字/混合信号全芯片 | 未见 RTL仿真、综合、STA、DFT/ATPG、数字P&R、形式验证等专用实现 | **库当前覆盖范围之外** | 若“芯片全流程”包含数字部分，分别连接相应工具并建立网表/时序/物理一致性与签核；不能把Virtuoso SKILL集合等同全部工具链 |

### 优先阻断项

- 全流程裁决需要原生 OA→netlist→仿真与 layout→DRC/LVS→PEX→后仿→交付的**同一设计链**；不同孤立小例 PASS 不能替代它。
- `getd` / 注册表 / 生成SKILL文本 / 提交作业 / 生成文件分别属于不同证据层级。每个阶段必须验证最终结果与上一阶段产物身份。
- 检查到的最大结构性缺口是物理验证、寄生提取、后仿交接和可靠性签核；这些不是再增加几项通用函数就可以验收的能力。

## 本审计验证

- `tests/test_coverage_audit.py`：3 passed；连同 `test_generic.py`、`test_registry.py` 定向回归共 31 passed。使用实际包内注册表及 GenericSkill 路径验证全量分区完整、写策略和含歧义签名的行为。
- 没有修改 core，没有调用未知函数，没有把上游离线回归或历史标签记作当前真机通过。
- 本轮定点getd与原生oblist inventory均已逐名核对并追加；专项结果按主执行链反馈标识，正式放行范围仍在 STATE。
