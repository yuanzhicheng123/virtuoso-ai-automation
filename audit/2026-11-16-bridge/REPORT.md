# 三项落地：写白名单 / GUI 标记 / Gateway 接入（2026-11-16）

## 一、写操作 allowlist（P0）
`virtuoso_bridge/policy.py`
- `Policy.check(fn, allow_write)`：写函数默认 **`WRITE_NEEDS_EXPLICIT_ALLOW`**；
- `load_write_allowlist()`：从 `registry/engineering_operations.json` 读已认证写操作，
  并回退到 10 个已验证底层写函数（`dbCreateNet/dbCreateInstByMasterName/schCreateWire/…`）。
- 真机：`dbCreateNet` → **denied**；`plus/nth/getVersion` → 放行。

## 二、GUI-only 标记（P1）
`virtuoso_bridge/availability.py`
- `annotate_needs_window()`：按 `contexts` 打标 → **26,587 个 needs_window**；
- `availability(fn)` → `HEADLESS_OK / NEEDS_WINDOW / NO_SIGNATURE / NOT_IN_REGISTRY`；
- headless 统计：**headless_ok 40,319 / needs_window 26,587 / no_signature 7,879**。

## 三、Gateway 接入（P1）
`virtuoso_bridge/gateway_adapter.py` + `tools/skill_cli.py`
- `GatewaySession.call()`：策略 → GenericSkill → 结果，写操作在调用前拦截并落日志；
- CLI：`python3 tools/skill_cli.py --fn plus --args 1 2`、`--why dbCreateNet`；
- 真机（session `cli1`）：
  `plus→5`、`nth→20`、`getVersion→IC23.1-64b`、`dbCreateNet→DENIED`、`why→HEADLESS_OK`。

## 四、按需挖签名（替代"硬挖"）
`GenericSkill.ensure_signature(fn)`：
- 注册表缺签名时，**对当前会话调用一次 `arglist`** 并缓存回注册表；
- 单次约 0.3 ms，不需要新起 Virtuoso，也不受宿主机负载影响；
- 单测（fake skill）验证：live 取得 `(n_x n_y)` 并写回缓存。

## 五、测试
`tests/test_bridge_policy.py`：4 项通过（写拒绝 / 策略放行 / 可用性状态 / needs_window 统计）。

## 六、当前覆盖
total 77.2%（57,751）、public 63.8%、documented 99.1%、headless_ok 40,319。

## 七、下一步（不硬挖）
1. 调用时按需 `ensure_signature`（用到才挖，结果永久缓存）；
2. 只对 **public + 无 `needs_window`** 的缺口做小批量补齐（约 7,879 个 no_signature）；
3. 给 `mine.py` 增加 **skip list**（已知会挂起的函数），避免卡住整块。
