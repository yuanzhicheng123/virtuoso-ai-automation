# Fusion 0.3.2：调用质量、缓存与解析优化验收

2026-11-24。基线为0.3.1 / `433e2f0`，固定上游lite仍为 `672b451`。本报告区分最终build4、已被替代的候选版本及未结束的验证；所有中间失败和慢样本保留。

**最终build4的源码回归、安装包回归、220组合真机与签名缓存恢复通过；真实SSH默认容量30分钟/3600请求/0错误，代码与进程清理复核通过。SSH尾延迟不满足“始终低于100ms”的承诺，本报告没有作此承诺。** 本轮落实输入契约、会话缓存、MCP注册表共享基础及PEX逐行解析。长任务仅独立设计，AI模型适配未实现，也不宣称芯片全流程或全平台签核。

## 已落地行为

| 改动 | 解决的问题 | 明确边界 |
|---|---|---|
| MCP入口复用 `Draft202012Validator` | 按31个已公布inputSchema校验类型、必填项、未知字段、范围/长度；alias按canonical schema处理；嵌套非有限数值拒绝 | 每工具构建一次validator，入口校验一次；不自动强转类型或注入默认值。错误带字段路径与规则，不回显参数值 |
| `_session_callers` | 同client复用GenericSkill，不再每次MCP调用丢失探测缓存 | 读/写权限caller分开；共享probe集合不共享写授权；替换registry会失效重建；无全局可变会话单例 |
| 私有基础registry＋session overlay | MCP离线检索和generic调用不必展开两份完整快照；live签名留在所属会话 | 基础表由内部约定只读；get/search返回的数据不能污染基础或另一会话。显式访问session `.raw`仍会物化完整独立字典，这是兼容成本 |
| 失败后撤销探测缓存 | 避免“缺签名调用失败→后续加载函数→kwargs仍永远失败” | 参数编排异常、执行异常/失败撤销该函数的probe标记；只让用户下一次显式调用再探测，不自动重发原执行 |
| Cadence `unknown`哨兵 | 真机arglist对未定义函数返回裸 `unknown`，不能当作一个叫unknown的形参 | `nil`/裸`unknown`均不是有效签名；`(unknown)`仍可表示合法参数名；`auto_signature=False`保持允许返回None的纯编译回调 |
| PEX逐行检查 | 消除 `read_text().splitlines()` 同时持有整个寄生网表及行列表的内存峰值 | 保留include去重、缺失/空/外部文件判定和R/C计数；不是更换Calibre提取算法，也不是对所有结果文件做流式改造 |

注册表总条目仍为 **74,785**；有效签名从 **61,979调整为61,941**，排除38个原先误计的unknown记录。这是判定修正，不是删除功能。38个打包资源快照保持原内容，live更新只在内存会话层，未手工改生成数据。

## 最终验证与证据归属

| 验证 | 实际结果 | 证据 |
|---|---|---|
| 最终源码回归 | **223 passed、39 skipped**，另报告69个subtests通过 | [unit-release.log](../../evidence/optimization-032/unit-release.log) / [XML](../../evidence/optimization-032/unit-release.xml) |
| 独立安装包 | **198 passed**，另31个subtests通过 | [installed-release.log](../../evidence/optimization-032/installed-release.log) / [XML](../../evidence/optimization-032/installed-release.xml) |
| build4组合真机 | **36 passed、3 skipped，172.37s**，零失败/错误 | [build4-live.log](../../evidence/optimization-032/build4-live.log) / [result.json](../../evidence/optimization-032/build4-live/result.json) |
| 独立缓存真机 | **2 passed、零skip，0.99s** | [cache-final-live.log](../../evidence/optimization-032/cache-final-live.log) |
| 最终组合内缓存复验 | alias正签名只探测一次；unknown失败→加载procedure→kwargs重新探测成功 | [mcp-cache-report.json](../../evidence/optimization-032/build4-live/mcp-cache-report.json) |
| build4真实SSH≥30分钟 | **PASS：1800.000133秒、3600请求、0错误；默认容量** | [build4目录](../../evidence/optimization-032/endurance/build4/) |

源码中的39个skip主要是未显式启用的真机入口，不能当作通过。组合真机的3个skip分别是需要独立配置的CLI `status`、`capture_session`，以及由单独耐久控制器负责的hold测试；该hold在组合里跳过，不代表SSH耐久完成。31个schema合法性检查也不等于31个MCP工具全部完成真机验证。

最终组合记录确认自有Virtuoso、RAMIC daemon、Xvfb均退出。`mcp-surface-report.json`仅记录其覆盖的handler→EDA断言，剩余SKIP保持原样，不能把其状态表改成全绿。

物理链仍按原判据验收：本轮带四端口单管fixture的LVS为CORRECT，PEX产生27R/32C并进行前后仿比较；DRC执行1737条检查，产生 **52条结果，checks_passed=false**。软件测试通过包含“正确识别设计不通过”，不代表DRC clean或整芯片可流片。[最终连接电路报告](../../evidence/optimization-032/build4-live/connected-report.json)

## 缓存恢复：受控回归与真机证据分开

独立审查首先用受控executor重现nil负缓存阻断恢复，记录在 [review.md](../../evidence/optimization-032/review.md)。随后真机发现当前IC23.1未定义函数实际返回裸`unknown`，初次验收失败原样保留于 [cache-first-report.json](../../evidence/optimization-032/cache-first-report.json)。build4同时修正哨兵判断与失败后再探测。

最终真机序列为：创建plus alias，连续3次MCP调用结果正确且caller仅探测一次；调用未定义函数得到`arglist→unknown`及执行错误；在自有CIW加载procedure；使用kwargs显式再调用，取得真实`(a b)`签名并返回9，后续返回42且使用缓存。快照文件前后SHA256一致，基础表不含动态函数。

**真机报告明确 `negative_cache_verified=false`。** alias是正签名缓存；unknown加载恢复是真机验证。nil负缓存的成功复用、失败清除等分支仅有受控单元回归，不改称已在这台Cadence得到nil并完成验收。

## 最终build4的调用性能

[最终call-smoothness.json](../../evidence/optimization-032/build4-live/call-smoothness.json) 来自220、Python3.13.12、认证localhost TCP、自有GUI，只读算术。MCP绑定隔离client，不计默认profile发现或SSH启动；不代表大型OA、求解器或WAN吞吐。

| 稳态路径 | 次数 | median / p95 / p99 / max（ms） |
|---|---:|---:|
| Python raw | 500 | 0.770 / 1.747 / 2.041 / 39.908 |
| Python generic | 500 | 0.694 / 1.730 / 1.909 / 40.978 |
| Python generic＋evidence | 500 | 0.755 / 1.836 / 2.027 / 23.265 |
| MCP evaluate | 500 | 0.961 / 1.931 / 2.978 / 36.993 |
| MCP call_registered | 500 | 0.972 / 1.946 / 3.666 / 41.438 |

共 **2500次稳态调用零失败**，预设p95≤50ms、p99≤100ms等筛查通过，约40ms尾部样本保留。这是短时负载，不等于30分钟结果。Python预热前后测量RSS均190976KiB；MCP在搜索后、首调用后、再1000次调用后均183112KiB（178.82MiB），仅证明该负载未观察到继续增长。

0.3.1的MCP搜索后首次generic约643.612ms且RSS从搜索后的175.51MiB增至309.19MiB；最终build4该步骤为 **8.705ms**，RSS未再增长。首次MCP搜索仍需 **689.461ms**；独立Python首次generic **668.100ms**。这些都是单样本，不当作冷启动分布。共享基础表消除了MCP路径第二次完整装载，**没有消除第一次装载**，也不替所有普通Python client自动共享数据。

中间build3曾测该首调用2.46ms、RSS约178.7MiB；这只能作为候选观察，不能重标成build4结果。最终用8.705ms；不同轮调度/初始化差异不用于宣称固定加速倍数。参考旧基线：[0.3.1调用报告](CALL_SMOOTHNESS_031.md)。

1秒长evaluate之后的同通道ping最终仍等待 **1003.274ms**。本轮没有修复同步长任务阻塞；独立控制通道快响应也不证明同CIW并行编辑安全。

## 本地同脚本基准与成本

比较 [baseline-031-benchmark.json](../../evidence/optimization-032/baseline-031-benchmark.json) 与 [release-032-benchmark.json](../../evidence/optimization-032/release-032-benchmark.json)：同一脚本SHA256、i9-13900K、Python3.11.5、独立安装包及 `-I` 子进程，每项11次。文件缓存未清空、主机未独占；首次库import前有相同标准库基准前置加载。nearest-rank p95在11次样本中等于最大值，不形成生产SLO。

| 项目（median） | 0.3.1 | build4 0.3.2 |
|---|---:|---:|
| Fusion import | 76.439ms | 72.118ms |
| MCP import | 73.516ms | **87.763ms** |
| MCP导入后RSS | 38040KiB | **41492KiB** |
| 默认5 client构造 | 2.338ms | 2.325ms |
| 5独立client都实际加载registry | 2291.580ms / 716.39MiB增量 | 2241.386ms / 715.72MiB增量 |
| registry首次装载 | 383.015ms | 373.430ms |
| coverage完整统计 | 30.814ms | 22.517ms |
| search命中（上限20） | 6.608ms | 7.532ms |
| search未命中 | 6.351ms | 6.905ms |
| 单参数kwargs编排 | 1.774µs | 1.788µs |

不能只报道更快的项：search增加了隔离用深拷贝/迭代开销，最终均摊查询较基线慢；5份独立Python registry仍占约716MiB。session `.raw`显式物化也仍有大对象成本。MCP import实际增加 **14.247ms、3.371MiB RSS**；中间测量曾约+24ms，本报告采用最终同脚本数字。

Fusion自身安装文件不含pyc由2,561,838增至2,566,810bytes，新增4,972bytes；Python物理行3486→3620。更明显的部署代价是新增jsonschema及4个传递依赖，共 **1,963,826bytes（1.873MiB，不含pyc）**：

| 新依赖 | 实测版本 | 安装bytes，不含pyc |
|---|---|---:|
| jsonschema | 4.26.0 | 478946 |
| attrs | 26.1.0 | 234681 |
| jsonschema-specifications | 2025.9.1 | 47697 |
| referencing | 0.37.0 | 116229 |
| rpds-py | 2026.9.1 | 1086273 |

[依赖实测](../../evidence/optimization-032/validation-dependencies.json)。这些是当前环境版本，不是将所有依赖锁死的声明；rpds-py含平台二进制组件，不能宣传增加校验完全没有二进制部署成本。选择复用成熟schema实现，是为了完整执行已公开契约，避免手写子集校验器；validator复用将初始化成本留在服务装载阶段。

## PEX流式读取：主要收益是内存

64,000,000bytes合成寄生网表的真实文件解析，结果前后一致：峰RSS **266616→15776KiB，降低94.08%**；耗时1.4024→1.3573s。峰RSS来自独立进程，未将业务解析逻辑复制进测试。[原始汇总](../../evidence/optimization-032/physical-streaming-summary.json)

该单次合成样例支持“整文件/行列表的额外驻留已去除”，不支持“Calibre提取快了多少”、任意大型PEX恒定内存或大型RF后仿吞吐。include路径集合、计数和报告结果仍需要内存；真实提取语义由组合真机中的Calibre链另行验证。

## 默认nonce容量失败与隔离复验

默认认证nonce cache容量4096、nonce至少保留900s。在叠加2500次短时调用、inventory和其它组合检查后，实际发生 `nonce cache at capacity`，服务按fail-closed拒绝；原尝试 **1 failed、28 passed、3 skipped、5 errors**，保留于 [live.log](../../evidence/optimization-032/live.log)。不能把它隐去或归为网络偶发故障，更不能将403类认证拒绝当作可自动重发设计操作的理由。

组合复验仅在独立测试daemon中使用原已提供的 `RB_NONCE_CACHE_MAX=16384` 参数。没有关闭认证、缩短nonce保留、改写vendor认证或修改全局用户配置；最终build4的36通过基于该容量条件。该结果证明配置足够承载本轮组合burst，不证明默认4096能承载相同负载。默认容量仍是高频部署需考虑的限制，参数调整不是所有场景性能保证。[上游nonce回归记录](../../evidence/optimization-032/upstream-nonce.log)

SSH耐久则明确使用未覆盖的上游默认nonce配置；它以间隔负载测试不同的工作条件，不用更大容量掩盖默认配置表现。

## 远程耐久：最终build4结果

**build4状态：PASS。** 实际单调计时1800.000133秒，3600次plus/getVersion请求、0错误；六个300秒窗口各方法300样本。使用真实OpenSSH本地转发，远端仅监听127.0.0.1，未覆盖默认nonce容量。[最终报告](../../evidence/optimization-032/endurance/build4/ssh-endurance-7devlhtp/report.json)

中位延迟约42–44ms；getVersion窗口p99为102.75–211.88ms，plus为85.85–167.88ms，整体max260.70ms。所有慢样本保留，不能用build3较快尾延迟替代最终结果。没有预先声明SSH延迟SLO，PASS判据为时长、调用正确性和清理；它不表示p99≤100ms，更不表示实时控制能力。

本机RSS184912→185892KiB（控制器保存了样本），daemon21676→22652KiB，Virtuoso656220→672144KiB。增长是实际观测，不推断为泄漏，也不宣称长期无泄漏。双方运行前后18个生产Python文件SHA一致，结果未含token，自有Virtuoso/daemon/SSH隧道均经/proc核实退出。[后验核对](../../evidence/optimization-032/endurance/build4/post-validation.json)

已完成的build3：实际 **1800.000s、3600次plus/getVersion、零错误**，经OpenSSH本地转发到220，非直接远端TCP；前后生产源码相同、token未入证据、自有进程与tunnel已退出。但其后为真实unknown哨兵修复改变生产代码，所以明确为 **PASS_SUPERSEDED_CANDIDATE**，不能充当build4最终验收。[build3原报告](../../evidence/optimization-032/endurance/build3/ssh-endurance-hffy1oax/report.json) / [替代说明](../../evidence/optimization-032/endurance/build3/disposition.json) / [后验检查](../../evidence/optimization-032/endurance/build3/post-validation.json)

早期未完成/被中断的运行及controller日志也保留，不计入30分钟完成结果。SSH间隔只读算术/版本查询不代表OA写入、多用户并发、大波形传输、跨公网丢包恢复或长模拟器作业耐久；不能外推数小时/数天无泄漏。

## 对标成熟实现，落实到本库契约

- **jsonschema**官方建议同一schema验证多个实例时复用具体validator；本轮为31工具各建一次validator、入口复用，另用测试检查schema本身有效。[官方验证文档](https://python-jsonschema.readthedocs.io/en/stable/validate/)
- **MCP Python SDK/协议**把schema、结构化结果与错误作为工具契约，而非只列一个函数名。本轮落实已发布inputSchema及错误字段定位；保留当前固定协议引擎，没有宣称已经迁移官方SDK、支持所有transport或所有AI模型。[官方SDK](https://github.com/modelcontextprotocol/python-sdk) / [Tools规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- **HTTPX**强调client生命周期、配置共享与关闭。本轮借鉴“复用有明确所有权的会话”来保存probe及registry overlay。HTTPX自身支持HTTP连接池，**Fusion本轮没有实现TCP连接池**，仍使用现有Cadence传输；不能据类比宣称相同吞吐或相同跨平台成熟度。[官方Client文档](https://www.python-httpx.org/advanced/clients/)

这些对标支持明确设计决策，不代替实际Cadence环境验收。没有为追求某框架而重建网络栈。

## 仍存在的部署与功能边界

1. 仓库根目录同时保留旧 `virtuoso_bridge/`。实测从该根目录直接import会让旧源码遮蔽已安装依赖，出现找不到ExecutionStatus的ImportError；进入 `virtuoso-fusion/` 或在仓库之外使用可正常导入0.3.2。[失败记录](../../evidence/optimization-032/legacy-root-import.log) / [子目录成功](../../evidence/optimization-032/fusion-root-import.log) 本轮没有迁移或删掉旧工程，也没有把该失败改写成安装包本身不存在。
2. 新旧上游同为1.0.0导致pip普通升级可能保留旧依赖的问题仍需交付wheel/能力guard/已验证强制重装路径；本轮没有让版本标识问题自动消失。旧工程根目录的安装失败状态也未被本轮修复，不将新库通过包装为整个历史仓库全部通过。
3. 长任务仍同步阻塞同通道；submit/status/result只在 [LONG_TASK_DESIGN.md](LONG_TASK_DESIGN.md) 独立设计，未实现取消、队列、持久化任务服务或重启自动恢复。
4. AI模型兼容层未实现。本轮保证工具入口和调用状态更明确，不宣称任意模型的参数生成、决策质量或全流程自动设计已通过。
5. 本轮没有扩展全芯片工艺签核、EMIR/ESD/DFM、所有PDK与Cadence版本。已有DRC不通过、未验证工具/平台仍保留边界。

本报告当前可确认最终build4的回归与组合真机结果，并量化缓存/内存收益及新增依赖代价；**最终SSH持续调用正确性与清理已通过，尾延迟和未覆盖边界如实保留；发布与最终提交状态以STATE.md和交付manifest为准。**
