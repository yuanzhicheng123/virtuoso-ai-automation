# 0.3.3 错误诊断与签名刷新验收

2026-11-25。修复目标是让新模型知道在哪里失败、目标有没有执行、下一步怎么处理。实现限于 Fusion 自有代码；固定上游资源没有修改。

## 改动

1. 全部 32 个 MCP 工具的 Schema 入口错误增加 `fusion_error`：字段路径、规则、期望、实际类型、未知字段名和恢复动作，不回显参数值。
2. 通用调用按实际阶段报告错误，调用前的权限/参数错误不再误报 unknown。连接配置失败报告 `connection_setup`。执行器抛出异常不会仅按异常类型推断“未执行”；优先采用原始 receipt 的 not_started / completed，其他保持 unknown。
3. 原始结果和请求凭据保留，Python 异常类型和对象身份保持；不自动重放请求。错误详情也返回为 MCP 文本，适配只读 content 的客户端。
4. `ensure_signature(force=True)` / `refresh_signature` 只刷新一个函数，清除旧解析参数，保留其他元数据与会话隔离；失败后不沿用旧签名。MCP kwargs 与 Python fn/self/timeout 控制参数分离。

用法与准确边界见 [ERROR_CONTRACT.md](../ERROR_CONTRACT.md)。没有增加运行时依赖、全局缓存 TTL、重试框架或异步作业框架。新增生产诊断模块为 55 行；wheel 增量 2997 字节（约 0.13%）。

## 验证

| 范围 | 结果 |
|---|---|
| 本地 Python 3.11 全套 | 257 passed，41 live skipped，69 subtests passed |
| 仓库外新环境，安装发行 wheel | 232 passed；不使用源码路径，源码工具/分发专用 25 项已在全套验证 |
| 220 Python 3.13 安装包定向错误/刷新 | 34 passed |
| 220 安装包、隔离认证 Virtuoso、默认 nonce 容量 | 5 passed，0 skipped |
| 本地和 220 安装载荷 | 56 文件与 wheel 一致，38 固定资源哈希正确；pip check 通过 |
| 独立复审 | 首轮 3 项具体诊断问题修复后复审通过；新增测试先失败后通过 |

真机验证包含原 alias 缓存、函数加载恢复、新增重定义后强制刷新、5 类调用前错误、2500 次稳态调用。所有自有 Virtuoso/RAMIC/Xvfb 进程退出。真实测试目录：`/home/lihong/virtuoso-fusion-20261123/signoff/errors-033/live/live-wh6w40k8`。

稳态五组 p95 为 1.36–1.89 ms，p99 最高 6.68 ms，最慢样本约 42.21 ms，2500 次零失败；Python 和 MCP 稳态 RSS 无增长。首次 generic 约 839 ms；MCP 检索后首次 registered 约 9.44 ms。同通道 1 秒任务仍使后续 ping 等待约 1004 ms，未解决同步阻塞。

首次仓库外测试收集因复制测试时遗漏 CSV fixtures 失败，保留原始失败日志；补齐已有 fixtures 后 232 项全部通过。没有通过跳过业务测试隐藏失败。

逐测试和测量结果：[errors-033.json](errors-033.json)。原始日志位于本地 `evidence/errors-033/` 和 220 `signoff/errors-033/`。源码父提交为 `e35e292f548bdaf0b24e3d74707d970ba9e037be`；发行包哈希、源代码提交以版本化交付目录 `RELEASE.json` 为准。

## 验收边界

本轮覆盖短时 localhost 真机通道及错误恢复；没有重跑 1800 秒 SSH 或全套 PDK/物理签核，也未重新验证 Python 3.10。0.3.2 长时 SSH 结果仅作为历史证据。上游工程工具内部错误仍沿用原格式，不能宣称全部 API 错误已统一。DRC52、未闭合 ERC、长任务异步与模型适配设计等历史缺口仍在。
