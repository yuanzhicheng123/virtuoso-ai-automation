# virtuoso_bridge 收尾：自动补签名 + skip list（2026-11-17）

## 一、最终设计（不再硬挖）
| 机制 | 行为 |
|---|---|
| `GenericSkill.auto_signature=True`（默认） | 调用前若注册表无签名，就地调一次 `arglist` 并**永久缓存** |
| `skip list`（`registry/arglist_skip.json`） | `arglist` 返回 nil/抛错的函数写入 skip，后续不重试 |
| 无签名仍可调用 | 按位置传参照常工作（向后兼容） |

## 二、测试
`tests/test_bridge_policy.py` 6 项全过：策略拒绝/放行、可用性状态、needs_window 统计、
自动补签名缓存、失败写 skip list；全套测试 全绿。

## 三、真机
- `getpid`（SKILL 中不存在）→ 真实报错如实抛出，未吞异常；
- `plus/nth/getVersion` 经 GatewaySession 正常（前轮证据）；
- `--why schCreateWire` → **NEEDS_WINDOW**（正确识别需要示意图窗口）。

## 四、覆盖（最终）
| 桶 | 覆盖率 |
|---|---|
| 全部 | **82.9%**（61,979 / 74,785） |
| public | **84.0%**（17,604 / 20,954） |
| documented | 99.1% |
| headless 可用 | 40,319 |
| needs_window | 26,587 |
| 抽样实调用成功率 | 97.5%（39/40） |

## 五、剩余
1,128 个 public 缺口 → `arglist` 不可得（实测 0 行/47 行），已交给 skip list + 按需机制。

## 六、日常入口
```
python3 tools/skill_cli.py --fn plus --args 1 2
python3 tools/skill_cli.py --why schCreateWire
```
