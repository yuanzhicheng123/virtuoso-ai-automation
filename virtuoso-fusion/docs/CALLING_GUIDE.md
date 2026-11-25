> 0.3.3 新增 [错误诊断与签名刷新](ERROR_CONTRACT.md)：先定位失败阶段，再决定恢复操作。

> 本文保留0.3.1测量背景；0.3.2已消除正常MCP冷客户端路径的双注册表装载，见[新版报告](codex/OPTIMIZATION_032_REPORT.md)。已有热registry或显式raw完整读取仍承担自己的副本成本。

# 流畅调用约定

适用于Fusion 0.3.1。这里区分启动成本、短调用延迟和EDA任务运行时间；实际数据见 [调用性能实测](codex/CALL_SMOOTHNESS_031.md)。

## Python生命周期

完成既有连接配置后，在一个任务/会话内复用同一个client。不要为每个参数、每个器件属性新建一套客户端和注册表。

```python
from virtuoso_fusion import FusionClient

with FusionClient.from_env(timeout=30, log_to_ciw=False) as client:
    ready = client.execute_skill('plus(1 2)')
    if not ready.ok:
        raise RuntimeError(ready.errors)

    # 仅当后续确实使用generic调用时，在启动阶段承担注册表首载成本。
    warm = client.call('plus', 1, 2)
    if not warm.ok:
        raise RuntimeError(warm.errors)

    result = client.execute_skill('list(getVersion() length(ddGetLibList()))')
    if not result.ok:
        raise RuntimeError(result.errors)
    print(result.output)
```

只使用schematic/layout/maestro等工程API的任务可以省略generic预热，让注册表保持未加载。`from_env`的profile发现、SSH与认证耗时属于启动阶段，不应与预热后的单次调用混在一起。

`log_to_ciw=False`减少GUI控制台输出；本轮没有证明开关CIW日志能稳定改变延迟。需要审计时使用 `evidence_dir`，不要为了速度丢弃执行结果或失败证据。

## 调用粒度

把有明确边界的一组读取或编辑放入已有批量工程API/一段SKILL脚本；避免每个属性一次往返。为每批保留检查结果和失败位置。一个多步脚本不自动具有事务或回滚保证，同一CIW也不因为Python开了线程就能并行修改数据库。

模型工具描述按任务提供必要子集；74785条注册表通过检索访问。不要每轮重新启动MCP进程、重读完整注册表或回传整份PSF/PEX。

## MCP冷启动和长任务

保持MCP进程常驻。`evaluate`和工程工具不会因为存在registry工具就自动加载完整注册表；只用它们时无需预热registry。

如果Agent既使用 `registry_search` 又使用 `call_registered`，两者当前有独立registry实例；启动阶段可分别进行一次必要的只读检索与 `plus` 调用，后续复用同一进程。这样把已知首载等待放到准备阶段，**不减少总内存或总首载成本**。本轮实测约309MiB进程RSS，连续调用后稳定；没有新增全局共享可变注册表。

同一MCP stdio连接同步处理请求，长工具调用会阻塞排在后面的ping/短调用。已建立的独立控制连接可以及时响应纯协议ping；这不代表另一条连接能并行修改同一Virtuoso数据库，也不等于已有持久化异步job/cancel API。任务界面应明确区分连接可响应与EDA任务完成；不要把正常长仿真误判为连接断开后自动重发。

## 验证约定

`tests/live/test_call_smoothness.py` 为显式opt-in测试，预先记录筛查目标：稳态p95≤50ms、p99≤100ms、零调用失败、Python预热后RSS增量≤10MiB。达到这些条件只通过本机轻量调用筛查，不建立WAN或生产SLO。

每条稳态路径500次，保留全部样本、median/p95/p99/max和内存前后值；冷启动单列，不混入稳态分布。性能超标会使测试失败并保留报告。测试同时记录1秒任务阻塞同通道ping的实际时间，这一限制不会被普通调用通过掩盖。

```bash
# 在220既有隔离工作区运行；runner负责创建并清理自有会话
VF_LIVE_SMOOTHNESS=1 venv-031/bin/python source-github-031/tools/run_live.py \
  --root signoff/smoothness-reproduce --cds-lib cds.lib --gui \
  --tests smoothness-validation/test_call_smoothness.py --test-timeout 300
```

本轮没有改变生产代码或接口，不声称解决了长任务调度、两个registry副本或全量RF数据回传问题。


## 高密度请求的认证容量

固定上游默认 `RB_NONCE_CACHE_MAX=4096`，每条nonce至少保留900秒（更长请求预算可增加保留期）。本轮在同一会话叠加功能普查和2500次短调用后实际触发容量拒绝；这不是凭证失效，也不能用关闭认证解决。

本轮隔离组合压力验收在daemon启动环境显式使用 `RB_NONCE_CACHE_MAX=16384`，重放标记保留期与拒绝重放逻辑保持原样。该值必须在daemon启动时生效；只修改客户端环境不会改变已运行daemon。容量应按认证消息速率、保留期和突发量配置，业务调用不一定与认证消息一一对应；不承诺16384适用于任意负载。30分钟SSH间隔负载使用默认容量另验，不能混写。

MCP参数现在会在执行前校验。调用者应处理明确的参数错误、执行错误和未知执行结果；不要把所有错误都作为网络故障自动重发。
