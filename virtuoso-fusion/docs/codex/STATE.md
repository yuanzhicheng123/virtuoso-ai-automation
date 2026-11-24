# Virtuoso Fusion 0.3.0 验收状态

日期：2026-11-24。项目验收唯一权威；0.2.1原记录保留在 [STATE_021.md](STATE_021.md)。目标为“用AI自动化模拟/RF设计”，不扩展数字SoC实现。

**裁决：放行已验证的220软件工作流。** 最终实现通过离线、安装包和真机回归；完整芯片签核、全部Virtuoso功能、全平台验证不放行。52个DRC结果与未闭合ERC是真实设计缺口。

## 版本与交付

- Fusion 0.3.0；固定lite提交 `672b451416b1ef3cc560db4196365075e94c8d4e`，资源由源头重新生成，共38项hash。
- 本地源码 `/home/lihong/Demo/virtuoso_bridge_end/virtuoso-fusion`；交付 `/home/lihong/Demo/virtuoso_bridge_end/fusion-upgrade-release`。准确源码commit、wheel/sdist哈希在交付 `RELEASE.json`。
- 220工作区 `/home/lihong/virtuoso-fusion-20261123`；新版独立环境 `venv-030`，旧 `venv` 保留0.2.1。发行物按 `dist/releases/0.3.0`、`0.2.1` 分开，避免同名上游1.0.0 wheel混淆。原0.3.0发行物为内部交付；本次源码上传GitHub，未发布PyPI。

## 本轮完成

1. 同步新上游的Maestro检查/日志/网表接口和GUI fallback资源；CSV兼容旧sweep-group与新IC23 corner-column。跨test同名输出保留在detail_rows；原points.outputs仍有上游覆盖语义。新格式来自上游真实观察的回归样本，不冒充220新捕获。
2. 修复真实point目录复制因悬空 `amps_evaluator.log` 失败的问题：只排除该诊断日志，其他缺失输入仍失败；本地与remote shell的真实文件/链接回归通过。
3. MCP新增工具发现与物理验证，现31工具。工具执行成功、DRC/LVS判定、PEX完成三者分开返回；PEX checks_passed仍为null。
4. 默认按需加载注册表，纯工程API避免约142MiB首载。未引入全局可变registry共享或新运行时框架。
5. 完成四命名端口OA→独立LVS→非零RC→原生前仿/提取后仿闭环，保留失败尝试、规则、告警与预先固定判据。
6. 发现并拦截普通pip升级保留旧1.0.0实现的问题；强制重装准确交付wheel的恢复路径实际通过。能力检查不能代替commit/hash验证。

## 验证证据

证据目录 `evidence/upgrade-030/`，离线原始大文件不纳入源码git。

| 范围 | 结果 | 证据 |
|---|---|---|
| Fusion全套 | **147 passed，24 live skipped**，69 subtests | unit-final.log/xml |
| 仓库外已安装wheel | **136 passed**，31 subtests；不通过源码导入 | installed-tests.log/xml、installed-payload.json |
| 新固定上游完整套件 | **2930 passed，126 skipped，1 failed，0 errors** | upstream-full.log/xml、upstream-full-summary.json |
| 新上游定向IC23/GUI等 | **77 passed** | upstream-targeted.log |
| 最终实现220组合真机 | **24 passed，0 skipped，116.86s** | live-full4.log、final-live/result.json/live.xml |
| MCP实际stdio+Calibre | 31工具；DRC返回CHECKS_FAILED；PEX完成27R/32C、checks_passed=null | mcp-probe-results.json、mcp-live.log |
| 安装/分发 | pip check通过；38资源、53包内文件与wheel一致；sdist重建wheel全部60成员字节一致 | installed-payload.json、remote-payload.log、sdist-rebuild.json |
| 普通升级误留旧依赖→恢复 | 复现旧reader且pip check通过；新guard拒绝旧依赖；指定新wheel强制重装后通过 | normal-upgrade-result.json、upgrade-recovery-result.json |
| 性能 | 同主机同脚本、每项11独立进程；实际220算术RPC另测 | ../performance-030/final-0.3.0.json、final-live/rpc-performance.json |
| 定点独立复查 | 复制/兼容/CSV共29通过，无阻断；MCP失败返回已另审 | final-review.md |

最终真机目录 `signoff/upgrade-030/full4/live-geyaqkmk`。runner确认其拥有的Virtuoso、RAMIC、Xvfb均退出。真机使用已安装0.3 wheel；后续只更新README/许可元数据并重建包，53项运行载荷与真机实现逐字节核对。未以元数据重装冒充另一次EDA运行。

前几轮保持原始结果：full1为22通过2失败（共享JSONL路径与point日志数量断言问题）；full2为23通过1失败（真实悬空诊断日志）；full3为24通过（含复制修复）；full4为24通过（再含导入兼容检查）。安装包测试首次使用 `/dev/null` 配置触发pytest扫描 `/tmp` 中无权限目录；加测试临时目录自己的空pytest.ini后136通过，未修改宿主权限或跳过产品失败。

上游唯一失败仍为本机bsd-csh selector将banner污染stdout，既有问题没有消失。126跳过包含95 opt-in live、14 Python2、16 Win/macOS、1宿主条件；本库24个真机测试不替代它们。旧0.2.1发现的0664资源legacy→auto迁移条件限制本轮未宣布修复，详见历史状态。

## 真机闭环与签核边界

- N65单个600nm/60nm nch，D/G/S/B四命名端口；实际OA原理图CDF/check/save/native netlist和实际OA版图/GDS。
- LVS对独立声明的参考CDL为CORRECT；三阶段xRC产出 **27R、32C**，值为正；后仿直接包含原提取网表。
- TT/27°C、VDD1.2V、VG0.6V、1kΩ负载：DC前后差 **2.76087%**；1kHz输出幅度差 **3.37413%**；100GHz后/前幅度 **0.187084**。100GHz仅寄生敏感性探针，20%窗口仅fixture功能判据，不是产品规格。
- DRC运行1737 checks、**52 results**；供电识别相关ERC PATHCHK未闭合。指定Spectre弃用/全局端口名告警保留并精确分类，其他coded warning会失败。
- 同轮也覆盖认证/错误token、generic恢复/串行、Maestro4个编号点+共享日志、实际点网表复制、符号/库、OA/PCell/GDS两出口、物理API、文件传输、MCP、超时receipt及双Spectre作业。
- 上轮0.2.1的RF（AC/noise/SP/PSS/PNoise/PAC/HB）、27点PVT与3工艺MC样本为历史证据，本轮未重新跑独立RF/PVT脚本；不能写为0.3全部RF重签核。

完整连接证据：[CONNECTED_POSTLAYOUT.md](CONNECTED_POSTLAYOUT.md)。

## 主要弊端与后续顺序

详见 [ARCHITECTURE_PERFORMANCE_REVIEW.md](ARCHITECTURE_PERFORMANCE_REVIEW.md)。

1. **设计判断层不足**：函数可调用不代表输入单位/PDK合法域、拓扑/config、测试台、测量区间/收敛判据正确。下一闭环应选一个有规格的真实多器件模拟/RF模块，统一约束与独立测量验收。
2. **物理质量未闭合**：匹配/对称/屏蔽/隔离意图、路由、DRC/ERC、寄生角、多corner后仿、失配与良率仍是硬缺口；不能自动授权tapeout。EMIR/ESD/DFM/封装不在本轮已证范围。
3. **长任务编排不足**：MCP物理调用同步阻塞同连接；timeout按阶段，取消通知不杀进程；SKILL receipt不能覆盖所有仿真/验证进程。没有统一持久化job/status/cancel与跨阶段恢复契约。先以真实长任务需求收敛最小实现。
4. **数据与部署成本**：5个纯工程client构造2.30ms、增量0.34MiB；5份实际generic注册表仍约2.23s/710MiB。首次registry约380ms/142MiB。PSF/PEX/完整JSON输出不可默认无限回传AI；同版本上游依赖必须由提交和hash锁定。

Python继续承担编排/适配/AI接口；SKILL/OA负责库内数据库操作，Spectre/Calibre负责求解验证。先复用client、批量有限操作、缩小结果回传，再按profile决定局部原生加速。没有证据支持整库改写C++/Rust或承诺无GIL自动加速。

GUI fallback代码/资源已升级，220缺wmctrl/xdotool，原生点击/输入未验收；SOS真实服务器未提供；全部74785注册函数逐个调用、全部Cadence版本/PDK/license/参数组合、多用户/跨平台矩阵未验证。名称覆盖率不是成功调用率。

## 复现

```sh
python -m pytest tests
python tools/prepare_resources.py --check
# 220工作区，使用新环境；不含旧版独立RF/PVT套件
VF_RUNTIME_INVENTORY=1 VF_INVENTORY_VIEWS=1 VF_LIVE_PHYSICAL=1 VF_LIVE_WORKBENCH=1 \
VF_LIVE_CONNECTED=1 VF_LIVE_BENCHMARK=1 \
VF_LIVE_MODEL=/eda/1P9M/TSMC065_1P9M/models/spectre/crn65gplus_2d5_lk_v1d0.scs \
venv-030/bin/python source/tools/run_live.py --root signoff/upgrade-030/reproduce \
  --cds-lib cds.lib --gui --tests source/tests/live --test-timeout 1200
```

原始OA/PSF/RDB/Calibre结果留220；摘要与hash留本地evidence；PDK规则、认证材料不进入交付包。

## GitHub源码交付说明

当前源码存放在 `yuanzhicheng123/virtuoso-ai-automation` 的 `virtuoso-fusion/`，保留仓库根部旧工程。原测试证据目录留在本地/220，GitHub上的相关本地证据链接不保证可访问。AI Agent兼容层设计作为待实现草案随代码提交，不计为已验证功能。本次上传没有重新运行EDA，真机结果对应上文0.3.0验收。
