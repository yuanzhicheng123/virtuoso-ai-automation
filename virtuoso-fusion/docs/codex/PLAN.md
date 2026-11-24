# Virtuoso Fusion 0.1.0 — 融合实施与验收

目标：新distribution virtuoso-fusion / import virtuoso_fusion，在同一VirtuosoClient上组合上游工程域与本地注册表驱动调用。保留现有0.8.1环境，不混装冲突包。

来源：Arcadia-1/virtuoso-bridge-lite，commit 3f0bd7d46ca3c5d2c750cea0894bbdefc7b67b9d（MIT）；本地virtuoso_bridge 0.8.1已验证的registry、参数编排、写允许清单和证据惯例。

接口契约：
```python
from virtuoso_fusion import FusionClient, ApiRegistry, Symbol, SpectreSimulator
with FusionClient(host='127.0.0.1', port=12345, daemon_token=token) as client:
    result = client.call('plus', 1, 2, 3)  # upstream VirtuosoResult, .ok/.output
    client.schematic.read('LIB', 'CELL')
    client.maestro.get_session_state()
```
- FusionClient继承上游VirtuosoClient，所有原有工程域绑定同一个实例。
- execute_skill默认禁止传输重发，单客户端用RLock串行；保留status/errors/warnings/metadata/request receipt。已有upstream服务与认证机制继续使用，不启动第二通信内核。
- ApiRegistry默认读取包内只读完整快照，增加离线search/coverage/availability。
- GenericSkill(executor, registry=None, allow_write=False)持有一个execute_skill回调；call返回原始VirtuosoResult。位置参数完整保留，仅明确签名允许Python关键字编排；Symbol显式编码符号。无法表达对象句柄时使用工程域API或显式execute_skill，不猜对象/语法。
- 写策略只适用于通用函数调用，不能被误称为高层API的全局权限沙箱。原始SKILL及工程域遵守上游行为与调用者授权。
- evidence_dir可选；记录请求hash、result与序号，明确失败/未知状态，默认不写包目录。
- 独立CLI提供registry/coverage/call；上游完整CLI原样可用。
- MCP复用上游现有stdio server及工具，绑定FusionClient，增加registry检索/覆盖/通用调用三工具；保留原工具与skill资源。
- 固定upstream依赖到commit；交付upstream wheel + fusion wheel +源码包+来源清单；不发布外部仓库或PyPI。

验收：
- [x] 先写失败测试，再实现registry/argument/serialization/client/evidence融合。
- [x] Python包安装、CLI与MCP真实子进程协议测试；仓库外运行。
- [x] 上游相关原有测试与新库测试通过；结果分开统计。
- [x] 220独立工作区、独立认证RAMIC daemon；同客户端通用调用与工程域OA操作交叉验证。
- [x] Spectre独立实例跑OP/DC/Tran，检查实际激励与新输出；GUI/Maestro能力按实际实测范围标记。
- [x] 保存来源/许可证/数据hash，独立审查，生成完整STATE和功能/验证矩阵。

所有修改限新库与专用工作区，不修改生产OA、PDK、现有全局daemon配置。
