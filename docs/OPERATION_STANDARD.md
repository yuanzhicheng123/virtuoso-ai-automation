# 「全部操作」的标准：L0–L4 与逐面覆盖

"能调用到 Cadence 的全部功能"必须可判定。这里把"操作"拆成五级，每一级都有真机证据。

## 1. 五级标准

| 级 | 含义 | 判定方法 |
|---|---|---|
| **L0 可寻址** | 能拿到对象（库/单元/视图/cellview/inst/net/shape） | 真机返回 `db:0x…` |
| **L1 可读** | 能读出对象的属性 | `obj~>prop` 返回值 |
| **L2 可写** | 能建/改/删对象与属性 | 返回对象或 `t`，且读回一致 |
| **L3 可往返** | 存盘后重开，内容一致 | `dbSave` → `dbOpen… "r"` 对比 |
| **L4 与工具等价** | GUI 里看到的与脚本一致 | 需 GUI 会话比对（未做） |

## 2. 逐面覆盖（含实测）

| 面 | L0 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|
| 库/单元/视图 | ✅ | ✅ | ✅ | ✅ | — |
| 原理图对象（inst/net） | ✅ | ✅ | ✅ | ✅ | ❌ |
| 版图图元（rect/path/label） | ✅ | ✅ | ✅ | ✅ | ❌ |
| 属性（自定义 `dbReplaceProp`） | ✅ | ✅ | ✅ | ✅ | — |
| 工艺/CDF | ✅ | ✅ | 🟡 | — | — |
| 网表/仿真 | 🟡 | — | — | — | — |
| 编辑器命令（`schCreateWire` 等） | ❌ | — | — | — | — |

## 3. 属性空间：从 OA 符号挖出 1,039 个类、3,730 个属性

```bash
nm -D --defined-only liboaDesign.so liboaBase.so liboaTechDB.so \
  | awk '{print $3}' | c++filt \
  | grep -oE 'OpenAccess_4::oa[A-Za-z]+::(get|set|is|has)[A-Za-z0-9_]+'
```

| 指标 | 数量 |
|---|---|
| 访问器（get/set/is/has） | **4,176** |
| 类 | **1,039** |
| 去重后的属性条目 | **3,730** |

样例（`oaInst` 的 12 个属性）：`name`、`master`、`instTerms`、`numBits`、`libName`、
`cellName`、`header`、`cluster`、`implicit`、`occInst`、`assignments`、`blockagesOwnedBy`。

这些就是 SKILL `~>` 的属性名来源（`getName` ↔ `inst~>name`）。
完整字典：[`out/mine/oa_properties.json`](out/mine/oa_properties.json)。

## 4. 本轮真机验证（属性 + 往返）

| 操作 | 结果 |
|---|---|
| `cv~>libName` / `cv~>cellName` | ✅ `"vlPy"` / `"inv"` |
| `cv~>instances` / `cv~>nets` | ✅ 1 / 1 |
| `inst~>name` / `inst~>master` / `inst~>cellName` | ✅ `"MN"` / `db:0x…` / `"nmos4"` |
| `net~>name` | ✅ `"IN"` |
| `dbReplaceProp(cv "MYPROP" "string" "hello")` | ✅ 对象 |
| `dbGetPropByName(cv "MYPROP")` | ✅ 对象 |
| `dbDeletePropByName(cv "MYPROP")` | ✅ `t` |
| `dbSave` → 重开 → 读回 | ✅ insts=1 / nets=1 |

**L0–L3 在这几个对象上全部成立。**

## 4.1 场景验证：不靠 `schCreateWire` 也能画出"有连线"的原理图

真机（2026-10-22）：

| 步骤 | 调用 | 结果 |
|---|---|---|
| 两个器件 | `dbCreateInstByMasterName ×2` | ✅ |
| 建网络 | `dbCreateNet cv "NET1"` | ✅ |
| **连接** | `dbCreateConnByName(net, m1, "D")` / `(net, m2, "G")` | ✅ 两个连接对象 |
| **画线** | `dbCreateLine(cv (list "text" "drawing") (list 0:0 4:0))` | ✅ 图形对象 |
| 存盘 | `dbSave` | ✅ |
| 重开读回 | `length(cv~>shapes)` / `length(cv~>nets)` | ✅ 1 / 1 |

结论：**`schCreateWire` 缺失不等于画不了连线**——数据面用 `dbCreateLine` + `dbCreateConnByName` 就够，
而且连接关系（netlist 的实质）由 `dbCreateConn*` 建立，不依赖编辑器命令。

## 5. 还差的（标准没达到的部分）

| 缺口 | 影响 | 怎么补 |
|---|---|---|
| 编辑器命令（`schCreateWire`/`leHi*`） | 画不了连线和交互版图 | GUI 会话里先开窗口（`deOpen` 参数要试出来） |
| L4（GUI 等价） | 无法证明"脚本做的 == 界面上看到的" | 需要 Xvfb 截图比对 |
| 工艺/CDF 的写操作 | 改不了 tech/CDF | `techSet*`/`cdfSet*` 批量验证 |
| 仿真/网表面 | 只做了自研引擎 | OCEAN 接口验证 |

## 6. 结论

- **"函数"面**：`-nograph` 环境已挖到底（48,198），有签名的 24,267，隐藏的公开 10,424，真机验证 377。
- **"属性"面**：已从 OA 符号挖出 3,730 个属性名，并在真机上验证了读/写/删/存盘往返。
- **"操作"面**：数据模型（L0–L3）成立；编辑器与 GUI（L4）还没过。

**标准 = L0–L4 全绿**。现在的进度：数据面 L3 达标，编辑器面 L0 都没到。
下一步只有一件事最关键：**在 GUI 里打开一个 schematic 窗口**，把编辑器上下文加载起来——
那一步过了，`sch*`/`le*` 的函数和操作才能进入同一套标准。
