# 模拟/RF AI 自动化：体积、调用成本与架构审查

审查日期：2026-11-24。基线为已经发布并独立安装的 Fusion 0.2.1 + 固定 lite `3f0bd7d`；修复后为独立安装的 Fusion 0.3.0候选 + lite `672b451`。两轮使用完全相同SHA256的脚本，各自环境执行 `python -I`，不通过源码目录导入。本文将“库运行质量”与“芯片电气/物理签核质量”分别判断。

## 结论与决策

Python 适合担当本项目的编排、配置、工具适配、结果整理和 AI 接口语言。0.2.1最明显的内存和启动负担是每个 FusionClient 都将压缩注册表展开成完整 Python 字典；0.3.0已改为默认按需加载，纯工程facade调用免于这项成本。首次实际查询generic/registry仍需展开完整字典。这属于装载策略与数据表示问题，不是将整个库重写为 C++/Rust 的理由。真正的 OA 对象操作、求解和物理验证应继续在 SKILL/OA、Spectre、Calibre 中执行。

“流畅”必须按场景拆开：长期复用一个客户端做分钟级仿真，与每次 AI 工具调用都重新创建客户端、每个器件属性都单独一次 RPC，是完全不同的成本结构。前者不会因几微秒的 Python 参数编排而卡顿；后者会放大注册表加载和远程交互成本。离线数字不证明真机延迟、并发吞吐或 license 队列稳定性。

面向模拟/RF全流程，最重要的缺口并不是增加函数名数量，而是把设计意图、参数合法性、拓扑一致性、测量判据、版图约束与寄生后仿串成可恢复且有证据的流程。库目前是经过若干真机闭环验证的工具适配层，不是可自主承担任意电路设计和 tapeout 责任的设计系统。

## 测量方法与可复现入口

原始结果：[`../../evidence/performance-030/baseline-0.2.1.json`](../../evidence/performance-030/baseline-0.2.1.json)。基准工具：[`../../tools/benchmark_library.py`](../../tools/benchmark_library.py)。

```bash
../fusion-full-install-mk_n1qn9/bin/python -I tools/benchmark_library.py \
  --python ../fusion-full-install-mk_n1qn9/bin/python \
  --repeat 11 --clients 5 \
  --release-dir ../fusion-full-release \
  --output evidence/performance-030/baseline-0.2.1.json
```

每项 11 个独立进程；median 与 nearest-rank p95 均保留。库首次导入前基准脚本已加载相同的标准库前置模块（argparse、json、importlib.metadata等），因此 `import_ms` 是此前置条件下的首次库导入，不是完全裸 `python -c "import …"` 的时间；两库比较条件相同。未绑定CPU或独占主机，其他工程任务可能影响尾延迟；N=11时nearest-rank p95等于样本最大值，只适用于成本定位，不能建立生产SLO。没有清空 Linux page cache，所以这里是“冷 Python 解释器、通常热文件缓存”，不能称为冷磁盘启动。`import_ms` 不含解释器启动；`process_wall_ms` 包含整个进程与测量脚本开销。每个进程均记录模块实际文件路径、解释器版本、当前 RSS（Linux `/proc/self/status`，KiB）和 lifetime peak RSS（Linux `resource.RUSAGE_SELF`，KiB）。RSS 差值是进程观察量，不是精确 Python 对象分配归因；peak 也不是测量区间增量。没有连接 Virtuoso，没有 mock RPC，也没有把参数编排时间当作真实调用时间。

`shared_registry=True` 是已经存在的显式 `registry=ApiRegistry()` 复用用法；`touch_registry=True` 会在构造计时区间真实查一次 `strlen`，确保延迟加载后的版本也承担第一次装载费用。普通客户端构造不会额外读取registry，专门的5客户端generic首载案例则每个实例都真实查询。它不是新增产品代码，也不证明跨版本/PDK会话可以安全共享可变签名。

测量主机：`ytai`，13th Gen Intel(R) Core(TM) i9-13900K，Linux x86_64，Python 3.11.5。

| 场景 | 时间 median / p95（ms） | RSS median / p95（MiB） |
|---|---:|---:|
| 首次导入 virtuoso_bridge | 55.83 / 80.93 | 33.45 / 33.50 |
| 首次导入 virtuoso_fusion | 70.86 / 73.61 | 36.71 / 36.74 |
| 首次导入 virtuoso_fusion.cli | 71.20 / 83.60 | 36.68 / 36.74 |
| 首次导入 virtuoso_fusion.mcp | 72.77 / 79.92 | 37.06 / 37.12 |
| lite 1 clients（仅构造） | 17.39 / 17.96 | 3.34 / 3.34 |
| lite 5 clients（仅构造） | 17.46 / 18.99 | 3.34 / 3.35 |
| fusion 1 clients（仅构造） | 378.62 / 385.90 | 146.17 / 146.17 |
| fusion 5 clients（仅构造） | 2292.29 / 2354.81 | 714.91 / 714.91 |
| fusion 5 clients（每实例实际首载） | 2282.48 / 2336.50 | 712.38 / 712.39 |
| fusion 5 clients（共享registry，实际首载） | 376.81 / 398.00 | 142.66 / 142.66 |
| ApiRegistry实际首载 | 386.05 / 423.39 | 142.42 / 142.43 |

导入行RSS为导入后进程总量；构造/首载行为相对导入后的RSS增量，不能混为同一指标。峰RSS与每次原始样本完整保存在JSON；close的median均低于0.02ms，仅代表清理客户端资源，不代表卸载对象树。

| 本地操作 | median / p95（µs/次） |
|---|---:|
| get已知函数 | 0.045 / 0.049 |
| search dbOpen（最多20条） | 7022.792 / 7617.423 |
| search不存在项 | 6501.037 / 7431.230 |
| coverage完整统计 | 30611.542 / 36439.540 |
| strlen单参数kwargs编排 | 1.783 / 1.862 |

这些值是每个子进程多次循环的均摊后再求median/p95，不是单个查询的尾延迟。



安装体积要与运行时展开分开判断：基线 Fusion wheel 为 **2,274,504 bytes（2.17 MiB）**，lite wheel **547,200 bytes（0.52 MiB）**。不含 pyc 的安装文件分别 **2,544,363 bytes / 2,032,825 bytes**。Fusion 内的 gzip registry **2,084,611 bytes，占本包81.9%**，解压JSON约 **34.17 MiB**，实际对象树驻留明显更大。Fusion安装包共16个Python文件/3,294物理行（含上游MCP与附带脚本），lite共106个Python文件/51,037物理行；这不等于圈复杂度，也不代表行数越少越好。

Fusion核心直接依赖只有lite，未新增运行时框架。此Python3.11/Linux默认安装的lite基础直接依赖为 python-dotenv、Pydantic、PyYAML、markdownify；含传递依赖共10个第三方运行时发行包（另有Fusion和lite自身），具体版本在证据的 `inventory.distributions`。该环境也安装pytest/build等验收工具，**整个venv磁盘占用不能算作库运行时依赖体积**。SSH和图像extras本基准未安装，不代表带Paramiko/Pillow的完整环境同样大小。

## 0.3.0按需加载后的同条件复测

候选证据：[`after-0.3.0.json`](../../evidence/performance-030/after-0.3.0.json)。两轮均为同一主机/Python3.11.5，每项11个独立进程，基准脚本SHA256完全一致；旧新上游也同时由 `3f0bd7d` 升到 `672b451`，所以这是发布候选整体对照，不是只改变一行代码的实验。基础运行时依赖版本一致；验收环境的dev依赖packaging由26.3变26.4，已保留完整inventory。

| 场景 | 0.2.1 median / p95（ms） | 0.3.0 median / p95（ms） | RSS median（MiB），旧→新 |
|---|---:|---:|---:|
| Fusion首次import | 70.86 / 73.61 | 71.74 / 82.28 | 36.71 → 36.77 |
| Fusion 1 client，仅构造 | 378.62 / 385.90 | 2.12 / 2.14 | 146.17 → 0.34 |
| Fusion 5 clients，仅构造 | 2292.29 / 2354.81 | 2.33 / 4.23 | 714.91 → 0.34 |
| Fusion 5 clients，每实例实际registry首载 | 2282.48 / 2336.50 | 2263.07 / 2317.70 | 712.38 → 716.35 |
| Fusion 5 clients，共享并实际registry首载 | 376.81 / 398.00 | 385.51 / 463.92 | 142.66 → 142.71 |
| ApiRegistry实际首载 | 386.05 / 423.39 | 374.65 / 401.14 | 142.42 → 142.40 |

**按需加载验收通过**：未使用registry的5客户端构造median 2.33ms、p95 4.23ms；RSS增量median 0.34MiB、p95 0.34MiB，均低于预先设定的50ms/10MiB界线。这个2ms级数字不含import；该场景完整子进程wall median为 **122.13ms**。普通客户端仍有约37MiB总RSS，不是整个Python程序只占0.34MiB。

**实际generic成本没有消失**：单registry第一次访问约375ms/142MiB，五份独立registry仍约2.26s/716MiB。lazy只让不需要注册表的工作流不付费；同一个client重复get不会再次加载。后续若真实AI负载大量使用generic并持有多个client，仍应优先复用同环境客户端/明确共享对象，再评估只读快照+会话覆盖层；当前不把该额外结构提前做进库。

0.3.0注册表已装载后的local get均摊median 0.065µs；单参数kwargs编排 1.785µs；search命中约6.80ms、未命中约6.42ms；coverage约30.04ms。它们依然是本地操作，不是真机RPC延迟。

候选Fusion wheel **2,279,621 bytes**，比0.2.1增加5,117 bytes（约0.23%）；安装文件不含pyc **2,556,379 bytes**，Python物理行3,361。lite候选wheel **559,076 bytes**、安装文件2,068,457 bytes。此次功能升级与lazy修改没有造成包体积骤增；具体候选wheel SHA256保留在after证据。

运行新版本基准：

```bash
../fusion-upgrade-dev/bin/python -I tools/benchmark_library.py \
  --python ../fusion-upgrade-dev/bin/python \
  --repeat 11 --clients 5 \
  --release-dir ../fusion-upgrade-candidate \
  --output evidence/performance-030/after-0.3.0.json
```


## 确认的负担与最小改进

1. **默认加载已修复，实际generic首载仍有成本。** 0.2.1的 `client.py` 构造时直接 `ApiRegistry(registry)`；旧 `registry.py` 使用 `gzip.open → json.load → _validate_records`，即使只用 schematic、layout 或 Maestro，也付出全部成本。0.3.0将默认packaged数据移到 `raw` 首次访问时加载，custom输入仍构造时验证，每实例保留独立对象树；首次加载由实例锁保护。`close()`关闭资源但不清空registry，应用继续持有已关闭client仍会持有数据；当前证据证明驻留成本，未证明持续泄漏。本轮已采用这个最小修复，未引入全局共享可变缓存或新存储格式。预先设定的性能验收目标：未访问 registry 的 5 客户端额外 RSS 不超过 10 MiB，构造 median 不超过 50 ms（同主机、相同依赖）；第一次真实 registry 操作仍单独测量，不能用延迟掩盖它。
MCP另有 `_registry_data()` 的进程级注册表，与其客户端的 `registry` 各持一份；当同一服务既做注册表搜索又做generic调用时会存在两份对象树。这里只确认装载路径，尚未量测该具体组合。lazy改造不自动消除双方都访问后的重复。

2. **注册表查询并不都是常数时间。** `get` 是字典查询；`search` 顺序扫描最多 74,785 条，每次做 casefold；`coverage` 做多轮遍历并创建中间 public 字典。对人偶尔搜索可接受；对 AI 高频批量 discovery 应先复用客户端并缓存一次会话的 capability/coverage 结果。没有证据表明必须引入数据库/搜索服务。未来如需更快，先测真实搜索频率再考虑一次性名字索引。
3. **全局共享注册表需要谨慎区分底表和会话状态。** `GenericSkill._ensure_signature()` 会写 `registry.raw` 的 `arglist_raw`。直接全局单例虽然省内存，却会把不同 EDA 会话加载出的函数签名混在一起。现有显式共享仅建议同一已确认环境使用；若工作负载确有多个长期客户端需要 generic，再采用只读共享快照 + 每客户端动态覆盖层。该改动比 lazy load 大，不应无条件同时实施。
4. **包入口急切加载若干非本次任务所需模块。** `__init__.py` 导入 client、Spectre、physical，因而 `virtuoso_fusion.cli` 的 registry/帮助路径也先导入它们。当前测量若只增加几毫秒就不值得复杂 lazy export。保留为次优先项，先解决百 MiB 的注册表装载。
5. **MCP 快照主要是维护耦合，不是首要运行内存。** Fusion 通过生成工具固定上游 MCP/skills；lite wheel 并不向 Fusion 提供同等 importable MCP 入口，不能直接删快照。普通 Fusion import 不加载 MCP server。真正成本是上游升级后要同时同步快照、适配补丁和回归，避免两套协议代码行为分叉。短期保留生成/哈希验证，长期优先推动上游提供稳定可导入入口。
6. **大结果文本会扩大端到端内存与日志。** 基线 `_exchange_payload` 先收集 bytes chunks、join、解码；Evidence 又序列化完整结果。`PhysicalVerification` 已将进程日志直接写文件、SHA256 也采用分块，不应改坏。PEX检查仍 `read_text().splitlines()` 读完整网表。大型提取网表/波形不要经 JSON/LLM全文传输，返回受控指标+产物路径+hash，按需解析文件；只有出现真实大文件负载后再落实流式解析改动。本文没有测量大型 PEX 内存，不能给出失效概率。

## 调用流畅性的真实决定因素

- **RPC 粒度。** 每次 `_exchange_payload` 新建 TCP socket；一次 execute 还可能包含认证/状态检查。多线程无法让同一 CIW 同时执行多个数据库修改。优先把“创建一组实例并连接检查”等有确定边界的工作变成一次 SKILL 脚本，返回简洁结果。不能为了少往返构造不可诊断的无限大批次；保留阶段结果与失败位置。
- **串行限制的来源。** Fusion 的 RLock 仅保证同一个 Python 客户端不交错；daemon 的单 IPC lane 与 GUI/数据库上下文才是同一 Virtuoso 会话的主要串行边界。改用 C++客户端或去掉 GIL不等于同一CIW并发安全。并行仿真用独立 netlist/workdir + SpectrePool，受 CPU、内存和 license 额度约束。
- **初始化与生命周期。** AI agent 每步重新 new/close 会重付初始化成本；优先长连接生命周期下复用客户端（底层每请求socket行为仍存在）。关闭 Python client 不等于销毁所有 Virtuoso 会话状态，失败重连也不保证数据库回滚。
- **状态和重试。** 库禁止故障后盲目重发写操作，这是必要边界；但它并未提供跨 OA 编辑、导出、仿真和报告的事务。实际存在部分修改已执行、响应丢失、上层只看到 timeout 的路径。恢复应读取已产生的阶段证据和 OA 当前状态，决定继续/重建受控工作区，不能用“retry=3”代替设计。
- **大搜索/证据与 AI token。** 74,785 项不适合全部作为提示词或工具菜单发送。使用 bounded search、明确工程动作、当前上下文 capability 查询；把模型看到的数据限定为结构化设计参数、失败摘要和指标，而不是把所有原始波形、日志、SKILL函数名灌入模型。

## 220真机RPC与MCP：真正获益来自减少往返

已补实际安装客户端经认证的 **220本机TCP** 测量：每轮完成相同20次 `plus` 运算，串行20次RPC与单批1次RPC分别重复11轮，`log_to_ciw=False`。这是轻量算术请求；不是guarded模式、几何编辑、远程网络或Spectre仿真性能。第三轮原始结果为 [`full3-live/rpc-performance.json`](../../evidence/upgrade-030/full3-live/rpc-performance.json)，早期完整轮保留于 [`rpc-full1.json`](../../evidence/performance-030/rpc-full1.json)，没有删除慢样本或只选最低值。

| 第三轮场景 | RPC/轮 | median / p95（ms/20次运算） |
|---|---:|---:|
| lite串行 | 20 | 20.0527 / 59.6926 |
| lite批量 | 1 | 1.7025 / 1.7750 |
| Fusion串行 | 20 | 15.3785 / 55.0004 |
| Fusion批量 | 1 | 0.7187 / 0.7852 |
| Fusion启用evidence，串行 | 20 | 17.7062 / 21.7829 |
| Fusion启用evidence，批量 | 1 | 0.7223 / 1.7519 |

可以得出的结论是：**同一Fusion客户端将等价运算合并为1次RPC，明显减少这类微小任务的往返成本**；保留evidence也不应成为逐条属性RPC的理由。本轮串行→批量median约21.4倍，仅对这20次轻量运算成立，不能外推“电路设计/仿真快21倍”。首次generic调用另计 **550.572ms**，包含220上的注册表首次加载，不是常态每次调用延迟。

**不能宣称Fusion比lite快。** 早期轮Fusion批量median为1.6551ms、lite为0.6525ms；最终轮相对次序相反。早期串行Fusion16.1455ms、lite16.8821ms，p95各有55–69ms波动。这些串行采样受CIW/daemon调度和宿主机状态影响，足以淹没两库的薄适配差异；N=11的p95也只是样本最大值。带evidence场景出现较低p95同样不代表日志提高速度。此次证据支持批量边界设计，不支持语言或客户端性能排名。

真实stdio MCP验证包含 `tools/list`、discovery、DRC和PEX，整组stdout共 **73,055 bytes（约71.34KiB）**；31个工具，DRC保持 `CHECKS_FAILED`，PEX为 `COMPLETE` 并报告27R/32C。[MCP实测摘要](../../evidence/upgrade-030/mcp-live.log) 这不是单次PEX响应大小，也不是大PEX压力测试或LLM token计数，但已经表明工具目录、报告结构和证据重复返回会产生响应/日志负担。AI流程应保存详细文件、返回必要状态/指标/路径/hash，避免每一步重复回灌整份报告；不能在没有大文件测量时声称内存上限或吞吐已验证。

独立审查确认当前MCP是**同步stdio循环**：长Calibre调用会阻塞同一连接的ping/discovery，取消通知不终止进程；PEX的timeout按phase而非整条流程，三阶段总耗时可能接近三倍预算。[物理接口契约](../PHYSICAL_API.md) 这些行为不是Python语言必然限制，也不是改用async关键字就能解决的作业所有权问题。当前长任务应由显式Python/CLI调用方管理工作目录、进程与恢复；调用方超时后先核对已有产物，不能据“连接没响应”自动重发。此版本没有增加异步框架，也没有声称具备持久化作业/取消服务。

尚未测量同CIW多客户端排队分布、跨主机网络延迟、大型几何编辑/PEX/GDS内存或真实长仿真的吞吐。它们不能由上述算术基准代替。

## 部署耦合已出现真实失败，不能只看pip check

本轮以pip **23.2.1** 从0.2.1正常升级到0.3.0时，实际出现Fusion已变0.3.0、上游仍保留旧 `virtuoso-bridge 1.0.0` 的组合，且 `pip check` 仍通过：上游新旧提交对外都使用1.0.0版本号，依赖版本一致并不保证所需能力已经更新。[失败证据与reader哈希](../../evidence/upgrade-030/normal-upgrade-result.json) 这是已复现的发布/依赖识别耦合，不是假设性兼容风险，更不是Python计算速度问题。

本轮已增加最低上游能力import guard：缺少 `check_points`、`check_netlist_options` 或 `copy_point_netlist` 时立即拒绝旧依赖并提示修复；随后用交付的上游wheel强制重装，已验证恢复且reader哈希符合预期。[旧依赖拒绝证据](../../evidence/upgrade-030/guarded-old-dependency.stderr) [恢复验证](../../evidence/upgrade-030/upgrade-recovery-result.json) guard证明最低能力存在，**不等于完整提交内容哈希校验**。正式部署仍应固定交付wheel/manifest、优先新环境，或对交付上游wheel执行已验证的 `--force-reinstall --no-deps` 路径，再做能力和核心接口验收；仅记录“安装成功/pip check通过”不足以放行。

## 后续优先级

1. **先保证设计与部署的判据可靠。** 持续保留兼容guard与交付哈希；按电路目标把原生网表、带端口LVS、真实RC后仿、PVT/失配与验收指标串起来。工具退出0、函数可调用、PEX文件含R/C都不能替代电气和物理签核。
2. **把高频AI操作组织成有界工程动作。** 复用同环境client；将一组编辑/读取批量执行并保留阶段结果，而不是每个属性一次RPC。以实际几何/原理图任务测调用数、失败位置和端到端时间，再决定批量大小；目前算术基准已经支持减少往返这一方向。
3. **明确长任务所有权和结果体积。** 使用现有Python/CLI明确管理长作业、工作目录和证据；客户端预算与per-phase预算对齐，超时先恢复核对。对AI返回摘要和产物引用。同连接MCP取消/进度能力当前存在缺口，不能宣称已经实现；只有确定长任务交互需求和验收后再补对应协议行为，不先堆通用调度框架。
4. **generic多实例确实成为瓶颈时，再压缩数据表示。** 默认lazy改进已通过实测；真正使用generic仍每实例约142MiB，应先复用，再根据实际并发会话需求评估只读共享快照+会话覆盖，或更紧凑的索引。当前证据不要求立即引入数据库服务或全库重写。

Python不是本轮主要瓶颈的依据具体而有限：本地kwargs编排约1.8µs，默认5客户端构造约2.3ms；实际微小任务的20次RPC为15–18ms，合成1次便约0.7ms，首次registry又是约0.4–0.55s。可观察的大项是数据装载、往返与工具调度，模拟器和数据库计算继续在原生工具中完成。未来若大网表解析/指标批处理profiling证明Python CPU或对象开销占主导，再局部采用原生数组/库、流式解析或进程并行；当前不能把这一结论泛化成“Python永远足够快”。

## Python 的适用性与弊端

| 问题 | 语言/运行时层面 | 当前库实现层面 | 工程选择 |
|---|---|---|---|
| CPU 密集解析/几何运算 | 默认 CPython 的 Python 字节码并行受 GIL限制；对象开销大 | 大注册表JSON变大量dict/list/str；没有用Python重写模拟器 | Python做编排；热点先profile，再选原生数组/库或独立进程 |
| RPC与外部模拟器等待 | 网络/子进程等待不是纯Python计算瓶颈 | 同一CIW串行、每请求socket、粒度过细会累积延迟 | 批量SKILL；独立仿真进程并行；不为了I/O等待重写语言 |
| 动态类型/结果格式 | 静态约束较弱，错误可能到运行期才显现 | VirtuosoResult、dataclass和dict结果并存；签名快照不保证工程前置条件 | 入口schema、明确状态和回归fixture；不把每个内部函数再校验一遍 |
| 内存驻留/启动 | Python对象与import有成本 | 默认加载所有registry是可消除成本 | lazy registry；长生命周期client；避免每AI工具步骤启动Python |
| 部署可复现性 | Python版本与二进制依赖版本需对齐 | 已复现pip保留旧1.0.0上游且pip check通过；已加最低能力guard并验证强制重装恢复 | 固定交付wheel/manifest与能力验收；不误称纯Python包完全无二进制依赖（Pydantic Core等） |
| OA对象控制 | Python侧并不天然拥有有效OA对象句柄 | generic只编排值/符号，缺乏对象生命周期封装 | 把数据库对象留在受控SKILL上下文；需要时设计有明确释放语义的句柄，不伪装字符串为对象 |
| AI可靠性 | 不是Python独有问题 | 函数可调用≠参数合法、拓扑正确、指标过线 | 设计约束和验收oracle驱动；保存来源、seed、模型、deck、结果 |

CPython默认GIL对Python字节码CPU并行的限制，不妨碍线程处理多个I/O等待任务；Python3.13的无GIL支持属于特定实验性构建，不能从“版本3.13”推断正在无GIL执行。[Python线程官方文档](https://docs.python.org/3.13/library/threading.html) 不支持free-threading的C扩展还可能重新启用GIL；因此本项目不能通过更换解释器开关就假设所有依赖和会话并发都已安全。[Python3.13 free-threading官方限制](https://docs.python.org/3.13/howto/free-threading-python.html)

不建议把全库迁移到 C++/Rust、引入异步框架或切换 free-threaded Python 作为本轮默认改造。没有现成证据表明这些能解决当前首要问题；异步仅改变等待方式，不会增加 Cadence 会话/许可证的可用并行度。

## 模拟/RF AI 自动化的流程缺口

| 阶段 | 库能承担的角色 | 仍需的设计闭环与判据 |
|---|---|---|
| 需求→拓扑/参数 | 传递参数、检索操作、执行脚本 | 单位/目标/约束、合法PDK器件范围、拓扑选择理由；不能靠函数列表推导设计正确性 |
| schematic/CDF/OA | 原理图编辑、CDF调用、原生网表导出 | 端口与层级/config一致、模型section与器件合法、编辑后check/save/readback；避免只验证“命令成功” |
| Spectre/ADE→优化 | 仿真调度、corner/sweep结果、错误记录 | 指标定义一致、波形提取可追溯、收敛/不收敛分类、鲁棒PVT/失配覆盖与预算；优化算法不是盲参数随机遍历 |
| layout | 几何/PCell/导出基础能力 | 匹配/对称/走线/屏蔽/寄生/供电约束；不是具备任意电路自动布局布线能力 |
| DRC/LVS/PEX | 调度、报告判定、提取产物 | DRC清零或正式豁免、电路级端口/拓扑LVS、非平凡RC及模型映射；工具退出0并非签核 |
| 后仿→收敛 | 可调用模拟器和解析结果 | 同等testbench前后仿对照、多corner后仿、性能退化与版图修订迭代、停止条件和证据版本锁定 |
| 交付/恢复 | hash、日志、独立workdir | 阶段manifest、可恢复作业状态、deck/model/PDK授权引用、固定验收门；不把一次toyfixture当作量产保证 |

主导架构应是“AI提出受约束候选 → Python适配与编排 → Cadence/Calibre求解 → 独立测量与验收 → 回写可追溯状态”，而不是“AI拿到所有函数名后自由执行”。目前应从一条明确的模拟/RF电路贯穿流程，再扩展电路族；不要为未选定的整个芯片宇宙建立巨型统一框架。

数字AI加速器RTL、综合、DFT、P&R、STA、EMIR、封装/固件不是当前主任务，也不是Virtuoso一个库能包办的能力。未来若进入这些阶段，应通过相应工具的原生Tcl/CLI/API适配，并保留专门的签核判据；Python可继续协调，但不能因此声称当前库已覆盖。工具接口混合语言并非本库的异常：例如OpenROAD官方仍说明Tcl与有限Python支持，并列出Python未支持的读写Verilog等操作；这是保留原生工具适配的例子，不是建议本轮扩建数字流程。[OpenROAD API官方文档](https://openroad.readthedocs.io/en/latest/main/openroad/README.html)

## 本次边界

本审查子任务分析了真实实现和本机已安装包，未修改产品代码；主任务另行实现了按需加载，本文通过同脚本复测验证其性能效果。本文已核对主任务提供的220轻量算术RPC和MCP真机证据；本审查子任务没有另外启动EDA负载，也没有测大型版图/PEX/GDS内存或给出未经测量的故障概率。后续源码升级及性能修复必须在相同基准脚本、相同机器与独立安装包上对照，不能把旧版本基准当作新版本结果。


## 最终发行实现复测（full4）

完成复制修复和依赖guard后，以最终安装包再次运行同一SHA的基准，每项11独立进程：
[final-0.3.0.json](../../evidence/performance-030/final-0.3.0.json)。上述候选测量保持原值，最终复测如下。

| 最终场景 | median / p95 ms | RSS增量median MiB |
|---|---:|---:|
| 5个纯工程client构造 | 2.302 / 2.392 | 0.340 |
| 5个client各自实际registry首载 | 2232.75 / 2255.33 | 710.44 |
| 单registry首载 | 380.31 / 439.60 | 142.41 |

5个纯工程client案例的完整进程wall为114.85ms，总RSS约37.17MiB。
最终wheel 2,281,118 bytes（约2.18MiB），比0.2.1多6,614 bytes，约0.29%。
内存对象展开和调用粒度比压缩包大小更值得关注；不将710与716MiB间的运行波动解释为新优化。

最终220运行 `full4/live-geyaqkmk`：[rpc-performance.json](../../evidence/upgrade-030/final-live/rpc-performance.json)。同条件每轮20次运算、11轮，median/p95 ms：

| 场景 | 串行20 RPC | 批量1 RPC |
|---|---:|---:|
| lite | 16.026 / 60.890 | 0.675 / 1.693 |
| Fusion | 17.619 / 18.966 | 0.753 / 1.723 |
| Fusion+evidence | 28.369 / 58.107 | 1.743 / 1.779 |

首次generic包含注册表为558.119ms。与第三轮相比，记录证据的串行路径明显波动，不能保证“日志无成本”；批量减少往返的方向保持一致。最终结果也不支持“Fusion比lite快”，不外推复杂OA编辑或求解吞吐。全部慢样本均保留。
