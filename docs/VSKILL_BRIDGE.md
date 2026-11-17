# vskill：把 SKILL 函数表变成 Python，桥接 virtuoso-bridge

目标：**SKILL 里能调的东西，Python 里也能调**——名字、参数、返回值、对象句柄都对齐，
底层换成任意桥（virtuoso-bridge / skillbridge / 直接喂 `skill` 二进制 / 离线 mock）。

## 1. 三层结构

```
vlight/_data/skill_index.json          ← 挖矿索引（6,151 个函数）
        │ tools/gen_vskill.py
        ▼
vskill/_index.json                     ← 运行期函数表（含参数名/语法/手册）
vskill/api.pyi                         ← 类型存根（IDE 补全，6,160 行）
        │
        ▼
vskill/api.py    SkillFunction(name).__call__(*args)  →  arity 检查 → 编码
        │
        ▼
vskill/bridge.py Bridge 实现：
     MockBridge                 离线，函数表 + 对象表（测试/演示）
     VirtuosoBridgeAdapter      适配 virtuoso-bridge（execute_skill/send/call）
     SkillbridgeAdapter         适配 skillbridge（220 上的 1.5.1）
     SubprocessSkillBridge      直接 `skill` 二进制，无服务端
```

## 2. 用法（换桥只换一行）

```python
import vskill

# 离线
vskill.set_bridge(vskill.MockBridge())

# virtuoso-bridge
vskill.set_bridge(vskill.VirtuosoBridgeAdapter(host="eda-220", port=65432))

# skillbridge
vskill.set_bridge(vskill.SkillbridgeAdapter(host="eda-220", port=7000))

# 直接喂 skill 二进制
vskill.set_bridge(vskill.SubprocessSkillBridge(
    binary="/eda/cds/IC2310/tools/dfII/bin/64bit/skill"))

cv = vskill.dbOpenCellViewByType("MYLIB", "lna", "layout", "r", "")
print(cv.cellName)                       # → getq(cv cellName)
inst = vskill.dbGetInstByName(cv, "M1")
print(inst.width)                        # → 2e-06
vskill.dbCreateInst(cv, "tsmcN65/nch", "M2", [0, 0], "R0")
```

只想要 SKILL 代码（审计/粘到 CIW）：

```python
vskill.dbCreateInst.skill(cv, "tsmcN65/nch", "M2", [0, 0], "R0")
# 'dbCreateInst(cv1 "tsmcN65/nch" "M2" (0 0) "R0")'
```

## 3. 做了什么转换

| SKILL | Python |
|---|---|
| `f(a b)` | `vskill.f("a", "b")`（arity 超出报 `TypeError`，带原签名） |
| `?key value` | `vskill.f(..., key=value)` → `?key value` |
| `"str"` / `nil` / `t` | `str` / `None` / `True` |
| `(1 2 3)` | `[1, 2, 3]` |
| `R0`（符号） | `vskill.Symbol("R0")` |
| cellview/inst/net 对象 | `SkillHandle`，属性读写自动 `getq`/`putpropq` |

## 4. 规模（实测）

```
函数数 6,151     存根行数 6,160     索引 960 KB
skdfref 1682 · sklayoutref 1788 · skuiref 785 · doc-scan 602 ·
sklangref 442 · sktechfile 335 · vivaxlskill 228 · skpcellref 129 ·
oceanref 58 · skoopref 44 · skipcref 34 · rodskillref 24
```

一条命令重新生成（索引变了就重跑）：

```bash
python3 tools/gen_vskill.py      # 写 _index.json / api.pyi / manifest
```

## 5. 桥的契约（写新适配器只需两个方法）

```python
class MyBridge(vskill.Bridge):
    def call(self, fn: str, args: list):      # args 已是 SKILL 字面量可编码的 Python 值
        return ...                            # 返回 Python 值或 SKILL 字符串
    def get_attr(self, obj, prop): ...        # 默认 getq；可覆盖
    def set_attr(self, obj, prop, value): ... # 默认 putpropq
```

`VirtuosoBridgeAdapter` 是 **duck typing** 的：传进来的 client 只要有
`execute_skill` / `executeSkill` / `eval_skill` / `send` / `call` 之一即可，
所以 virtuoso-bridge 升级换类名也不影响。

## 6. 边界（说清楚）

| 项 | 现状 |
|---|---|
| 签名 | 来自文档，**可选参数被展平**（`[ x ]` 的方括号在挖矿时去掉了）→ 只做"上限"检查 |
| 返回值解析 | 内置一个轻量 SKILL 解析器（字符串/数字/list/t/nil/handle）；复杂结构建议桥侧直接给 JSON |
| GUI 函数 | `hi*` / `leHi*` 需要**活着的会话**，离线 mock 只能验证调用形状 |
| special form | `if/let/foreach` 不是函数，不在 `vskill` 里（它们是解释器语法） |
| 权限 | 真机调用会改库，建议在副本 library 上跑 |

## 7. 测试与演示

```bash
python3 -m pytest tests/test_vskill.py -q     # 9 条：表完整/arity/编码/句柄/适配器/存根
python3 examples/vskill_demo.py               # 离线跑一遍，打印将发送的 SKILL
```

## 8. 封装成库（可安装、可命令行）

```toml
# pyproject.toml
[project.scripts]
vskill = "vskill.cli:main"

[project.optional-dependencies.bridge]
virtuoso-bridge = ["virtuoso-bridge>=0.1"]

[tool.setuptools]
packages = ["vlight", "vskill", "vskill.bridges"]

[tool.setuptools.package-data]
vskill = ["_index.json", "api.pyi"]
```

命令行：

```bash
vskill list --filter dbCreate --limit 20     # 查函数
vskill doc dbCreateInst                      # 看签名/参数名
vskill eval 'car(list(1 2 3))' --bridge virtuoso
```

### 8.1 对接 virtuoso-bridge 的真实形态

virtuoso-bridge 的客户端是 `virtuoso_bridge.VirtuosoClient`（`from_env()` 读
`~/.virtuoso-bridge/.env`），执行入口是 `execute_skill(...)`，另有
`client.schematic.create(...)` 的批量编辑器。适配器 `vskill.bridges.virtuoso.VirtuosoBridge`：

```python
from vskill.bridges.virtuoso import VirtuosoBridge
vskill.set_bridge(VirtuosoBridge())            # from_env() 自动连
vskill.set_bridge(VirtuosoBridge(client=my_client))   # 显式传入
```

它按顺序探测 `execute_skill / executeSkill / eval_skill / skill / send / call`，
所以 virtuoso-bridge 改方法名也不会断。

### 8.2 批量：6 千个函数不能变成 6 千次往返

```python
with vskill.Session(VirtuosoBridge()) as s:
    with s.batch() as b:                  # 一次 RTT
        r1 = b.dbCreateInst(cv, "nch", "M1", [0, 0], "R0")
        r2 = b.dbCreateInst(cv, "pch", "M2", [4, 0], "R0")
    print(r1.value, r2.value)
```

生成并发送：

```skill
let( (__vl0 __vl1)
  __vl0 = dbCreateInst(cv "nch" "M1" (0 0) "R0")
  __vl1 = dbCreateInst(cv "pch" "M2" (4 0) "R0")
  list(__vl0 __vl1)
)
```

`Batch` 用 `Ref` 占位，flush 后回填 `.value`；没有 `execute_batch` 的桥自动退化为逐条。

## 9. 一句话

**能。** 挖出来的 6,151 个函数 → 一张表 + 一个 `__getattr__` + 四个桥，
Python 侧就得到了与 SKILL 同名的 API；`virtuoso-bridge` 只是其中一个可替换的传输层。
