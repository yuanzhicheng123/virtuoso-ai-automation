# 融合能力与验收边界

新库依赖固定提交的上游实现，而不是把两套同名包同时安装。FusionClient继承真实VirtuosoClient；工程域与通用调用共享认证会话、执行入口和结果对象。以公开对象和实测证据区分接入与验收。

| 能力 | 新库入口 | 来源 | 当前验证层级 |
|---|---|---|---|
| 注册表/签名/检索 | ApiRegistry、client.registry | 本地0.8.1快照与修复 | 完整快照74785条，61979有签名；哈希和离线测试 |
| 通用函数调用 | client.call、client.generic | 本地参数编排迁移至上游执行 | 真机变长参数、kwargs、Symbol、重复/线程调用、失败传播 |
| 库和原理图 | client.library、client.schematic | upstream工程域 | 真机新库、新原理图、读取；与generic共用session和证据 |
| 原理图检查/CDF/网表/manifest | client.schematic.* | upstream工程域 | analogLib RC 的 OA→原生导出→DC/AC 数值闭环；复杂层级/config 尚未签核 |
| 符号/版图/GDS | client.symbol、client.layout | upstream工程域 | N65 tech attach、几何变换、PCell受控尺寸、保存重开、GDS 真机通过；完整布局布线及符号全能力未签核 |
| ADE/Maestro/Corners/MonteCarlo/jobs | client.maestro | upstream工程域 + Fusion corner修复 | RC sweep×corner共4次实际仿真、RDB核对、MC配置持久化；ADE MC执行未验收 |
| SOS与GUI/请求恢复 | client.sos、client.dialogs、client.requests | upstream工程域 | 保留原接口/metadata；本次未做全流程真机签核 |
| Spectre与结果读取 | SpectreSimulator、SpectrePool；upstream.spectre.psf | upstream引擎 | N65 OP/DC/Tran、AC/noise/SP/PSS/PNoise/PAC/HB、27组PVT、3次process-only MC及同seed复现；非设计指标或yield签核 |
| DRC/LVS/PEX | PhysicalVerification；physical CLI | phase3接口融合 + 已验证Calibre流程 | 0.2.0正式库API；连接fixture DRC52结果正确返回失败、四端口LVS正确、PEX三阶段27R/32C及前后仿；非DRC/ERC-clean芯片 |
| OA复制/映射导出/扩展 | client.oa、register_flow/register_parser | phase3融合 | 顶层复制、XStream/oa2strm命名映射真机通过；自定义runner/parser已接线 |
| 远程SSH/多profile/实例发现 | FusionClient.from_env、upstream CLI | upstream配置与传输 | 代码和相关离线验证；本次真机采用本机认证TCP |
| CLI | virtuoso-fusion、virtuoso-bridge | 融合独立命令+upstream完整CLI | 仓库外安装及真实CLI子进程 |
| MCP和agent skills | virtuoso-fusion-mcp | 上游26工具+新增5工具，5项技能 | 真实stdio discovery/list/resource/离线registry，保留错误metadata |
| 执行证据 | FusionClient(evidence_dir=...) | 新融合执行入口 | JSONL中工程调用与generic具有相同client_id |

注册表中的DISCOVERED/DOCUMENTED/CALLABLE等历史标签不自动提升为新库的ENGINEERING_VALIDATED。`availability` 返回候选上下文，不把仅存在于注册表当作可运行保证。

原0.8.1基于Unix socket的SkillBridge客户端未叠加启动；它的“禁止盲重发、明确结果、只读包资源、参数不截断”原则在新入口沿用。原来的Python原生返回值改为完整VirtuosoResult，迁移时要检查.ok/.output/.errors/.metadata。

策略只约束通用函数调用中已识别的写操作；工程域和raw SKILL不是受该允许清单保护的全局沙箱。多客户端/多步骤事务并发不在本次验收范围。

新库和上游结果的正式验收记录以docs/codex/STATE.md为准，不以此静态接入清单替代运行证据。

本轮原生 `oblist` 审计得到已加载函数 116,738 个，注册表匹配 74,776（64.0545%）；41,962 个当前函数不在快照。generic 没有 OA 对象句柄封装，并存在语法/策略边界。未收录不代表 raw SKILL 无法执行，已绑定也不代表参数、上下文和副作用均已验证。数字综合/P&R/STA、完整物理签核、后仿、EMIR/ESD/DFM、流片交付仍不构成完整能力闭环。

本表以 [STATE.md](codex/STATE.md) 为当前验收权威；逐工具证据见 [逐项审查](codex/API_COVERAGE_20261124.md)。0.3.1波形接口拒绝无效PSF和无时间轴的at；不声称所有31工具已做完整端到端验证。
