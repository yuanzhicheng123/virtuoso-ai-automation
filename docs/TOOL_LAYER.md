# 工具层：把 Virtuoso 的设计能力暴露成"可发现 / 可调用 / 可验证"的远程工具

目标：AI（或人）能用一套稳定的工具名，驱动远处的 Virtuoso 做设计，并且**每次调用都能自证有效**。

## 1. 三层设计

```
ToolSpec      工具定义：名字、参数 schema、风险级、实现、验证器
Registry      list / describe（MCP 形状）/ call / verify / report
Evidence      每次调用留证据：参数、SKILL、返回值、读回校验结果
```

- **可发现**：`Registry.manifest()` 输出 `{"schema":"mcp/tools/v1","tools":[...]}`，
  每个工具带 `inputSchema`（JSON Schema）和 `annotations.risk`；
- **可调用**：`call()` 先按 schema 校验（缺参/多参直接拒绝），再把参数编码成 SKILL；
- **可验证**：`verify()` 调用后立刻"读回"（`dbGetInstByName`/`length(cv~>nets)`…），
  把"没报错"升级成"副作用真的发生了"。

## 2. 当前工具（13 个，全部对应真机验证过的 SKILL 调用）

| 工具 | 风险 | 说明 | 验证方式 |
|---|---|---|---|
| `vl.lib.create` | write | 建库 | `ddGetObj` 非空 |
| `vl.cell.open` / `close` / `save` | write | 打开/关闭/保存 cellview | `cellName` 可读 / `dbSave` 返回 t |
| `vl.inst.create` | write | 按 master 名放实例 | `dbGetInstByName` 命中 |
| `vl.net.create` | write | 建网络 | `dbGetNet` 命中 |
| `vl.conn.create` | write | 端子连网 | 返回连接对象 |
| `vl.wire.draw` | write | 画线（数据面） | 图形数增加 |
| `vl.shape.rect` | write | 画矩形 | 图形数增加 |
| `vl.prop.set` / `get` | write/read | 自定义属性 | `dbGetPropByName` 命中 |
| `vl.cell.inspect` | read | 读回实例/网络/图形数 | 返回计数 |
| **`vl.design.inverter`** | write | **复合**：2 器件+2 网络+2 连接+1 线+存盘 | 计数匹配 + `saved=True` |

清单文件：`out/vskill_tools.json`（MCP 形状，可直接喂给工具调用方）。

## 3. 真机证据（2026-10-22，eda-220）

```
OK  vl.design.inverter | verify=True | {'instances': 2, 'nets': 2, 'shapes': 1, 'saved': True}
调用 1，成功 1，验证 1/1
```

复合工具一次往返完成：建库 → 打开 schematic → 放 NMOS/PMOS → 建 IN/OUT 网络 →
连 G/D → 画线 → 设 AUTHOR 属性 → 存盘 → 读回计数。

## 4. 一个必须说清楚的限制：**会话持久性**

细粒度工具（`vl.inst.create` 等）假设**同一个 Virtuoso 会话**里句柄有效。
用 `NographBridge`（每次调用起一个进程）时，句柄在第二次调用就失效——
所以：

| 桥 | 适合 |
|---|---|
| daemon（virtuoso-bridge / vcli daemon / skillbridge） | **细粒度工具**（多轮、可交互） |
| `NographBridge` | **复合工具**（一次往返跑完整段脚本） |

这也是为什么加了 `vl.design.inverter`：它把"一次设计动作"打包成一个工具，
在没有 daemon 的环境里也能被验证。

## 5. 与 virtuoso-bridge 的对接

```python
from vskill.bridges.virtuoso import VirtuosoBridge
from vskill.session import Session
from vskill.tools_builtin import BUILTIN

with Session(VirtuosoBridge()) as s:      # client = VirtuosoClient.from_env()
    BUILTIN.session = s
    ev = BUILTIN.verify("vl.inst.create", cv=cv, lib="analogLib", cell="nmos4",
                        view="symbol", name="M1", x=0, y=0)
    assert ev.verified
```

适配器按 `execute_skill → executeSkill → eval_skill → skill → send → call` 顺序探测，
virtuoso-bridge 换方法名也不断。

## 6. 下一步（补齐清单）

| 缺口 | 做法 |
|---|---|
| 编辑器类工具（wire/pin/label 的 GUI 等价） | 接到 GUI 会话（vcli daemon）后加 `vl.sch.wire` 等 |
| 仿真类工具 | `vl.sim.run`（Spectre + PSF 解析） |
| 批量/事务 | `with reg.transaction():` 合成一个脚本，失败回滚 |
| 更细的验证 | 图形几何比对（bbox/layer），不只计数 |
| 工具版本化 | manifest 里加 `version` 与 `deprecated` |

## 7. 复现

```bash
python3 tools/emit_manifest.py                 # 生成 out/vskill_tools.json
python3 tests/live/tools_live.py               # 真机跑复合工具（VLIGHT_HOST=eda-220）
python3 -m pytest tests/test_tools.py -q       # 6 条：schema/校验/验证/报告
```
