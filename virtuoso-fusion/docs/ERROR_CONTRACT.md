# 调用错误与恢复（0.3.3）

MCP 错误先读 `structuredContent.fusion_error`，再读原始 `errors`、`metadata` 和 receipt。错误详情也作为第二段文本返回，兼容只读取 `content` 的模型。成功结果维持原结构；`refresh_signature` 另返回 `signature_available`。

| 字段 | 用途 |
|---|---|
| `code` | 稳定的错误分类，供 agent 分支处理 |
| `stage` | `connection_setup`、`validation`、`policy`、`signature_probe`、`argument_binding`、`encoding`、`execution` |
| `tool` / `function` | MCP 工具和目标函数；Schema 拒绝尚未识别目标函数时不返回 `function` |
| `path` | 出错字段；绑定错误的具体参数名和可用参数见 `message` |
| `target_execution` | `not_started`：未调用目标执行器；`completed`：原凭据确认请求完成（不代表成功）；`unknown`：无法判断是否完成或产生副作用 |
| `message` / `next_action` | 原因和可执行的恢复建议 |
| `constraint` / `expected` / `actual_type` | Schema 校验失败的规则、期望值、实际类型；不回显参数值 |
| `unexpected_fields` | Schema 拒绝的多余字段名（如拼错的 `limt`） |

目前统一阶段诊断适用于 `call_registered`、`refresh_signature`，Schema 诊断适用于全部 32 个 MCP 工具。其他上游工具的 handler 内部错误沿用上游格式，尚未统一阶段。Python `GenericSkill.call`、`ensure_signature` 仍抛出原异常类型和对象，新增 `exception.fusion_error` 字典；返回的 `VirtuosoResult` 不改变。

## 各阶段怎么处理

- `CLIENT_SETUP_FAILED`：检查连接配置和 profile；缺少 `VB_REMOTE_HOST` 等配置时目标未执行。
- `INVALID_ARGUMENTS` / `INVALID_FUNCTION_NAME`：修正字段、类型或函数标识符。目标未执行。
- `WRITE_NEEDS_EXPLICIT_ALLOW`：仅在该写操作已获授权时设 `allow_write=true`。`WRITE_NOT_ALLOWLISTED` 无法通过该开关放行，需检查策略或选择已支持的工程接口。
- `ARGUMENT_BINDING_FAILED`：检查 `message` 中的缺失、重复、未知参数和可用参数。函数刚重定义或加载过时，显式刷新签名；也可使用位置参数。
- `VALUE_ENCODING_FAILED`：SKILL 值支持字符串、有限数字、布尔、null 和列表；Python 额外支持 `Symbol`。JSON 对象无法自动转换成 SKILL 对象。
- `SIGNATURE_PROBE_FAILED`：目标函数未调用，失败的是 `arglist` 探测。探测自身的执行结果可能仍未知，原始请求凭据保持不变；检查连接、上下文和探测错误。
- `EXECUTION_FAILED`：已进入执行器，检查原始结果和 receipt；**禁止据此自动重放写操作**。即使抛出 `TypeError` 或 `PermissionError`，也不能当成调用前拒绝。

执行阶段优先采用原始 metadata 中 `request_sent=false` / `outcome=not_started` / `outcome=completed` 的明确证据；缺失证据时保持 unknown。

调用前拒绝的顶层 `outcome` 为 `not_started`。探测或实际执行失败保留上游原有字段；不要将探测 receipt 的 outcome 误认为目标函数 outcome。`fusion_error.target_execution` 专指请求的目标函数，不表示整个请求中完全没有只读探测。

## 显式刷新签名

Python：

```python
result = client.generic.ensure_signature("myFunction", force=True, timeout=10)
if result is not None and not result.ok:
    # 检查 result.errors 和 result.metadata 后决定恢复步骤。
    raise RuntimeError(result.errors)
```

MCP：

```json
{"name":"refresh_signature","arguments":{"name":"myFunction","timeout":10}}
```

这只清除当前 registry 中该函数的旧 `arglist_raw` / `params` / `zero_args` 等签名字段并读取新 `arglist`，不会执行目标函数、重放业务请求或修改离线资源。失败后旧签名不会继续使用；`nil` / `unknown` 返回 `signature_available=false`，关键字绑定仍不可用。默认 `ensure_signature()` 保持缓存行为；不会自动探测函数是否被其他代码重定义。

MCP 的 `kwargs` 数据与工具控制参数已分开，SKILL 的 `self`、`fn`、`timeout` 可以放在 `kwargs` 中。Python `call()` 保留原签名，仍需使用位置参数或已有的 `g_self` / `g_fn` / `g_timeout` 前缀别名。
