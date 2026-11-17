# 100% 覆盖：所有函数、所有功能，甚至 Virtuoso 没有的

"100%"不是一句话，是四级承诺。每级都有数字、都有机制、都能回归。

## 0. 四级承诺与现状

| 级别 | 含义 | 现状 | 保证方式 |
|---|---|---|---|
| **L0 名字级** | 文档里出现的每个函数，在解释器里都存在、可解析 | **6,162 / 6,162 = 100%** | 文档索引 + 自动 stub 绑定 |
| **L1 可调用** | 调用不会"未定义"，要么返回结果，要么抛类型化错误 | **100%** | registry 四层解析 |
| **L2 行为级** | 本地实现真的算出结果 | **152 个 native**；真实脚本调用覆盖 **98.8%** | 按频次实现 + 差分验证 |
| **L3 扩展** | Virtuoso 没有的能力 | **4 个 `vl*`**，可反向导出 SKILL 包装 | extension 层 |

数字来源：`python3 tools/coverage.py`

```
== 名字级 ==
  索引函数 6162，已登记 6198 = 100.0%
  分层：emulated=6042, extension=4, native=152
== 真实脚本调用（内置函数） ==
  调用总数 352,909，本地覆盖 348,809 = 98.8%
```

## 1. 机制（已经落地，不是设想）

### 1.1 四层登记表 `vlight/registry.py`
| tier | 含义 | 行为 |
|---|---|---|
| `native` | 本地实现 | 直接执行（vdb / 几何 / 仿真） |
| `extension` | Virtuoso 没有的新功能 | 直接执行，且可导出成 SKILL 包装 |
| `proxy` | 转发真实 Virtuoso | JSON-RPC 调用，返回远端句柄 |
| `emulated` | 只有名字（来自文档索引） | 抛 `Unimplemented`，带签名；接上代理即执行 |

解析顺序：native > extension > proxy > emulated。**名字永远存在**，这就是 L0/L1 的 100%。

### 1.2 代理协议 `vlight/skillproxy.py`
```
请求 {"seq":1,"op":"call","fn":"dbOpenCellViewByType","args":[...]}
响应 {"seq":1,"ok":true,"result":{"$h":{"type":"cellview","id":"cv1"}}}
```
- 远端对象 → `RemoteHandle`，`cv~>cellName` 时回源 `op=getq`，写操作 `op=setq`
- `RecordingTransport`：把交互写成 `jsonl`；`ReplayTransport`：离线回放
- 传输层可替换：Socket（真机）/ FakeBackend（测试）

### 1.3 自动绑定 `vlight/skill.py`
`Session()` 启动时：先把已有实现标 `native`（含 special form），再从索引补齐 6,042 个桩，
最后把 `~>` 属性访问接上远端句柄。**任何脚本里的任何函数名都不会"未绑定"。**

### 1.4 覆盖率工具 `tools/coverage.py`
- 名字级 / 分层 / 分手册
- 真实脚本调用覆盖率（用 3,519 个 `.il` 的调用频次做分母）
- **下一步该实现谁**：按调用频次排出的 emulated 清单

### 1.5 扩展 `vlight/extensions.py`
`vlNetlist` / `vlLvs` / `vlRun` / `vlExportSkill`：`vlExportSkill()` 会打印对应的
`procedure( ... )`，粘到 Virtuoso 里就能反向调用——**能力双向流动**。

## 2. 把 L2 从 98.8% 推到 100% 的路

按调用频次排的待实现清单（工具自动生成）：

| 函数 | 调用次数 | 说明 |
|---|---|---|
| `load` | 143 | 加载 .il |
| `rexMatchp` | 131 | 正则 |
| `dbDeleteObject` | 113 | 删对象 |
| `hdbOpen` / `hdbClose` | 112 / 112 | 层次数据库 |
| `dbCreateParamInstByMasterName` | 106 | 按 master 名建实例 |
| `dbCopyCellView` | 91 | 复制视图 |

做法固定：**实现一个 → 加一条差分用例（对真实 Virtuoso 或录制轨迹）→ 覆盖率涨一格**。
没实现的继续走代理兜底，行为级"够用"。

## 3. Virtuoso 没有的东西怎么加

1. 命名空间 `vl*`，不与 SKILL 冲突；
2. `@extension` 注册到 extension 层，SKILL 里直接可调；
3. `vlExportSkill()` 导出 SKILL 包装，在 Virtuoso 侧用 `vlProxyCall` 回调本地；
4. 纯增量：不加就完全没有，加了也不影响原生函数。

## 4. 边界（写清楚，不吹）

- **签名级 100% ≠ 行为级 100%**：6,042 个桩里，GUI（`hi*`）、版图编辑器（`leHi*`）、
  数字/验证器 API 占大头，真实脚本几乎不调用；
- **代理依赖真实机器与 license**：离线时用录制回放；
- **GUI 与性能不在"覆盖"定义内**：我们保证的是"调用/结果/数据"，不是"像素与速度"；
- **索引有挖矿缺口**：`schCheck` 这类"命令"不是函数页，靠 doc-scan 补名字，签名仍缺。

## 5. 复现

```bash
python3 tools/coverage.py                     # 100% 名字级 + 98.8% 调用级 + 待办清单
python3 -m pytest tests/test_coverage.py -q   # 6 条：索引/分层/代理/录制回放/扩展
python3 -m conformance.run                    # 20/20（端到端没退化）
python3 -m compat.check                       # 13/13（KLayout 没退化）
```

## 6. 一句话

**L0/L1 的 100% 是构造出来的**（索引 + 桩 + 代理），
**L2 的 100% 是挣出来的**（按频次实现 + 差分回归），
**L3 是设计出来的**（`vl*` 扩展 + 双向导出）。
三条都不靠"重写 6,162 个函数"，靠的是分层、兜底和度量。
