# 与 GitHub 上游 virtuoso-bridge (Arcadia-1/virtuoso-bridge-lite) 对比（2026-11-17）

来源：GitHub `Arcadia-1/virtuoso-bridge-lite`（`pyproject.toml` 显示 version = **1.0.0**）与其
`skills/virtuoso/SKILL.md`、`references/client-basics.md`。

**更正**：上一轮我对比的是本机那份**损坏的 editable 安装（0.7.0）**；上游实际是 1.0.0，能力面大得多。

## 一、定位差异（最关键）
| | 上游 1.0.0 | 本仓库 0.8.0 |
|---|---|---|
| 核心思路 | **结构化领域 API**：`client.schematic / symbol / layout / library / maestro / sos` | **全函数库的通用调用层**：注册表驱动，按签名编排 |
| 面向任务 | 具体设计动作（建图/布线/GDS/Maestro/Visio） | 任意 SKILL 函数的可发现、可度量、可管控调用 |
| SKILL 逃生口 | `execute_skill()` / `eval` / `load_il()`（原始 SKILL） | `GenericSkill.call(fn, *args, **kw)`（按签名编排） |

## 二、能力对比
| 维度 | 上游 1.0.0 | 0.8.0 |
|---|---|---|
| 原理图/符号/版图 API | ✅ 体系化（含参考文档全套） | 🟡 有 `schematic_ops`（声明式建台/连线/校验），范围窄 |
| Maestro / ADE | ✅ | ❌ |
| GDS / SOS / Visio / MCP 波形预览 | ✅ | ❌ |
| SSH 传输 | ✅（paramiko，多会话） | ❌（本地 socket） |
| 批量属性读取 | ✅ `fetch()/fetch_one()` | 🟡 通过 `getq` 逐项 |
| **全库函数覆盖度量** | ❌ 无 | ✅ 74,785 符号；签名 82.9%；headless **89.9%** |
| **按签名通用调用** | ❌（靠手写封装或原始 SKILL） | ✅ args/kwargs 编排 + symbol 转换 |
| **按需补签名** | ❌ | ✅ `ensure_signature`（0.3 ms，缓存） |
| **写操作白名单** | 部分（对话框/锁/checkout 等工程安全） | ✅ 调用级策略默认拒绝 |
| **GUI/headless 判定** | 文档要求人工核对 | ✅ `availability` 自动标记（26,587 个 needs_window） |
| 依赖 | 5 个必需（dotenv/pydantic/pyyaml/markdownify + 可选 paramiko/Pillow/pywin32） | **仅 skillbridge** |
| Python | ≥3.9 | ≥3.6 |
| 文档 | 极完善（references 十余篇） | 有 audit 证据，缺系统文档 |
| 成熟度 | 1.0.0，多传输后端 | 原型级 |

## 三、结论
1. **上游在"做什么"上更强**：设计动作的结构化封装、Maestro/GDS/Visio、SSH 传输、文档体系。
2. **我们在"能调什么"上更强**：全库覆盖度量、按签名通用调用、按需挖掘、写安全策略、headless 判定。
3. **两者互补**：
   - 设计任务优先用上游的结构化 API；
   - 上游未覆盖的函数（`execute_skill` 逃生口）用我们的 `GenericSkill` + 策略；
   - 我们的 `coverage()` 可反过来度量"上游到底覆盖了多少函数"。

## 四、建议的整合方式（下一步可选）
1. 在 `VirtuosoClient` 之上挂一个 `generic` 命名空间（复用我们的 registry/policy）；
2. 用我们的 `availability()` 给上游文档里的"需人工核对"项做自动预判；
3. 把覆盖率报告贡献回上游 issue/PR，作为能力路线图依据。
