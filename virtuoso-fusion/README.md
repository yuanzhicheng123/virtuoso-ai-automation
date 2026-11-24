# Virtuoso Fusion 0.3.1

本目录为已验证的 Fusion 0.3 系列工程。AI Agent/模型兼容层目前为[设计草案](docs/codex/AGENT_COMPATIBILITY_DESIGN.md)，尚未实现。

在仓库根目录可用独立虚拟环境安装：`python -m pip install ./virtuoso-fusion`。依赖固定上游Git提交，需要网络；已有环境升级注意下文同版本依赖问题。

一个 Python 入口同时使用 Virtuoso 工程操作与注册表驱动的通用 SKILL 调用。

- 工程域复用 `Arcadia-1/virtuoso-bridge-lite` 固定提交 `672b451416b1ef3cc560db4196365075e94c8d4e`：原理图、符号、版图、库、Maestro、SOS、GUI/请求恢复、SSH，以及独立 Spectre。
- 通用层迁移本地 0.8.1 的 74,785 项符号快照、签名编排、写允许清单和只读资源约定。
- 二者使用同一个 `FusionClient` 和同一 RAMIC 服务；不启动第二个 SkillBridge 会话。
- 新包名 `virtuoso-fusion`，导入名 `virtuoso_fusion`。这是新 API，不是旧 `virtuoso_bridge 0.8.1` 的原位升级。

## 安装

Python 3.10+。使用新的虚拟环境，保留已有 0.8.1 环境：

```bash
python -m venv .venv
source .venv/bin/activate
(umask 022; python -m pip install /path/to/virtuoso_fusion-0.3.1-py3-none-any.whl)
python -m pip check
virtuoso-fusion coverage
```

普通安装会按包元数据从 GitHub 获取固定提交的上游依赖，需要 git 和网络。离线/受控部署可先安装交付的上游 wheel 和依赖，再 `pip install --no-deps` 安装 Fusion，最后执行 `pip check`。交付清单标识上游提交和每个 wheel 的 SHA-256，不能用任意同版本号的上游包替换。

**从 Fusion 0.2.x 升级时，不要只依赖 `pip install --upgrade` 或 `pip check`。** 上游不同提交都使用 `1.0.0` 版本号，已实测普通升级可能保留旧实现而依赖检查仍通过。推荐新环境；复用环境时，先核对交付 `RELEASE.json` 的 SHA-256，再执行：

```bash
python -m pip install --force-reinstall --no-deps /path/to/virtuoso_bridge-1.0.0-py3-none-any.whl
python -m pip install --no-deps /path/to/virtuoso_fusion-0.3.1-py3-none-any.whl
python -m pip check
python -c 'import virtuoso_fusion; print(virtuoso_fusion.__version__)'
```

上述上游 wheel 必须来自 **0.3.0 交付目录、提交 672b451**。新版本导入时检查所需 Maestro 接口，可以拦截已知旧实现，但接口存在不等于完整源码 hash 匹配。

本库依赖的上游包与旧库都叫 `virtuoso-bridge`。不要把它们安装在同一个旧环境中。新库尚未发布 PyPI。

## Python 使用

已有上游配置可直接使用：

```python
from virtuoso_fusion import FusionClient, Symbol

with FusionClient.from_env(timeout=10, evidence_dir="evidence") as client:
    result = client.call("plus", 1, 2, 3, 4)
    assert result.ok and result.output.strip() == "10"
    result = client.call("nth", index0=1, list=[10, 20, 30])
    assert result.ok and result.output.strip() == "20"
    print(client.registry.search("dbOpenCellView", limit=5))

    # 工程操作与通用函数调用使用相同客户端/会话。
    schematic = client.schematic.read("myLib", "myCell")
    snapshot = client.maestro.snapshot()
```

直接连接已经启动的本地认证 daemon：

```python
client = FusionClient(host="127.0.0.1", port=port, daemon_token=token,
                      timeout=10, evidence_dir="evidence")
```

token 应来自对应会话的私有配置文件，不要写入源码或报告。远程部署、实例发现及既有 profiles 使用上游原生配置和 `virtuoso-bridge` CLI。

公开工程域：`client.schematic`、`symbol`、`layout`、`library`、`maestro`、`sos`、`dialogs`、`requests`、`oa`。原有 `execute_skill`、`fetch`、文件传输和文档查询接口继承保留。`SpectreSimulator`、`SpectrePool` 从 `virtuoso_fusion` 导出并保持上游接口。

0.3.0 同时支持原有 Detail CSV 和 IC23 无 Nominal、每列一个 corner 的导出，后者用 `point_semantics="corner_column"`，保留 `corner_parameters` 与跨 test 的同名输出明细。`check_points`、`check_netlist_options` 与 `copy_point_netlist` 同步新上游；复制网表时仅排除 Cadence 可能留下的悬空诊断日志 `amps_evaluator.log`，其他缺失输入仍报错。

0.1.1 修复 Maestro Detail CSV 的 corner 列遗漏。`client.maestro.read_results(...)` 保留原返回字段，并增加 `detail_columns`、`corner_columns`、`detail_rows`；每行的 `corner_values` 保留各 corner 的原始字符串。`point_semantics="sweep_group"` 表示 CSV Point 是 sweep 分组，不是 RDB 的逐 corner 仿真编号。空 waveform 单元格不当成数值零；CSV 数值只有显示精度，精确值和完成状态须查 RDB/波形。

## 返回值、参数和写策略

`client.call` 与 `client.execute_skill` 都返回上游 `VirtuosoResult`，包含 `ok/status/output/errors/warnings/metadata`。与旧 0.8.1 不同，`call("plus",...)` 不直接返回整数。SKILL 执行失败保留原始结果；本地参数错误、策略拒绝使用 Python 异常。必须检查 `result.ok`，且执行成功不等于电气验收达标。

位置参数完整保留。Python kwargs 只对可确认的位置签名编排；复杂语法、联合参数与歧义签名要求位置调用。`self`、`fn`、`timeout` 是 Python 调用层的保留名：`fn` 指定目标函数（仍支持 `client.call(fn="plus", op1=1, op2=2)`），`timeout` 指定执行预算。同名 SKILL 参数可按位置传递；签名无歧义时，也可使用已有前缀别名 `g_self`、`g_fn`、`g_timeout` 编排。前缀只用于参数名匹配，不改变值类型或增加对象句柄支持。签名探测和实际调用各自使用该预算，不承诺两者合计为一个总时限。

字符串始终作为数据编码；显式符号用 `Symbol("name")`。支持 None/bool/有限数值/字符串/list/tuple/Symbol。对象句柄与任意 SKILL 表达式不隐式猜测；使用对应工程域、`fetch` 或显式 `execute_skill`。

默认写拒绝只针对 `client.call` / `client.generic` 中已识别的写函数，放行须同时 `allow_write=True` 且位于已知允许清单。原始 SKILL 和工程域保留上游权限与调用者授权语义；这个策略不是全局安全沙箱。启用方式：

```python
with FusionClient.from_env(allow_write=True) as client:
    result = client.call("ddCreateLib", "newLib", "/isolated/path/newLib")
```

## 请求与证据

Fusion 的 `execute_skill` 禁止连接故障后重发请求，即使调用方传 `retry_connect=True` 也不启用。上游明确拒绝且确认未执行的 busy 响应仍遵循其等待机制。单个客户端的 `execute_skill` 串行执行；不宣称整个多步设计事务或多个客户端之间原子并发。

超时、未知执行结果与恢复句柄原样保留，按上游 receipt/history 机制核对后再决定，不盲重发。`close()` 幂等；关闭后请建立新客户端。

`evidence_dir` 可选，给通过 `execute_skill` 的请求保存代码、哈希、序号和完整结果JSONL。取回异步receipt、独立Spectre等路径沿用上游自身证据，不会自动全部汇总到这个日志。日志写入失败会在已执行结果中增加warning和`fusion_evidence_error`，不会伪装成尚未执行的传输失败。

## CLI 和 MCP

```bash
virtuoso-fusion registry dbOpenCellView --limit 5
virtuoso-fusion coverage
virtuoso-fusion call plus --args '[1, 2, 3, 4]'
virtuoso-fusion upstream --help
virtuoso-fusion-mcp
```

CLI 的准确选项以 `--help` 为准。MCP保留固定上游的工具和skill资源，再增加 `registry_search`、`registry_coverage`、`call_registered`、`discover_tools` 和 `run_physical`，当前共31个工具。物理验证在同一 stdio 连接中同步执行；超时按阶段计算，取消通知不会终止外部进程。新增通用调用的写许可按次显式设置，不修改缓存客户端的全局权限。stdio输出只用于协议消息。

## 验证与来源

```bash
python -m pytest tests
python tools/prepare_resources.py --check
```

完整注册表和MCP/skill资源的来源hash记录于包内 `_data/provenance.json`；复制资源由脚本生成，不手工修改。上游MIT许可与作者声明完整保留，本目录上传GitHub不新增原代码或注册表的许可授权；第三方材料保留原许可，不因打包而重新授予Cadence文档/PDK内容的权利。

`docs/codex/STATE.md` 是最终验收状态。签名覆盖率不是功能通过率。继承的Maestro、GUI、版图、GDS、SOS等接口不等于已在所有PDK上验收；新库测试、上游测试和220真机测试分别报告。

220 上已执行 OA→原生网表→DC/AC、Maestro sweep/corner、版图/PCell/GDS、N65 PVT 与 RF 分析探针。0.3.0 新增四端口连接电路的 OA→LVS CORRECT→27R/32C 提取→原生前仿/寄生后仿闭环；仍有52个DRC结果和未闭合的ERC，不能称为芯片签核。完整范围见 [验收状态](docs/codex/STATE.md)、[连接与后仿证据](docs/codex/CONNECTED_POSTLAYOUT.md)。

0.3.0 对大注册表按需加载，纯工程 API 无需承担其约142MiB的展开成本；第一次使用 generic/registry 仍需装载，各独立实例仍可重复占用内存。创建客户端耗时与真实 RPC/EDA 求解时间分别测量，详见 [性能和模拟/RF自动化架构审查](docs/codex/ARCHITECTURE_PERFORMANCE_REVIEW.md)。
0.2.0 新增正式 [OA/GDS 与物理验证 API](docs/PHYSICAL_API.md)：`client.oa`、`PhysicalVerification`、`discover_tools` 和 `virtuoso-fusion physical`。DRC/LVS报告判定、PEX三阶段提取、流程/解析器/层映射扩展均实际接线；工具退出码与设计验收结果分开返回。

0.2.1 修复 oa2strm 时间戳 WARNING/ERROR 的结构化捕获，并让真机验收脚本读取 JUnit：全部跳过不能记为通过。全库验证见 [当前状态](docs/codex/STATE.md) 和 [上游与公开接口审计](docs/codex/FULL_SURFACE_AUDIT.md)。固定上游仍有 bsd-csh 输出污染及 group-writable legacy 部署迁移限制。新安装与新部署使用进程级 `umask 022` 已验证；这不会修复已有0664部署，也不应直接放松其权限检查。220显式认证入口验证通过，跨平台/配置组合不承诺全覆盖。

0.3.1 修复 MCP `read_waveform` 的无效PSF误报成功及无时间轴时的伪采样。`at` 仅表示秒，不表示频率；AC复数幅相统计仍未由这个MCP入口完整提供。逐项验证记录见 [GitHub库验证报告](docs/codex/GITHUB_VALIDATION_20261124.md)。
