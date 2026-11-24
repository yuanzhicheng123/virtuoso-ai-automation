# 调用流畅度实测：Fusion 0.3.1

2026-11-24，生产代码基线63d0706。本轮仅新增性能验收与调用约定，不改生产实现。

**稳态短调用通过本轮筛查；首次注册表装载和同通道长任务仍会等待。** 最终2500次稳态调用零失败，Python与MCP预热后RSS未增长。测试总时长8.66s，是短时连续负载，不是数小时耐久性或生产SLO证明。

## 最终实测

220已安装0.3.1，Python3.13.12，认证localhost TCP、自有GUI会话。计时包含真实执行/往返；MCP还包含JSON序列化和stdio传输。使用只读plus运算，不代表大型OA/几何编辑或模拟器吞吐。MCP显式绑定隔离客户端，不包含from_env/profile/SSH启动；标准路径log_to_ciw=False。

| 路径 | 次数 | median ms | p95 ms | p99 ms | max ms |
|---|---:|---:|---:|---:|---:|
| Python execute_skill | 500 | 1.604 | 1.734 | 1.930 | 41.266 |
| Python generic | 500 | 1.621 | 1.740 | 1.952 | 41.982 |
| Python generic＋证据日志 | 500 | 1.730 | 1.816 | 2.114 | 42.001 |
| MCP evaluate | 500 | 1.740 | 1.888 | 2.319 | 40.963 |
| MCP call_registered | 500 | 0.783 | 1.715 | 2.063 | 41.377 |

p95/p99采用nearest rank，所有样本保留。偶发约41–42ms尾部峰值未删除，尚未通过抓包/profiling归因；不能仅凭延迟形状认定TCP或Python为根因。

预先写入报告的筛查目标为p95≤50ms、p99≤100ms、零调用失败、Python预热后RSS增量≤10MiB；最终均通过。测试代码最后断言筛查结果，超标不会只输出一份漂亮摘要后仍通过。

## 冷启动、内存与批量

- 首次Python generic：641.381ms；首次raw调用：1.104ms。这两个单样本不用于推导冷启动分布。
- MCP首次registry_search：654.499ms；随后首次call_registered：643.612ms。当前两条路径各有一份registry，合计约1.30s首载成本。
- MCP RSS从38.80MiB增至检索后的175.51MiB，再增至调用后的309.19MiB；接下来1000次调用仍为309.19MiB。预热把等待移到准备阶段，不能消除副本成本。
- Python预热后再执行1500次调用，RSS观察值均为188900KiB，增量0。它只证明这个短时负载未观察到增长，不能证明所有场景无泄漏。
- 一批20次plus、100批，median1.587ms/p951.771ms。支持减少细碎RPC；不外推仿真速度提升。
- 100次新建client后raw调用median2.045ms；这里只做raw，**没有**承担每次新建generic注册表的成本，不能据此推荐反复创建generic client。
- CIW日志开启的100次raw调用p951.744ms/p997.472ms；本轮与首轮波动不支持宣称关闭日志必然提速，关闭主要减少GUI输出量。
- 启动至首次MCP ping约211ms，包含主/控制两个worker启动；不是单worker纯启动基准。

## 长任务排队

同通道先发送 `evaluate: progn(sleep(1) 3)` 再发送ping，ping完成约1003.292ms。独立控制通道20次ping median0.061ms/p950.201ms。

这验证了当前stdio同步排队行为，也表明独立通道能响应纯协议请求；没有证明多个客户端能并行修改同一CIW，没有实现持久化异步作业/取消服务。长仿真正常运行时不能因为短请求被排队就盲目重发设计操作。

## 两轮与证据

首轮 `signoff/smoothness-031/live-ipbkuu3k`：2500稳态调用完成，耗时7.80s；p95约1.72–1.86ms，证据日志路径p99为4.687ms。原样保留，没有按最低延迟择优。

最终轮补充源码hash、版本和明确超标断言后重跑：`signoff/smoothness-031/final/live-ee_3cnqo`，1测试通过、零跳过、8.66s；runner确认自有Virtuoso/RAMIC/Xvfb均退出。上表来自这一轮。原始样本位于本地evidence/smoothness-031，公开摘要见 [call-smoothness-031.json](call-smoothness-031.json)。

使用方式见 [流畅调用约定](../CALLING_GUIDE.md)：长期复用client/MCP进程、仅对需要的generic路径预热、批量执行有界动作、区分短调用与长作业等待。实际SSH/WAN、默认profile发现、大型RF波形传输与长时间运行仍需独立测量。
