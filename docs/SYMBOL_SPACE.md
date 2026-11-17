# 最后一层：把 SKILL 的**完整符号空间**挖到底

## 1. 方法（只读）

```skill
(foreach s oblist
  (setq g (getd s))
  (if g (fprintf p "%s|fn|%L\n" s g)                  ; 函数
      (if (errset (eval s))                            ; 变量有值
          (fprintf p "%s|var|%L\n" s (type (eval s)))
          (fprintf p "%s|unbound|nil\n" s))))          ; 未绑定
```

环境：GUI 会话，同时打开 schematic 与 layout 窗口（覆盖最广的上下文）。

## 2. 结果（eda-220，IC2310）

| 类别 | 数量 |
|---|---|
| **符号总数** | **200,690** |
| 函数（`getd` 非 nil） | **74,785** |
| **全局变量（有值）** | **22,367** |
| 未绑定符号 | **103,537** |

变量类型分布：`fixnum` 8,732 · `symbol` 5,628 · `list` 3,607 · `string` 1,055，
其余是 UI 结构（`buttonStruct` 252、`labelStruct` 366…）。

变量家族：`lec*` 2,103 · `_te*` 1,419 · `_db*` 1,130 · `_lx*` 945 · `_cl*` 611 · `__i*` 607。

未绑定符号是**属性名/枚举/关键字词表**：`loEnable`、`HaloSize`、`AutoFix`、
`Standard`、`Customized`、`AnnotationLimit`…（1,143 个 OA 属性名里有 387 个在此出现）。

## 3. 五层结构（可挖的全部）

| 层 | 内容 | 数量 | 状态 |
|---|---|---|---|
| L1 函数 | `getd` 非 nil | **74,785** | ✅ 全量 |
| L2 签名 | `arglist` | **24,267** | ✅ 有 arglist 的全拿到 |
| L3 变量 | 有值的全局符号 | **22,367** | ✅ 全量（本轮新增） |
| L4 词表 | 未绑定符号（属性/枚举/关键字） | **103,537** | ✅ 全量（本轮新增） |
| L5 C++ 层 | OA 类/属性/访问器 | 1,039 类 / 3,730 属性 / 4,176 访问器 | ✅ 符号级 |
| L6 二进制 | `.cxt` 字节码、OA ABI | — | ❌ 不挖（私有格式，需反编译） |

**结论：SKILL 层已经挖无可挖**——符号表 100% 枚举，函数/变量/词表三层全在。

## 4. 这些变量有什么用

| 变量家族 | 用途 |
|---|---|
| `lec*`（2,103） | 版图编辑器约束/规则开关 |
| `_db*`（1,130） | 数据库内部开关与阈值 |
| `_lx*`（945） | Virtuoso XL 布线器参数 |
| `_vcac*` | 布线器告警/限值 |
| `de*` | 设计编辑器行为 |

它们是"改行为"的旋钮（很多没有文档）。**只读枚举安全，修改有风险**——不在本次范围。

## 5. 产物

- [`out/mine/symbols.txt`](out/mine/symbols.txt)：200,690 行原始记录（`name|kind|detail`）
- [`out/mine/symbol_space.json`](out/mine/symbol_space.json)：分类统计
- [`out/mine/oa_properties.json`](out/mine/oa_properties.json)：OA 属性字典

## 6. 一句话

**SKILL 的可枚举边界就是 200,690 个符号**：74,785 个函数、22,367 个变量、103,537 个词表项。
再往下是 C++ ABI 与私有字节码——那不是"SKILL"，也不在只读审计的范围内。
