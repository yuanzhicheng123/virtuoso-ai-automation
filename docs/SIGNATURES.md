# 边挖边验：函数 + 签名 + 真机验证

## 1. 三个原语

| 原语 | 作用 | 实测 |
|---|---|---|
| `oblist` | 运行时全部符号 | 116,243 |
| `getd` | 取函数定义；变量/未定义返回 nil | **48,198 个函数** |
| `arglist` | 取参数表（`lambda:` 类型有，`primop:` 没有） | **24,267 个带签名** |

```skill
(foreach s oblist
  (when (getd s)
    (fprintf p "%s|%L|%L\n" s (getd s) (errset (arglist (getd s))))))
```

注意：`errset` 必须写成 `(errset expr)`；写成 `(errset expr t)` 语义不同（早期踩过这个坑，
导致所有探测都返回 nil）。

## 2. 签名的样子

`arglist` 直接给出参数名和位置标记：

| 函数 | 参数表 |
|---|---|
| `dbCheck` | `(@optional g_general)` |
| `dbConvertDBUToUU` | `(g_general)` |
| `dbGetInstByName` | `(dbobject t_string)` |
| `cdfCopyCDF` | `(g_general t_string g_general)` |
| `dbCompactCellView` | `(dbobject @optional g_general ×5)` |
| `pcAdjustConditionalStretches` | `(skillfile stretches params)` |
| `techDumpLayerMapping` | `(dbobject t_string)` |

参数名里的类型前缀（`d`/`t`/`g`/`l`/`x`）就是 Cadence 自己的约定：
`dbobject`、`t_string`、`g_general`…

## 3. 合并进库

```
合并前（文档）        6,162
+ 运行时公开        10,424
+ 带签名的隐藏函数   14,699
= 索引总数          31,276
vskill Python 表    31,248
```

`vskill` 的 arity 检查现在用的是**真参数个数**（含 `@optional` 处理）。

## 4. 真机验证（这一轮新增 6 个）

| 函数 | 调用 | 结果 |
|---|---|---|
| `dbCheck` | `(dbCheck)` / `(dbCheck cv)` | ✅ `t` / `t` |
| `dbIsId` | `(dbIsId cv)` | ✅ `t` |
| `dbGetInstByName` | `(dbGetInstByName cv "MN")` | ✅ 对象 |
| `cdfGetInstCDF` | `(cdfGetInstCDF inst)` | ✅ `cdf:0x…` |
| `techGetTechFile` | `(techGetTechFile cv)` | ✅ 对象 |
| `dbConvertDBUToUU` | `(dbConvertDBUToUU t)` / `(nil)` | ✅ `t` / `nil` |

累计真机验证：**17 个**（11 条 `tests/live/` + 6 条本轮）。

## 5. 没验成的（诚实记录）

| 函数 | 现象 | 推测 |
|---|---|---|
| `techDumpLayerMapping` | 返回 nil，文件没生成 | 第 1 个参数可能要 `tech` 对象而不是 cellview 的 techfile；或需要先 `techOpenTechFile` |

这类"签名有了但语义没对"的函数，标 `needs-investigation`，不进 `verified` 集合。

## 6. 下一步

1. 把 `arglists.json`（24,267 条）按家族切片，挑只读函数批量跑真机验证；
2. 给 `vskill` 加 `verified` 标记：只有真机通过过的函数才标绿；
3. GUI 会话里开窗口，把 `sch*`/`le*` 的 200+ 个编辑器函数也收进 `arglist`。

## 7. 复现

```bash
python3 mine/skill_runtime.py --binary virtuoso --out /tmp/fns.txt   # 函数清单
# 签名：同一脚本里把 getd 换成 (errset (arglist (getd s)))
```
