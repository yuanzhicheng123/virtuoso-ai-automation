# virtuoso-bridge：原 0.7.0 vs 本仓库 0.8.0（2026-11-17）

## 一、可比性说明（先说清楚）
- 原 0.7.0 是 **editable 安装**：`direct_url.json` 指向 `file:///tmp/virtuoso-bridge-lite`，
  该目录**已被清空**；PyPI 上也**没有**该发行版（`pip download` 无匹配）。
- 因此原库**源码不可得**，只能依据 `dist-info/METADATA` 与 `entry_points.txt` 做**可证对比**；
  未证实的部分一律标 UNKNOWN，不臆测。

## 二、可证元数据对比
| 维度 | 原 0.7.0（dist-info 可证） | 本仓库 0.8.0（源码可证） |
|---|---|---|
| 包名/版本 | virtuoso-bridge 0.7.0 | virtuoso-bridge 0.8.0 |
| 安装状态 | ❌ **损坏**：editable 指向已删除目录 → `import virtuoso_bridge` 失败 | ✅ 完整包 + `pyproject.toml`，可 `pip install -e .` |
| Python 要求 | `>=3.9` | `>=3.6`（现场 conda 3.6.8 直接可用） |
| 运行时依赖 | `python-dotenv>=1.0`、`pydantic>=2.0`、`pyyaml>=6.0`、`markdownify>=0.14` | 仅 `skillbridge`（无额外依赖） |
| 可选扩展 | `visio`（Windows: pywin32） | 无 |
| CLI | `virtuoso-bridge = virtuoso_bridge.cli:main` | `tools/skill_cli.py`（`--fn` / `--why` / `--allow-write`） |
| 定位（自述） | "Lightweight Python bridge for SKILL execution and Spectre simulation" | 在原定位上增加**注册表驱动 + 策略化** |

## 三、能力对比（原库 UNKNOWN 项只标未知）
| 能力 | 原 0.7.0 | 0.8.0 |
|---|---|---|
| 函数级通用调用（按签名编排参数） | UNKNOWN | ✅ `GenericSkill`（args/kwargs→位置、symbol 自动转换） |
| 函数覆盖度量 | UNKNOWN | ✅ 74,785 符号；有签名 61,979（82.9%）；headless 口径 **89.9%** |
| 签名挖掘 | UNKNOWN | ✅ 分块批量 + **按需 `ensure_signature`（0.3 ms，自动缓存）** |
| 写操作安全 | UNKNOWN | ✅ `Policy` 白名单 + 默认拒绝（`WRITE_NEEDS_EXPLICIT_ALLOW`） |
| GUI 上下文判定 | UNKNOWN | ✅ `availability`：HEADLESS_OK / NEEDS_WINDOW / NO_SIGNATURE；26,587 已标记 |
| 失败函数管理 | UNKNOWN | ✅ skip list（`registry/arglist_skip.json`） |
| 覆盖率分桶 | UNKNOWN | ✅ total / public / documented / headless 四口径 |
| 测试 | 无源码、无测试可查 | ✅ 6 项策略单测 + 全套测试全绿 |
| 实测调用率 | UNKNOWN | ✅ 抽样 40 个只读函数 **39/40 = 97.5%** |
| 错误语义 | UNKNOWN | ✅ 真实异常透传（`getpid` → `*Error* eval: undefined function`） |
| Visio 图输出 | ✅ 有 extra 声明 | ❌ **未实现**（唯一的已知功能缺失） |

## 四、结论
1. **可用性**：原库当前**不可导入**（源码丢失），0.8.0 可直接用；
2. **能力面**：0.8.0 多出"通用调用/覆盖度量/按需挖掘/写安全/GUI 判定"五块，
   且这些都有真机或单测证据；
3. **唯一退步**：`visio` extra（Windows 图形输出）未实现；
4. **依赖**：原库 4 个第三方依赖 → 0.8.0 零额外依赖，部署更轻。

## 五、建议
- 若需要 Visio 输出：单独加一个可选子模块，不影响主链路；
- 若要回到"原库 API 兼容"：0.8.0 已保留 `Skill` 类与 `.call()` 形态，可平滑替换。
