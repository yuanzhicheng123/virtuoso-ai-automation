# GitHub 2a9c977：公开 Python / MCP 接口独立审查

日期：2026-11-24。对象：`2a9c97720011969f9375a561fe5d2539c11ee183`，Fusion 0.3.0。审查者只读生产代码；仅写本报告，未启动 EDA、未改共享虚拟环境。主任务其他执行者可能在审查后修复问题，本报告保留提交时状态，不自动把后续修复算作本轮已验证。

## 结论与证据等级

发现两个可复现的 `read_waveform` 错误：无效输入被报为正常结果；无时间轴时 `at` 伪造采样时间并重复第一个值。其余本轮定向测试通过，但 **31 个工具都有定义和实现，不等于 31 个工具均真实调用通过**。

- `__all__` 11 个名称均可导入；31 个 MCP 名称均能找到 dispatcher 实现。这里只确认接线。
- 使用指定解释器，确认导入路径来自本次 GitHub checkout 的 `src/virtuoso_fusion`。
- 本轮复跑：`test_cli_mcp.py test_physical_mcp.py test_maestro_copy.py test_upstream_compatibility.py`，**27 passed in 3.13s**。其中物理工具使用真实子进程测试程序，不是 Calibre；Maestro copy 使用真实文件/软链接/shell，不是 Maestro 仿真。
- 独立 stdio 子进程实际调用 8 类离线工具：registry_search、registry_coverage、discover_tools、list_pdk、search_pdk、describe_pdk、check_netlist、read_waveform。临时目录下创建自己的 cds.lib、PDK 外观目录及文件，不接入真实 OA 数据库。
- `read_waveform` 的 transient/AC 输入来自本 GitHub 仓库 `../tests/fixtures/tr1.tran.tran`、`ac1.ac`；netlist checker 使用 `diff_amp.scs`。
- 全部原始请求/响应暂存在 `/home/lihong/.cache/tmp/fusion-github-api-review-cnnyw0ni/probe.json` 与 `ac-at-repro.json`；关键复现永久摘录如下。临时探针不是生产验收脚本。
- 下表“已有 live 用例”只指仓库包含该测试路径，及既有验收记录可供主任务核查；本审查未运行它们，更不把旧提交的运行自动当作本 GitHub 提交通过。

## 必须修复的具体问题

### F1：空/损坏 PSF 被 read_waveform 报为正常结果

位置：`src/virtuoso_fusion/_upstream/mcp_server.py:1284`，`parse_spectre_psf_ascii(p).data` 丢弃解析状态，后续 `data or {}` 返回正常 `_base_result`。

精确复现输入：创建 `empty.psf` 为 0 字节；创建 `bad.psf` 内容 `this is not a waveform\n`。通过 `tools/call` 调用：

```json
{"name":"read_waveform","arguments":{"path":"<临时目录>/empty.psf"}}
```

底层返回：空文件为 `ExecutionStatus.ERROR`，`ok=False`，`errors=["PSF ASCII file is empty"]`；坏文本为 `ExecutionStatus.FAILURE`，`ok=False`，`data={}`。

MCP 实际返回两者均相同：无 `isError=true`，`structuredContent={"source":"...","available":0,"keys":[],"stats":{}}`，`resultType="complete"`。不是说 `resultType=complete` 本身应表示电气成功，而是明确的底层解析失败没有传播到 MCP 错误标志/诊断。

影响：AI 得到成功完成的空波形摘要，难以区分文件不存在有效数据与合法空查询。最小修复：Fusion 自有适配层检查解析结果并保留失败状态/诊断；空目录无可读波形也明确处理。不要手改生成的 vendor。

### F2：无时间轴的 AC 输入使用 at 时返回虚假的时间采样

位置：同文件 `1327–1336`。没有 `time/TIME` 时 `i=0`，`row["t"]` 直接取用户请求时间，对每次请求返回第一点信号值。

精确输入（这是无时间轴输入，不应把 at 偷换成频率接口）：

```json
{"name":"read_waveform","arguments":{"path":"<临时目录>/ac1.ac","signals":["out"],"at":[1000000,10000000000]}}
```

该 fixture 有 `freq`、21 个频点，没有 `time/TIME`。实际返回没有错误：

```json
{"samples":[
 {"t":1000000.0,"out":"(0.047619-8.54855e-06j)"},
 {"t":10000000000.0,"out":"(0.047619-8.54855e-06j)"}
]}
```

最小修复：`at` 非空而不存在有效时间轴时返回明确错误；保留 transient 的最近点采样语义。无需本轮实现完整 AC/RF 图表系统。

### 已确认但不要求本轮扩写的 RF 表达缺口

AC fixture 的复数信号 `numeric=0,min=null,max=null`，head 经 JSON 序列化为复数字符串；图表 `x="sample time (s)"`、`status="unavailable"`。说明现有 MCP 波形摘要偏 transient，不具备幅度/相位、频率采样、噪声积分等完整 RF 测量表达；底层 Python PSF/数值 API 的能力不能直接算成 MCP 已覆盖。可在接口说明中注明边界，另立明确需求再实现。

## Python 根导出逐项表（11/11）

本表枚举根 `__all__` 的全部名称。类有继承方法、设置项、多个平台/后端，因此类级覆盖不等于每个方法组合已验证。

| 导出 | 实现与已有测试 | 本轮结论 / 未覆盖边界 |
|---|---|---|
| FusionClient | `client.py`；`test_client.py`；live release/workbench | 导入与无连接构造成功；本轮 CLI/MCP 测到错误元数据、write opt-in。完整 OA/GUI/SSH 生命周期依赖主任务真机测试 |
| GenericSkill | `generic.py`；`test_generic.py`；live release 的 kwargs/Symbol/repeat/error/concurrency | MCP error/policy 适配已复跑；没有逐个执行 74,785 个函数，OA 对象句柄仍不是 Python 对象包装 |
| ApiRegistry | `registry.py`；`test_registry.py`、`test_registry_lazy.py`、coverage audit | 本轮 stdio search 返回2条、coverage真实读取；注册表是名称/签名快照，不是 runtime 可调用证明 |
| Symbol | `values.py`；`test_generic.py::ValueTests`；live release | 明确符号编码用例存在；没有证明任意 SKILL 表达式/对象都可编码 |
| ExecutionStatus | 上游 models 原样导出；client/generic/MCP 错误测试 | 本轮正常/错误结果使用该 enum；无独立遍历 enum 全值的集成验收 |
| VirtuosoResult | 上游 models 原样导出；client/MCP 诊断保存 | 本轮验证 ERROR 元数据完整保留；通用执行结果不等于电路判据结果 |
| SimulationResult | 上游 models 原样导出；Spectre/MCP adapter tests | 本轮读取真实 PSF fixture 的 ERROR/FAILURE/SUCCESS；发现 MCP 丢失解析状态 |
| SpectreSimulator | 上游 runner 原样导出；live release、connected、RF/PVT脚本 | 有真实求解路径用例；本轮无模拟器运行，mode/远端/异常全组合未证实 |
| SpectrePool | 上游 pool 原样导出；live workbench 两作业 | 既有两作业用例不等于任意并发数或 license 队列可靠性，本轮未启动 |
| PhysicalVerification | `physical.py`；physical / physical_integration / physical_mcp；live physical/connected | 本轮 MCP 经实际测试子进程验证 DRC失败/干净、LVS、三阶段PEX/不完整产物；非Calibre验收、非芯片签核 |
| discover_tools | `environment.py`；physical_integration / physical_mcp | 实际读取指定 Python 执行文件和deck存在性，license_checked_out=false；不证明许可证可用 |

### FusionClient 公开 facade 补充

| facade/入口 | 仓库覆盖 | 仍需区分 |
|---|---|---|
| library | live release/workbench：创建、列举、tech、临时rename/delete、category | 未枚举所有库管理方法及异常组合 |
| schematic | live release、Maestro、connected：构建/readback/check-save/native export | MCP add_part/add_pin/add_wire 另有参数适配，不自动被 Python 用例覆盖 |
| layout | live layout/physical/connected：PCell、变换、保存重开、GDS | 不代表复杂层级、布线、匹配/对称约束全面覆盖 |
| symbol | live workbench：pin/label/termOrder | 非全方法、非任意symbol master |
| maestro | corner/copy/compatibility 单测；live Maestro：run/points/netlist options/copy | 多版本/任意CSV、GUI fallback、统计yield均未全面覆盖 |
| oa | `test_oa.py`、live physical：geometry copy、GDS两后端 | 顶层几何范围，不是任意层级 flatten/完整物理设计 |
| dialogs | live workbench：inspect/guard | 非任意弹窗、阻塞恢复覆盖 |
| requests | live workbench：unknown outcome→receipt、执行次数1 | 非所有网络/进程故障与恢复路径 |
| sos | `test_client.py`只证明 facade 存在 | 本仓库未见 Fusion 专属 SOS 真机验收；不能标记调用通过 |
| registry / generic | 专属离线回归、live release 和 inventory | binding inventory 不是所有函数实参调用测试 |
| execute_skill / upload/download / screenshot 等继承入口 | release/workbench、上游测试；screenshot 有离线适配测试 | 上传下载实际文件与截图GUI、SOS是不同证据，不可互相替代 |

## MCP 31项逐项覆盖

记号：`F` 为当前 Fusion `tests/`；`L` 为当前 Fusion `tests/live/`；`U` 为固定上游 `672b451` 的 `tests/test_mcp_adapter.py`（在 `/home/lihong/Demo/virtuoso_bridge_end/upstream-lite-672b451/` 可查，本轮仅阅读，没有把上游独立模块的单测算作 Fusion MCP 真实执行）。`子进程夹具`是实际运行测试程序，但不是 EDA 工具。所有31项 schema/handler 接线均确认。

| 工具 | 实现 | 已有针对性覆盖 | 本轮真实执行与缺口 |
|---|---|---|---|
| add_part | `_impl_schematic_add_inst` | Python schematic/live；未见专门 MCP handler 参数端到端用例 | 未调用；需 MCP→OA 放置并readback |
| add_pin | `_impl_schematic_add_pin` | Python schematic/live；未见专门 MCP handler 用例 | 未调用；需端点、方向、pin readback |
| add_wire | `_impl_schematic_add_wire` | Python schematic/live；未见专门 MCP handler 用例 | 未调用；需两端连通readback |
| build_schematic | `_impl_schematic_build` | U batch/editor/overwrite mocked client | 未调用；批处理中途失败、结果及实际连接需真机 |
| call_registered | Fusion `_call_registered` | F cli_mcp failure/write；L workbench plus/quotient | 离线错误/policy用例通过；本轮无EDA调用 |
| capture_session | `_impl_snapshot` | U snapshot registered CLI/failure，mock subprocess | 未调用；需要真实snapshot目录/内容校验 |
| check_netlist | `_impl_netlist_check` | 随包semantic checker脚本 | 本轮实际子进程检查diff_amp.scs成功；静态检查不代替仿真 |
| check_schematic | `_impl_schematic_check_save` | U uncertain save/receipt；Python live | 未调用MCP；需真实diagnostic和保存失败 |
| describe_pdk | `_impl_pdk_info` | U filesystem PDK discovery | 实际读取临时PDK目录/tech/cell；非真实器件合法性 |
| describe_skill | `_impl_skill_info` | 未见专门 Fusion MCP handler 用例 | 未调用；实现走 find_skill，需校验目标函数及返回文档质量 |
| discover_tools | Fusion `_discover_tools` | F physical_mcp/integration | 实际文件检查通过；未checkout许可证 |
| evaluate | `_impl_skill_eval` | U no-replay/failure；Python execute_skill live | 离线适配证据；本轮未调用真实MCP→CIW |
| list_cells | `_impl_cell_list` | U library-query uncertainty；Python library live | 未调用MCP；不存在lib、特殊名称、视图结果需实测 |
| list_libraries | `_impl_library_list` | U library-query uncertainty；Python library live | 未调用MCP；返回会话库列表待实测 |
| list_pdk | `_impl_pdk_list` | U filesystem discovery | 实际临时PDK文件扫描通过；明确仅MCP主机文件系统 |
| load_file | `_impl_skill_load` | U local tunnel without upload mocked client | 未调用；本地/SSH load、执行失败需实际验证 |
| lookup_skill | `_impl_skill_find` | 未见专门 Fusion MCP handler 用例 | 未调用；需文档索引/配置测试 |
| new_schematic | `_impl_schematic_create` | U existing-view guard/replace mocked client | 未调用；实际existing/replace与空view完整性待验 |
| read_waveform | `_impl_psf_read` | U nearest sample/precision/extrema；本轮真实PSF文件 | transient104点通过；F1/F2失败，AC表达有限 |
| registry_coverage | Fusion `_registry_coverage` | F cli_mcp/registry | 实际stdio读取74785条；signature比例不是工程coverage |
| registry_search | Fusion `_registry_search` | F registry；本轮stdio | 实际返回限定2条；不证明命中函数在当前CIW可调用 |
| run_physical | Fusion `_run_physical` | F physical_mcp 7用例；Python live physical/connected | 子进程夹具覆盖DRC/LVS/PEX判定；真实MCP→Calibre仍需主任务实测 |
| run_test | `_impl_maestro_run` | U explicit session/confirmed completion/uncertainty mocked client | Python live run不等于MCP读取/错误返回都覆盖；未调用 |
| screenshot | `_impl_screenshot` | U真实PNG字节+mock capture、target路径验证 | 未截图；实际GUI目标选择/PNG传输未覆盖 |
| search_docs | `_impl_doc_search` | U remote-aware search mock | 未查询Cadence文档；索引可用性、远端路径待验 |
| search_pdk | `_impl_pdk_search` | U filesystem discovery | 实际临时PDK resistor命中；非器件/参数约束检索 |
| simulate | `_impl_spectre_run` | U failed run diagnostics mocked simulator；Python live | 未执行MCP→Spectre；模式/timeout/result-summary需独立真机 |
| simulate_many | `_impl_spectre_parallel` | U mixed results/preview mocked simulator；Python live pool | 未执行MCP批量；失败数、顺序、并发资源不能由单仿真推出 |
| startup | `_impl_bridge_start` | U registered CLI/environment mock及entrypoint存在 | 未运行；会影响配置，不能为补计数盲启动 |
| status | `_impl_bridge_status` | tools/list仅名称/schema；未见专门Fusion状态内容用例 | 未调用；tunnel/daemon/license三类状态需分别验证 |
| test_results | `_impl_maestro_read` | U explicit session/history；F Maestro CSV与live Python | 本轮未调用MCP；跨test完整结果、错误传播需MCP专测 |

## 可执行的后续验收顺序

1. 在 Fusion 自有层修复 F1/F2，以真实空/坏/AC/transient fixture 通过 stdio 回归；无需启动EDA便可完成。
2. 在隔离220会话中对公开 MCP 工具逐项调用，并把 request/response 与OA/netlist/PSF/Calibre产物关联。尤其补 add_part/pin/wire、status、查文档、capture_session、screenshot 的缺失验证。
3. 将 Python facade 用例、MCP adapter离线用例、MCP真机用例分开统计；未覆盖的SOS、多平台、多种工具模式明确标注未验。
4. DRC违规/PEXcomplete/图像存在/函数binding都不能写成芯片签核通过；原有52条DRC及ERC缺口仍需独立关闭。

## 修复跟踪

F1/F2由0.3.1自有waveform适配器修复；上表为修复前2a9c977审查。新增真实stdio回归先4失败/3通过，修复后7通过；独立联合复查20通过。最终真机补充见GITHUB_VALIDATION_20261124.md。
