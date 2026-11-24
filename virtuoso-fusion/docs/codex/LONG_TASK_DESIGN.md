# 长任务 submit / status / result：独立设计，尚未实现

日期：2026-11-24。审查基线：Fusion 0.3.1，提交 `433e2f0`；上游 lite `672b451`。0.3.2的输入校验、缓存、registry、PEX及远程耐久验证另行进行。**本文件是设计提案，不是已实现接口、发布承诺或真机验收结果；本次不启动EDA、不修改vendor、不实施长任务服务。**

建议增加三个短请求 `job_submit` / `job_status` / `job_result`，首版仅包装MCP服务器所在Linux主机上的独立Spectre网表仿真与Calibre内建DRC/LVS/PEX。工作由独立Python子进程完成，MCP主循环继续响应协议请求。默认一个活动作业、没有排队，不加入DAG、分布式调度、数据库或新的异步框架。运行中取消首版明确不支持；未知状态不自动重跑。

## 1. 要解决的实际问题

[0.3.1流畅度验收](CALL_SMOOTHNESS_031.md) 已测到：同一stdio连接先执行 `evaluate: progn(sleep(1) 3)`，后续ping等待 **1003.292ms**。独立控制连接可以立即处理纯协议ping，但没有使同一CIW并行执行。当前长任务等待不是猜测。

此设计使**新submit路径**在长任务运行时仍能处理同连接ping/status。它不改变旧同步 `evaluate`、`simulate`、`simulate_many`、`run_physical` 的阻塞契约；继续调用这些旧工具仍会占住该连接。尤其不能宣称把任意长SKILL移到后台便可并行修改同一OA数据库。

另外，本轮已有MCP实测在tools/list、discovery、DRC、PEX的一组响应中输出73,055bytes。status/result应返回小摘要和产物引用，不重复发送完整波形/所有文件hash。这是已有响应体积的约束，不是凭空引入通用对象存储。

## 2. 现有实现与可复用边界

| 实现 | 已有能力 | 不能据此宣称的能力 |
|---|---|---|
| [Fusion PhysicalVerification](../../src/virtuoso_fusion/physical.py) | 新建独立run目录；逐phase日志；真实进程返回值；DRC/LVS判据；三阶段PEX；manifest与hash | 公共运行中cancel句柄、整体deadline、服务重启自动接管均没有 |
| [Fusion MCP适配](../../src/virtuoso_fusion/mcp.py) | 注册工具；调用现有物理API；保留结构化结果 | 当前 `_run_physical` 直接等待结束 |
| [固定MCP实现](../../src/virtuoso_fusion/_upstream/mcp_server.py) | `for line in sys.stdin → handle → dispatch_tool`；simulate调用同步runner；simulate_many最终也等待一批结果 | `notifications/cancelled`直接忽略；同通道不能在执行中分派ping |
| lite `SpectrePool` | `submit → Future`，每任务独立workdir；显式max_workers；wait_all/shutdown | Future不持久，不能恢复到另一Python进程；运行中Future不能强制取消模拟器 |
| lite `MaestroJobManager` | `submit/load/list/status/log/reconcile`；持久run_id、endpoint与request handle；完成callback；status读标记，不重复发送SKILL | 没有统一Spectre/Calibre任务接口，没有公共cancel；Maestro提交仍是CIW操作 |

上游依据：固定提交的 [Spectre runner](https://github.com/Arcadia-1/virtuoso-bridge-lite/blob/672b451416b1ef3cc560db4196365075e94c8d4e/src/virtuoso_bridge/spectre/runner.py) 与 [Maestro jobs](https://github.com/Arcadia-1/virtuoso-bridge-lite/blob/672b451416b1ef3cc560db4196365075e94c8d4e/src/virtuoso_bridge/virtuoso/maestro/jobs.py)；本次读取的是对应本地固定源码。

**不复制Maestro的恢复逻辑。** 将来纳入ADE长任务时，应薄适配其已有manager，并保留原endpoint/history/request证据及unknown含义。该接入不属于首版范围，不能因存在该类就把当前MCP宣称为已支持异步Maestro。

## 3. 方案选择

| 方案 | 收益 | 代价/边界 | 选择 |
|---|---|---|---|
| 独立控制连接，保持所有旧调用同步 | 立即绕开ping排队，无产品改造 | 没有统一job handle/result；任务连接仍长时间等待 | 现阶段可用操作约定 |
| 单后台线程＋Future字典 | 代码表面最少，stdlib即可 | 不能停运行中Future；进程退出要等线程；旧handler存在全局stdout重定向，直接线程化会影响主线程协议输出 | 不直接采用 |
| **每作业独立worker进程＋小JSON状态文件** | stdout/stderr隔离；父服务只处理短请求；复用同步runner；保存完成证据 | 要定义退出/重启unknown；额外Python启动成本；不自带远程取消 | **推荐最小实现** |

选择进程有具体代码依据：vendor `dispatch_tool` 使用 `contextlib.redirect_stdout(sys.stderr)`。如果在后台线程中保留该上下文，同时主线程执行 `_reply`，二者共享 `sys.stdout`，JSON-RPC响应可能被重定向；若去掉重定向，runner/初始化中的普通print又可能污染协议输出。这个跨线程重叠路径可构造复现，但目前未做概率测量；**不估计发生概率**。Python官方也说明该重定向有全局副作用，不适合多数线程应用。[contextlib官方说明](https://docs.python.org/3.13/library/contextlib.html#contextlib.redirect_stdout)

因此worker调用公开Python runner，不调用vendor的 `dispatch_tool` 或MCP formatter；worker的OS级stdout/stderr在启动时直接接到文件。主进程仍沿用现有同步协议分派，无需改vendor主循环。新增的仅是三个快速tool handler和一个受控worker入口。

## 4. 最小接口契约

以下名字均为提案，不存在于当前发行包中。

### `job_submit(operation, arguments, job_id)`

- `operation`仅为 `simulate` 或 `run_physical`；后者kind限现有内建DRC/LVS/PEX/RCX。不接受任意Python callable、shell文本、任意SKILL、动态插件或已有CIW对象句柄。
- `arguments`沿用相应公开Python接口语义；服务器配置固定 `jobs_root`。netlist、PDK、deck及工具路径都属于**服务器主机**，不是调用者笔记本路径。
- `job_id`由调用方事先生成，例如UUID hex。它是作业键，不是JSON-RPC request id。这样submit回复丢失时仍有可查询的键；同一键目录已存在便返回已有状态，绝不重新启动。若operation或规范化参数摘要不同，返回 `JOB_ID_CONFLICT`。这只是一个目录与请求摘要的查重，不宣称分布式exactly-once，也不引入重复提交缓存平台。
- 先做现有入口规则的参数规范化和校验；与同步API共享同一入口逻辑，不在worker和内部函数重新做同一套类型检查。worker接受由本服务写出的规范化请求；文件在实际打开时产生的IO错误仍按真实执行失败报告。具体复用0.3.2的公共校验边界，不能复制一份规则。
- submit只做小参数/路径检查、占用一个作业槽、写请求与初始记录、启动worker。PDK文件大规模hash、复制、SSH发现、许可证探测、仿真、报告解析均不在主请求里执行。服务首次启动前应准备好环境；状态查询也不调用EDA。
- 默认已有一个活动作业时返回 `BUSY` 与现有job_id，不建立等待队列。该限制是**这个服务/工作区**的资源预算，不是全主机license调度。首版只支持一个MCP服务管理一个jobs_root；多服务共享root不在支持范围，不为此增加分布式锁。
- 成功提交返回 `job_id / state=ACCEPTED / job_dir / cancel_supported=false / survives_normal_connection_close=true`。ACCEPTED只说明任务已接收，不是工具启动成功、更不是设计通过。

示意：

```json
{"operation":"run_physical","job_id":"82b58f578e9949d29d7842fb64e5110179","arguments":{"kind":"drc","deck":"/pdk/drc.svrf","layout":"/work/dut.gds","primary":"dut","timeout":240}}
```

worker给PhysicalVerification传入 `<job_dir>/artifacts` 作为work_dir；它依然创建原有 `physical-…` 子目录。Spectre使用显式本地模式和 `<job_dir>/artifacts`，不沿用所有任务共享的 `./output`。不让 `from_env()` 在后台悄悄选成远程执行；远程Spectre是后续单独适配，当前应返回不支持。

### `job_status(job_id)`

读小状态文件及本服务已有的worker句柄，不重新读取大报告、扫描全部产物或发送SKILL。返回：

- `job_id, operation, state, created_at, updated_at`；
- `owner_host, owner_service_id, job_dir`；
- `phase`仅在adapter实际提供时填写；首版可以只有 `running`，不猜百分比、不按日志行数伪造进度；
- `cancel_supported=false`、已知异常的简短诊断、终态时的 `result_ref`。

使用同一个job_id重复查询不修改任务、不重新运行。状态的更新时间不是heartbeat；没有心跳时，不能凭文件“最近没变化”判任务已死。

### `job_result(job_id)`

立即返回；未完成时返回 `ready=false` 和当前状态，不等待到任务结束。完成后返回 `ready=true`、最多16KiB的确定结构摘要、原始manifest/PSF/log路径和已存在的hash引用。

- 物理结果保留 `execution_ok / checks_passed / extraction_complete / status / errors / warnings` 的含义；DRC违规的任务可以正常执行结束，但 `checks_passed=false`。
- Spectre保留runner的执行状态、错误、输出目录和必要元信息；波形通过现有read_waveform按信号读取，不再把全量数组塞入job_result。
- 超过摘要上限的诊断仅截断展示，并给完整诊断文件引用及 `truncated=true`；不截掉状态/判据字段，不宣称16KiB覆盖全部证据。
- 不复制一遍已在物理manifest中的全量artifact字典；result指向原始证据。读取结果不是新的签核行为。

这是项目自定义tool API。首版不宣称实现MCP标准的task扩展，不添加 `tasks/*` capability。

## 5. 工作区、状态与所有权

目录约定：

```text
jobs_root/<job_id>/
  request.json       # 规范化请求、请求摘要、服务/版本/主机来源；不写token
  state.json         # 小状态，worker逐次以临时文件+replace更新
  result.json        # 完整写好后才发布的有界结果摘要，引用原始证据
  worker.log         # worker stdout/stderr；不与MCP stdout混用
  artifacts/         # 现有runner的实际产物与manifest
```

文件更新使用同目录临时文件加replace，解决status与worker同时读写JSON这一正常路径；不增加WAL、事务数据库或重放日志。原始输入默认是外部只读引用，调用方不得在运行期间修改；输入hash记录实际runner读取的版本，**不承诺submit瞬间已快照所有PDK/include文件**。不能为了快速submit偷偷省略来源，也不能把大规模快照放回协议主线程。

| state | 含义 | 允许转移 |
|---|---|---|
| `ACCEPTED` | 请求已保存、worker启动已安排 | RUNNING、FAILED、UNKNOWN |
| `RUNNING` | 本服务仍持有该worker句柄且正在运行，已有开始记录 | FINISHED、UNKNOWN |
| `FINISHED` | worker发布了匹配job_id/请求摘要的完整result | 终态；内部设计判据可通过或不通过 |
| `FAILED` | 可以明确证明在工具启动前失败，如worker无法启动、导入/准备失败 | 终态；保留原因，不自动重试 |
| `UNKNOWN` | 服务不再掌握执行、worker异常退出且无完整结果，或重启后只有非终态记录 | 有完整匹配结果后可FINISHED；否则人工/原有工具核对 |

工具正常返回错误/timeout也是可记录的结果，job可为FINISHED，结果里的工具状态仍失败/timeout；FINISHED只表示接口处理和证据记录结束，不表示设计正确。若worker在已启动工具后崩溃而没有最终结果，应为UNKNOWN，不能仅凭worker退出码把整个EDA任务认定已停止。

MCP父服务拥有submit/状态接口和当前worker句柄；worker拥有本次Python runner及其工作区；实际模拟器的启动、timeout与清理由现有runner负责。父服务不可关闭用户原有Virtuoso、删PDK、杀同账户其他任务。进程PID只用于诊断，不能在新服务中凭旧PID对外发kill。

服务退出与重启约定：

1. **正常stdio EOF/连接关闭**：不再接受请求。worker使用独立session、`stdin=DEVNULL`、stdout/stderr文件，不持有父服务协议管道；继续完成已接受的本地作业，最终写result。父服务不因连接断开删目录或取消任务。此行为须在Linux验收，不承诺被OS/管理员杀死、主机断电后仍继续。
2. **父服务异常退出**：worker可能继续，也可能被所在环境一起终止。没有证据就不能选择其中一种结论；保留工作区，不自动重新提交。
3. **新服务读取旧作业**：完整匹配result可直接作为FINISHED读取；仅见ACCEPTED/RUNNING文件时先返回UNKNOWN，不能因旧PID“存在”就宣称接管成功。之后worker若正常发布完整result，可读为FINISHED；没有结果则保持UNKNOWN。首版不实现对陌生进程的自动收养或递归进程探测。
4. **未解决的旧UNKNOWN作业**：该jobs_root默认不再启动新作业，明确返回需核对的job_id，避免悄悄绕过单活动作业预算。人工或已有工具确认真实进程/产物后，才能归档旧root并显式配置新的空root继续；不提供一个把UNKNOWN随意改成“已停止”的开关。若该人工恢复成本不可接受，应另提持久worker监管需求，不能在本版宣称已解决。
5. **提交回复丢失**：调用方用事先保存的job_id查询或重发相同submit；存在的请求只返回现状。服务崩溃点若留下请求但没有结果，仍遵守UNKNOWN，不从目录存在推断“未执行所以可重发”。

以上UNKNOWN语义故意保守地表达证据不足，不声称失败概率。它只处理用户明确要求的退出/重启边界，不增加跨主机租约或共识系统。

## 6. 取消与timeout的真实边界

**首版没有 `job_cancel` 工具；每份handle明确 `cancel_supported=false`。** 不返回“取消成功”来代替实际停止EDA。已有MCP取消通知引用的是在途JSON-RPC请求，已返回job_id的submit请求已经结束；通知不能自动等同停止长期作业。[MCP取消规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)

- `Future.cancel()`不能取消已运行的任务；`shutdown(cancel_futures=True)`也只取消尚未开始的Future。`shutdown(wait=False)`不会令正在运行的线程工作自动终止，Python退出仍受这些任务约束。这是SpectrePool不能直接当作cancel服务的原因。[Python futures官方文档](https://docs.python.org/3.13/library/concurrent.futures.html)
- PhysicalVerification为每个Calibre phase启动新session，timeout时对该phase进程组发SIGKILL并wait。**只杀外层worker的进程组不能据此保证杀掉已经自成session的Calibre子树**；所以不能把 `Popen.kill()`包装成通用cancel。
- 本地Spectre使用同步subprocess.run；超时会处理其直接子进程，但通过csh/sh或站点wrapper启动时，不能从这点推断所有后代/许可证都已释放。远程SSH路径还可能留下远端执行。当前没有可供统一job接口复用的完整取消确认句柄。[Python subprocess官方文档](https://docs.python.org/3.13/library/subprocess.html)
- `timeout`沿用底层契约：Calibre为**每phase**预算，PEX可消费三个预算；Spectre为runner执行预算，不是从submit开始的总墙钟deadline。首版不额外引入一个会只杀worker、可能留下求解器的“统一总deadline”。排队不在预算内，因为首版没有队列。
- 用户如果需要运行中真正取消，需单独定义adapter如何持有实际进程/远端run句柄、发终止、wait确认、保留残留诊断和回收许可证证据；未确认停止只能返回UNKNOWN。Maestro也不能通过杀CIW来充当精确取消。

## 7. 接入点与不实施的部分

计划只在Fusion自有代码增加小作业模块/worker入口及MCP三个handler。现有同步API继续提供原结果；worker复用其执行核心。若入口校验要共用，沿0.3.2的公共规范化入口复用，避免在MCP、worker和runner分别抄一份。

不改vendor MCP引擎、不引入Celery/RQ/Redis/FastAPI，不将任意SKILL后台化；不默认增加EDA并发，不重写SpectrePool，不复制MaestroJobManager。`simulate_many`、远程Spectre取消/恢复、ADE async接入及整体deadline留在首版之外。首版可把MCP部署在220并使用本地工具；用户通过远程连接调用MCP不等于worker内部又有一套SSH作业管理。

这是把长外部工具与短协议请求分开，不是构建芯片自动化调度平台。AI的优化策略、corner预算、设计验收与阶段恢复仍由上层工程流程管理。

## 8. 验收门与未知项

以下是未来实现后的验收要求，**本次没有执行，不能标PASS**。

| 验收 | 具体判据 |
|---|---|
| 解决已测阻塞 | 用真实受控子进程执行至少1s的等待任务；同一stdio连接submit后立即发送20次ping/status。11轮中submit median≤250ms、ping/status p95≤50ms，且每轮至少有一个ping在任务完成前返回。阈值为本地筛查目标，不是WAN/SLO保证；保留全部样本 |
| 正常短响应与输出隔离 | worker持续输出stdout/stderr，MCP每行仍是合法JSON-RPC，无日志混入；stdout/stderr仅出现在本job日志。不要用mock替代实际子进程边界 |
| 状态/结果判据 | 一个正常任务、一个启动前错误、一个工具正常返回失败；FINISHED不抹掉DRC失败，PEX完成仍不宣称signoff；尚未完成result不等待、不返回旧结果 |
| 容量与重复提交 | 同时只接受一个活动任务；另一个job_id明确BUSY；相同job_id/参数重发不多启动一个进程，不同参数冲突；对文件中记录的启动次数核对 |
| 连接关闭 | 真实启动worker后关闭MCP stdin，父服务退出；worker按约定继续并写终态；另起MCP能读取结果；没有残留MCP stdout pipe导致读取者一直等EOF |
| 异常退出与重启 | 受控杀父服务；再分别测试worker继续完成、worker异常退出无结果。新服务非终态必须UNKNOWN、不重发；匹配完整结果才FINISHED。只操作测试自有进程 |
| 超时与取消诚实性 | 验证旧取消通知不被宣传为job取消；handle始终cancel_supported=false。分别测试物理phase timeout与Spectre wrapper路径；没有确认的后代清理须记录为未知，不能通过“worker没了”判成功 |
| 结果与内存 | status/result响应≤16KiB，完整日志/manifest仍可定位；大报告hash/波形解析不在主请求执行。只测响应上限，不据此承诺整个仿真峰RSS |
| 220真实工具 | 独立工作区各跑一个Spectre与Calibre长任务，期间同连接ping；核对结果、run目录和现有同步API等价。若fixture不足1s，应选真实有代表性的较长输入，不把sleep替代EDA验收 |
| 不回归现有契约 | 原同步工具和输入校验回归通过，旧参数错误仍可诊断；没有并行修改OA、没有关闭用户CIW/覆盖PDK |

需要实测、当前无法量化的事项：目标220 Python启动与本地文件系统延迟；站点Spectre wrapper的后代与timeout清理；父MCP正常退出后worker是否被宿主监督器一起收回；许可证等待与释放；真实Spectre结果解析内存；部署环境对分离session的限制。已有1s同通道阻塞是实测事实；这些未知的失效概率没有测量，不能编造百分比。

## 9. 实现成本与实施前决策

粗量级估计：Fusion自有生产代码约 **250–450行**（三个handler、job记录/读取、worker入口、两个固定adapter与异常结果），离线/子进程及真机验收约 **200–350行**，不计复用的runner和已有测试工具。熟悉本仓库者预计 **2–4个工程工作日**完成实现与本机/220验证，取决于退出/恢复实测是否暴露runner边界；这不是交付工期承诺。若需要跨主机自动接管、运行中可靠cancel、共享多服务或持久队列，超出此估计，必须重新说明具体需求与成本。

实施前只需确认本提案的三条产品契约：**首版本地两类外部工具、一个活动作业无队列；连接正常关闭后已接受任务继续；运行中取消明确不支持、重启非终态先UNKNOWN。** 若这些契约不满足实际操作要求，应修改设计再实现；不能为了让界面看起来“全异步”隐藏无法兑现的取消/恢复语义。

当前结论：**可进入独立设计评审，尚未实施。** 0.3.2其它已授权优化不因本提案而扩大为调度平台改造。
