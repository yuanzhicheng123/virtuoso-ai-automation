# 批量真机验证：377 个函数被证明确实能调用

## 1. 方法

```skill
;; 对每个函数，按 arglist 的参数名生成合法实参（dbobject→cv、t_string→"MN"…），
;; 依次尝试 cv / inst / net 三种对象，用 errset 判定成功与否。
(setq r (errset (dbGetInstByName cv "MN")))
(fprintf p "%s|%L|%L\n" "dbGetInstByName" (if r "ok" "err") (if r (car r) nil))
```

关键细节（都踩过）：
- `errset` 必须 `(errset expr)`；`(errset expr t)` 语义不同，会全部返回 nil；
- `%L` 打印字符串带引号，解析时要 strip（`"ok"` ≠ `ok`）；
- 参数不能一律传 nil：`dbGet*` 需要真实 `cv`/`inst`/`net`。

## 2. 结果（eda-220，只读族 500 个函数）

| 指标 | 数量 |
|---|---|
| 探测次数（每函数试多组实参） | 1,297 |
| 成功 | 870 |
| 失败 | 427 |
| **可调用的函数** | **377** |
| 其中返回非 nil 值 | **49** |
| 全部尝试都失败 | 90 |

按家族：`db*` 167 · `tech*` 118 · `cdf*` 7 · 其余单点。
（`sch*`/`le*` 编辑器族不在 `-nograph` 环境，未纳入本轮。）

## 3. 已验证返回值的函数（节选）

```
dbCheck(cv)                  → t
dbIsId(cv)                   → t
dbGetInstByName(cv "MN")     → db:0x…
cdfGetInstCDF(inst)          → cdf:0x…
techGetTechFile(cv)          → db:0x…
dbFindAnyInstByName(cv ...)  → db:0x…
dbGetBagTimeStamp(...)       → 数值/对象
dbGetCellViewCounter(cv)     → 数值
```

完整清单：[`out/mine/verified.json`](out/mine/verified.json)（377 个已验证 + 49 个返回值）。
原始探测记录：[`out/mine/verify_results.txt`](out/mine/verify_results.txt)（1,297 行）。

## 4. 已写进库

`vskill` 的函数表现在带标记：

```python
import vskill
vskill.FUNCTIONS['dbCheck']        # {'args': [...], 'verified': True}
[n for n, m in vskill.FUNCTIONS.items() if m.get('verified')]   # 377 个
```

## 5. 失败的 90 个：为什么

| 原因 | 例子 | 说明 |
|---|---|---|
| 需要特殊对象 | `techGetExtract*` 返回 nil（可调用但无值） | 工艺里没定义提取规则 |
| 参数语义没猜对 | `dbConvertDBUToUU` 需要布尔 | `arglist` 只给类型没给含义 |
| 需要上下文 | `dbGetCellViewDdId` 需要已保存的 cellview | 沙箱 cellview 刚建未存 |

这些标 `needs-investigation`，不冒充“已验证”。

## 6. 下一步

1. 把同样的批量方法用到 `dbSet*`/`dbCreate*`（写操作，需沙箱 + 回滚）；
2. GUI 会话里开窗口，把 `sch*`/`le*` 纳入（现在它们连函数都还没加载）；
3. 对 49 个“返回值正确”的函数补断言（值对不对，而不只是“没报错”）。
