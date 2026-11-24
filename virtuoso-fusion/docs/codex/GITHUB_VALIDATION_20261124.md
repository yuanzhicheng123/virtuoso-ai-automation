# GitHub源码逐项验证与0.3.1修复

日期：2026-11-24。输入为GitHub `main` 提交 `2a9c97720011969f9375a561fe5d2539c11ee183`，已核对远端与本地一致；后续修复产生Fusion 0.3.1。新版 `virtuoso-fusion/` 与根目录旧RFIC-GPT分别验收，不能互相替代。

**裁决：放行已验证的新版Fusion工作流；全仓库全部通过、31工具所有模式全覆盖、完整芯片签核不放行。** 新版两处MCP错误已修复；旧根工程仍不能按README独立安装。

## 逐项结果

原始日志位于本地 `evidence/github-validation-20261124/`，不上传PDK、认证文件、OA/PSF原始工程。可公开核对的逐用例状态和源码哈希见 [verification-20261124.json](verification-20261124.json)，逐接口清单见 [API_COVERAGE_20261124.md](API_COVERAGE_20261124.md)。

| 项目 | 实际结果 | 边界 |
|---|---|---|
| GitHub/本地一致性 | 上传提交2a9c977一致 | 0.3.1是本轮后续修复 |
| 新环境README安装 | 新venv安装Fusion及dev extra成功 | Linux/Python3.11；上游实际解析至672b451 |
| 最终源码全套 | **154 passed、35 live skipped**；69 subtests | skip不计通过；与下面真机分别报告 |
| 仓库外已安装wheel | **143 passed**；31 subtests；pip check通过 | 临时测试副本，无源码导入；构建/runner类测试另验 |
| 包资源 | **38项hash一致**，11个根导出可导入 | 名称存在不等于全部方法/参数组合可用 |
| wheel/sdist | 构建与sdist重建内容核对 | 准确包hash见交付RELEASE.json |
| 0.3.0上传基线真机 | **24 passed、0 skipped，128.11s** | GitHub归档脚本+已安装同实现 |
| 0.3.1原核心真机 | 原24项全部通过 | 来自组合35项中的核心子集；组合首轮有1个新增测试断言错误 |
| 0.3.1新增MCP真机 | 最终 **9 passed、2 skipped，1.48s** | 真实handler→EDA/API，13个工具；stdio传输由独立离线测试验证 |
| RF分析 | linear、periodic、HB、noise refinement均PASS | N65单管探针；AC/noise/SP/PSS/PNoise/PAC/HB；非产品规格 |
| RF交叉检查 | PSS/HB相对差约1.2933e-6；PNoise边带7/14对照通过 | 只适用于当前驱动测试台 |
| PVT | **27/27 PASS** | tt/ff/ss × 1.08/1.2/1.32V × -40/27/125°C |
| 工艺Monte Carlo | 两次同seed，均3样本+独立nominal，数值一致 | process-only；不是失配验证或统计良率 |
| 原理图/版图/物理后仿 | 四端口LVS正确，27R/32C，前后仿判据通过 | **52个DRC结果；ERC仍未闭合** |
| 独立复查 | 新波形/CLI/MCP联合20项通过；正常真实PSF返回结构与原handler一致 | 未扩大为完整RF波形系统 |
| 旧根工程 | 安装失败；可执行部分309通过/3失败/7收集错误/8跳过/1预期失败 | 单独隔离副本测试，未混入Fusion |

独立RF/PVT套件在0.3.0基线和0.3.1安装环境均重新执行，没有只引用旧0.2.1结果。固定上游完整2930通过/126跳过/1个bsd-csh失败仍属于先前同pin审计，本轮没有重复计为新跑完整上游套件。

## 本轮修复的两个实际错误

**F1：无效PSF误报成功。** 上游MCP handler只取解析结果的data，丢掉ERROR/FAILURE。空文件、非PSF文本或无数据目录于是被包装成成功的空摘要。0.3.1在自有 `waveform.py` 中保留单文件解析状态与诊断；空目录返回明确错误。

**F2：没有时间轴时伪造采样。** AC频率数据传入 `at` 时，原实现把请求值当作时间，并重复返回第一个样本。现在明确要求时间轴；`at`仍只表示秒，不偷偷改为频率。

新增7项真实stdio/解析器用例：先4失败、3通过，修复后7通过；与已有CLI/MCP联合20通过。正常transient、AC不带at、DC标量和目录读取的完整返回结构与原handler对比一致，单文件仅解析一次。固定vendor与38项资源未修改。

仍有限制：AC复数摘要未提供完整幅相统计、频率采样、噪声积分。混合目录解析继续沿用上游合并规则，不宣称每个文件均独立完整验收。

## MCP逐工具真机覆盖

新增隔离会话实际通过13个工具：

`list_libraries`、`list_cells`、`lookup_skill`、`describe_skill`、`evaluate`、`new_schematic`、`add_part`、`add_wire`、`add_pin`、`check_schematic`、`build_schematic`、`screenshot`、`read_waveform`。

单项和批量原理图不仅检查返回值，还从OA读回R/C与IN/OUT/VSS连通性。evaluate失败结果与当前客户端JSONL原始result逐字段一致，再以plus验证后续调用恢复。截图检查文件位于工作区、PNG标识和字节数；不声称完成UI视觉审查。read_waveform使用真实8ns RC瞬态PSF，核对数值、最近点和图表状态。

`status`、`capture_session`明确SKIP：handler启动独立CLI，不使用绑定的隔离客户端；VF_LIVE_*不足以保证其profile/state隔离。本轮没有为了补数量触碰默认会话。startup、全部Maestro/MCP组合、load_file、完整文档搜索等也没有因工具列表存在而自动算通过；其余各工具的离线/既有真机证据逐项列在API清单。call_registered与物理API等原核心测试结果保留，不等同新增测试文件覆盖了31个工具。

## 失败尝试与复跑

0.3.1组合首轮 `fixed031/live-d9zmvb9l`：**32通过、1失败、2跳过**，整体result保持FAIL。失败为新增测试要求即时除零错误也带request_id/request_handle。实际原始结果只带skill_error且错误传播正确；即时已知结果不应套用超时未知结果的receipt契约。

修正测试为errors+skill_error、与原JSONL完整比对、随后plus=42。只重跑受影响的MCP补充模块：`mcp-final/live-1l1ojvex` **9通过、2跳过**。没有重写首轮FAIL，也没有伪造一份35项全绿运行。两轮runner均确认其拥有的Virtuoso/RAMIC/Xvfb已退出。

归档首次命令在Git子目录错误使用archive路径导致准备失败；改为从仓库根指定子树后才启动EDA，准备失败不计真机失败。220与本机时钟存在偏差，tar保留了未来时间戳警告；版本身份依据提交/内容hash，不依据机器墙钟推断顺序。

## 旧根工程的独立结论

旧版README的 `pip install -e .` 实际失败：`project.optional-dependencies.bridge`被写成map，PEP621要求列表。包列表只含vlight/vskill/vskill.bridges，遗漏virtuoso_bridge、vskill.phase2和alchemy子包；无条件导入numpy也与默认零依赖声明不符。

Git又忽略了必要的 `_index.json`、`skill_index.json` 和注册表。标准根tests在收集阶段报7错误；使用continue-on-collection-errors后可执行部分309通过、3失败、7错误、8跳过、1xfail。README离线查询也因缺registry失败。`.gitignore`指向的 `registry/MANIFEST.md`不存在，不能声称用户已有完整重建路径。

本轮保留旧工程，不放宽测试、不生成替代数据掩盖缺失。首页已明确旧安装说明不可作为当前已验证入口。新版独立目录可以正常安装与使用。

## 交付与尚未覆盖

新版本0.3.1，不覆盖旧0.3.0发行物；220使用独立 `venv-031`。准确源码提交与产物hash在版本化交付清单。源码、测试与报告同步GitHub；原始EDA证据留本地和220。

尚未覆盖：所有74785函数逐调用、全部参数/PDK/license/平台组合、真实SOS、GUI原生点击输入、失配MC、多角寄生后仿、EMIR/ESD/DFM/封装。AI Agent/模型兼容层仍是设计草案，本轮没有实现或声称模型联调通过。
