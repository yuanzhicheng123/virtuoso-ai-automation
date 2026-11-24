# AI Agent 与模型兼容层设计

2026-11-24；基线 Fusion 0.3.0 / commit 8a2f810。状态：待用户确认的具体设计，尚未实现；不改变 STATE.md 中现有发行验收结论。

目标：用 AI 自动化模拟/RF 设计。兼容层让不同 Agent/模型使用同一组已经存在的工程工具，同时保留原始执行状态和设计验收证据。兼容协议不代表模型具备正确设计能力。

## 已检查的现状

- `mcp.py` 从固定上游生成26个工具，再添加5个工具，共31个；该清单与handler是事实来源。
- 31项完整定义 JSON 约16931字符（Python默认JSON编码；不是token数）。74785项注册表通过搜索/通用调用访问，不应展开为74785个模型工具。
- `server.dispatch_tool`处理异常和限流，但未统一按inputSchema验证输入；兼容层不能把模型生成参数直接视为已验证类型。
- 现有工具包含开放字典/任意数组，不全部满足严格结构化输出子集；不允许为了导出成功静默删除字段或把可选参数全部变成业务必填。
- 上游MCP执行器持有进程级客户端和缓存。首版一个进程绑定一个既有EDA配置；多个profile通过独立进程使用，不新增进程管理服务。
- 当前测试环境没有OpenAI/Anthropic/Google模型SDK，已有jsonschema验收依赖；本轮没有读取密钥或发出模型请求。

## 方案比较与选择

| 方案 | 收益 | 代价/边界 |
|---|---|---|
| 仅维护MCP | 最少改动，支持已有MCP客户端 | 无法直接供自建模型tool-calling loop使用 |
| **统一工具目录＋薄协议适配（推荐）** | 复用全部现有执行能力，多个模型共享逻辑，依赖少 | 不负责模型路由/会话编排；需要明确协议配置 |
| 引入完整Agent框架/模型网关 | 内置更多Agent生命周期能力 | 扩大依赖与运行面，不能自动解决EDA的恢复/签核问题 |

选择第二种。新增代码限制在兼容协议和必要入口校验；不复制原理图、Maestro、Spectre、Calibre实现，不修改生成的vendor文件。

## 对外职责与模块边界

建议增加 `virtuoso_fusion.agent` 入口；协议转换实现可独立放在 `agent_protocols.py`，只在确有必要时再拆分。

1. **工具定义导出**：从现有31工具生成选定协议定义；调用方按工具名显式选择子集。不默认向模型推送整个注册表。
2. **调用解析**：接收SDK转成的普通JSON对象；输出关联ID、工具名、参数对象。原始模型响应由调用方持有，不把不同厂商的会话历史压成一个有损通用格式。
3. **执行**：解析后的外部参数在Agent入口按原始Schema校验一次，然后复用现有dispatcher。参数无效或工具未在选择集时不进入handler。未知执行结果不自动重放。
4. **结果回填**：保留call ID，按协议生成tool result；完整原始MCP结果单独返回调用方，包含structuredContent、isError、receipt、warnings与文件引用。

公开操作以 `tools`、`parse_calls`、`execute`、`format_results` 四个职责为基础；具体签名在实现计划中固定。使用标准dict/dataclass即可，不增加插件注册中心或抽象基类体系。

## 首批协议与能力配置

用户尚未指定厂商优先级，暂按下表拟定；按API协议选择，而不是用模型名字符串猜测能力。

| 配置 | 工具定义/调用/回填 |
|---|---|
| MCP | 保留现有stdio服务，继续支持MCP Agent |
| OpenAI Responses | 顶层function定义；function_call中的call_id；function_call_output |
| OpenAI Chat Completions | function嵌套定义；tool_calls；role=tool与tool_call_id |
| Anthropic Messages | input_schema；tool_use；tool_result与tool_use_id/is_error |
| Gemini Interactions | 当前官方function工具定义；steps中的function_call；function_result关联call_id |

本地Qwen/DeepSeek等服务若实现Chat Completions协议，可选择该配置；必须记录真实服务端、模型ID和能力测试结果，不能按品牌宣称全面兼容。Gemini旧generateContent、云平台专有封装属于另外的协议配置，首版不隐含承诺。

调用方负责模型请求、认证、会话历史、模型选择、重试预算和流式组装。适配层只接收完整工具调用；不执行尚未结束的参数流。保留厂商返回的reasoning/thought/signature内容及工具调用原顺序，提供示例说明如何接回原会话。

## Schema与错误语义

默认原生定义使用非strict格式，明确暴露兼容模式；不能把原MCP Schema原封不动标为strict=true。

严格模式采用显式选择的调用封装：工具名枚举＋`arguments_json`字符串。外层满足固定Schema，内层JSON仍需按原始工具Schema验证。代价是模型多生成一层JSON字符串；不得称为模型侧保证了全部内部参数。原生逐工具strict转换不在首版自动推导。

入口校验使用现有验收中使用的jsonschema，放入可选 `agent` extra并延迟导入；普通Fusion/MCP安装不强制引入模型SDK或Agent框架。其余内部层继续接收已确定类型，不添加重复防御检查。

工具选择与现有写策略保持不同含义：允许使用evaluate即允许其已有任意SKILL能力；readOnlyHint仅是提示，不能当安全沙箱。适配层不得自动设allow_write=True。主机既有授权策略继续生效。

结果区分：参数无效、工具执行失败、未知执行结果、设计检查不通过。已有 `execution_ok` / `checks_passed` / `extraction_complete` 原样保留；不以进程退出零覆盖DRC失败，不把PEX完成改成芯片PASS。不新增“自动可重试”猜测字段。

## 多模态、数据量与长任务

首版核心路径为结构化文本结果。完整原始content始终向调用方保留；不能将图片base64塞进普通文本，不能静默抹掉图片后把纯图片工具说成已成功返回可读结果。文本配置不提供纯截图工具；混合结果若只回填文本，必须列明省略的内容类型，调用方可取原始图片。

首版不自行截断JSON或统计数据；优先选择已有的limit、signals等参数缩小结果。返回体测量计入验收，结果分页/文件服务须由实际负载驱动另做，避免仅包装接口就引入存储系统。

现有MCP物理验证仍同步阻塞，timeout仍按阶段，取消通知不杀进程。兼容层不宣称解决这些问题；首版示例保持串行执行同一EDA会话。接受模型一次生成多个调用不等于自动并行修改OA数据库。

## 验收要求

- 各协议工具导出、调用解析、关联ID与结果回填的完整离线往返；覆盖同名工具多次调用、空工具调用、不支持的消息形态、非JSON参数。
- 真实输入Schema拒绝缺字段/错误类型/额外字段，拒绝后handler不执行；开放字典和嵌套列表不丢信息。严格封装内层仍实际验证。
- 通过真实dispatcher执行离线工具发现、受控netlist检查和临时文件中的失败场景；不在测试里复制业务实现。
- DRC失败、PEX完成、unknown receipt和错误关联必须完整保留；不因协议转换触发第二次执行。
- 原MCP stdio、现有单元/安装包回归不得退化；兼容模块导入/工具导出不展开大注册表。
- 分别报告：协议夹具通过、真实Agent接入通过、真实模型HTTP调用通过、220 EDA链路通过。仅离线夹具不能写“所有模型兼容”。
- 220新验收通过兼容入口执行受控只读查询；需要写入的探针沿用独立工作库。既有24项真机验收不自动计为新增适配层验收。
- 若没有配置真实模型服务，留下可运行示例和明确未测项，不向猜测的endpoint发请求、不产生虚构云模型结果。

## 交付与实施顺序

1. 固定协议、最小公开API与Schema失败语义；增加契约测试。
2. 实现目录导出/转换/入口校验/既有dispatcher接线。
3. 添加MCP Agent配置示例与各协议调用循环示例；依赖均为调用方选择。
4. 跑离线、仓库外安装、输入错误与数据量验证；再跑220受控接入。
5. 更新README、兼容矩阵、STATE与交付清单；所有能力声明带验证层级。

不改变0.3.0既有交付物。新功能验收完成后再确定新版本发行；当前仅生成设计文档。

## 官方参考（2026-11-24核读）

- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)：Responses/Chat两套工具与结果格式、strict约束。
- [Anthropic Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)：tool_use_id关联、tool_result/is_error、消息块顺序。
- [Gemini Function calling](https://aistudio.google.com/docs/function-calling)：Interactions步骤与函数调用循环。
- [Gemini Thought signatures](https://aistudio.google.com/docs/thought-signatures)：会话状态与thought步骤处理边界。

## 设计检查记录

已完成仓库/执行路径检查、三种方案对比、官方协议核对和本文一致性检查。无UI视觉任务；未生成新框架或实现代码。待用户确认首批协议与本设计后，进入具体实现计划和回归验证。
