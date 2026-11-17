# 这些函数是谁的：Cadence 原有 vs 我封装的

一句话：**函数是 Cadence 的，Python 壳是我生成的，功能一行都没重写。**

## 1. 三层来源

| 层 | 内容 | 谁做的 | 数量 |
|---|---|---|---|
| **Cadence 原有** | `dbCreateInst`、`dbOpenCellViewByType`、`rodCreateRect`、`hiGetCurrentWindow`… | Cadence（Virtuoso 自带） | 6,153 个名字 |
| **我挖的元数据** | 函数名、参数名、语法、所属手册 | 我（从 `doc/` 的 11 本 HTML 手册解析） | 同上 |
| **我生成的 Python 壳** | `vskill.dbCreateInst(...)` | 我（`tools/gen_vskill.py`） | 6,143 个 |
| **我写的本地实现** | 同名函数的本地算法（几何/仿真/网表/DRC…） | 我（`vlight/`） | **120 个** |
| **我加的新功能** | `vlNetlist` / `vlLvs` / `vlRun` / `vlExportSkill` | 我 | 4 个（Virtuoso 没有） |

## 2. "封装"到底封装了什么

6,023 个包装函数的**全部代码**就是这一段（`vskill/api.py`）：

```python
def __call__(self, *args, **kwargs):
    if self.args and len(args) > len(self.args):      # 参数上限检查
        raise TypeError(...)
    if kwargs:                                        # ?key value
        args = args + tuple(...)
    return get_bridge().call(self.name, list(args))   # 编码成 SKILL 文本，发出去
```

它**不实现任何功能**：把 `vskill.dbCreateInst(cv, "nch", "M1", Point(0,0), "R0")`
变成字符串 `dbCreateInst(cv "nch" "M1" 0:0 "R0")`，发给一台真的 Virtuoso 去执行。

没有 Virtuoso 时，它只会抛 `Unimplemented` 或走 `MockBridge`——**没有远端就没有能力**。

## 3. 数字对账

```
SKILL 名字（Cadence 文档）        6,153
vskill 包装（我生成）             6,143     （10 个非标识符名字被过滤）
  其中：有本地实现（我写）          120
        纯转发（无本地实现）      6,023
扩展函数（Virtuoso 没有）             4
```

## 4. 哪些是"真的有功能"

| 类别 | 例子 | 功能来自 |
|---|---|---|
| 纯转发 | `dbCreateInst` | **远端 Virtuoso**（我们只转发） |
| 本地实现 | `dbOpenCellViewByType`（vlight 版）、`abs`、`sprintf` | **我们的代码**（`vlight`） |
| 扩展 | `vlLvs` / `vlNetlist` | **我们的代码**（Virtuoso 没有） |
| 组合 | `vskill.dbCreateInst` 落到本地 vlight 的 `Design` | 看桥怎么接 |

## 5. 诚实边界

- **函数名不是我们发明的**：全部来自 Cadence 文档的目录（`skdfref` 1682、`sklayoutref` 1788…）。
- **签名可能过期**：文档与实现会漂移，真机验证时才暴露（`ddCreateLib` 参数顺序、
  `dbCreateInstByMasterName` 的 `l_origin` 要 list、点要 `x:y`）。
- **doc-scan 有噪声**：已过滤 9 个文档标题（`Attributes`、`Expressions`、`Help`…）。
- **不能用它来"白嫖" Cadence**：没有 license 和 Virtuoso 进程，这些名字只是名字。

## 6. 结论

- **原本就有**：SKILL 函数、它们的语义、它们的实现——都在 Cadence 产品里。
- **我加的**：一层 12 行的 Python 壳、四个桥、120 个本地同名实现、4 个新扩展。
- **"复用"的真实含义**：让 Python 用同一套名字、同一套参数，去调那台已经在跑的 Virtuoso；
  而不是把 Virtuoso 的代码搬出来。
