# virtuoso-bridge 修复 + 通用扩展（2026-11-14）

## 一、原状（真实检查）
- `virtuoso-bridge 0.7.0` 只剩 `dist-info`，**package 目录不存在** → `import virtuoso_bridge` 直接失败；
- `/tmp/virtuoso-bridge-lite/src/virtuoso_bridge` 为**空目录**（无源码）；
- 结论：该库处于**损坏且无源码**状态，本轮按"修复 + 扩展"重写为可安装包。

## 二、交付的包（repo 内 `virtuoso_bridge/`，版本 0.8.0）
| 模块 | 作用 |
|---|---|
| `skill.py` | `Skill(workspace_id)` 封装 skillbridge：`call/eval/close/上下文管理`，`connect()` 触发连接 |
| `registry.py` | `ApiRegistry`：加载 `api_registry_v2.json`；`params()` 自动解析 `arglist_raw`（含类型）；`coverage()`；`risk_of()` |
| `generic.py` | `GenericSkill`：**注册表驱动**参数编排（args/kwargs→位置）、symbol 自动转换、写操作默认拒绝、调用日志 |
| `mine.py` | `mine_arglists()` 生成分块 SKILL 脚本；`parse_arglists()` 解析 `name|arglist` |
| `pyproject.toml` | 可 `pip install -e .` |

## 三、真机验证（session `vb1`）
| 调用 | 结果 |
|---|---|
| `plus(2,3)` | ✅ 5 |
| `strlen("hello")` | ✅ 5 |
| `nth(1,[10,20])` | ✅ **20** |
| `ddGetLibList()` | ✅ 返回远程库列表 |
| `getVersion()` | ✅ `IC23.1-64b` |
| `dbCreateNet(...)` | ✅ **PermissionError：拒绝写操作**（allow_write=False 生效） |
| `sprintf(None,"%d",7)` | ⚠️ 参数编排报错 → 已修（显式 None 不再被当作"未提供"），本地单测通过 |

## 四、覆盖率
`ApiRegistry().coverage()` → `total=74,785，with_signature=31,839，coverage=42.57%`
（含 2026-11-14 挖掘的 7,819 条 `arglist`）

## 五、局限
- `arglist` 只有参数名、**无类型**；类型来自 `.fnd` 时为 `n/t/S/l/g/x` 前缀推断，未覆盖全部；
- 未挖掘的 42,946 个函数 `params()` 返回空 → 只能按位置调用（无 kwargs 支持）；
- 仅 `read` 默认放行，`write` 需显式 `allow_write=True`；
- 未做 GUI 上下文函数的可用性判定。

## 六、下一步
把 `mine.py` 的分块脚本接入一个批量运行器，跑完剩余 41 个 chunk，
把覆盖率从 42.6% 推到 ~85%，再补类型推断（`.fnd` syntax ↔ arglist 对齐）。
