# 按 KLayout 补缺口：兼容性矩阵与实测

目标：**在版图/几何/规则这条线上，让 vlight 与 KLayout 的结果逐项对得上**，并且每一对都能回归。
对照对象：本机 KLayout **0.29.8（二进制）** + **0.30.5（Python 模块）**。

```
python3 -m compat.check        # 13 项，全部与 KLayout 对跑
```

## 1. 实测结果

```
总体 13/13 = 100%
data 7/7 · geometry 3/3 · verification 3/3
```

| 检查 | 比什么 | 结果 |
|---|---|---|
| data.gds_write | 本实现写 → KLayout 读：cell 数、层、文字数 | ✅ 2 cells / [(1,0),(2,0)] / 1 text |
| data.text_path | TEXT 字符串、PATH 线宽 | ✅ `VOUT`、0.4 µm |
| data.aref | AREF 阵列（COLROW + XY 三点） | ✅ 扁平化 11 个元素，与 KLayout 一致 |
| data.kl_write | KLayout 写 → 本实现读（含 2×2 阵列） | ✅ 3 图形 / 1 实例 / 间距 2 µm |
| data.hierarchy | 三层 SREF 层次 | ✅ 两边递归/扁平化都是 6 |
| data.oasis | 本实现出 OASIS（经 KLayout 桥） | ✅ 476 B，cells/层/文字与 GDS 一致 |
| geom.bool | 并/交/差/异或面积 | ✅ `{or:166, and:0, sub:150, xor:166}` 完全一致 |
| geom.sizing | `sized(+1µm)` | ✅ 84.000 vs 84.000 |
| geom.width_space | width/space 检查计数 | ✅ 1/1 vs 1/1 |
| verify.drc | DRC 规则命中数（Ruby 脚本对照） | ✅ 1/1 vs 1/1 |
| verify.mos | OD∩PO 器件数 | ✅ 2 vs 2 |
| verify.lvs | 版图→提取网表→与原理图图同构 | ✅ MATCH |
| tech.layermap | tsmcN65.layermap 层号 | ✅ M1=(31,0) M9=(39,60)，481 条 |

## 2. 这轮真正修掉的 bug（都是与 KLayout 对跑才暴露的）

| 现象 | 根因 | 修法 |
|---|---|---|
| KLayout 报 `XY record expected` | PATH 里 `WIDTH` 写在 `PATHTYPE` 前面 | 按规范改成 `PATH→LAYER→DATATYPE→PATHTYPE→WIDTH→XY` |
| 阵列数量不对（2×3 变 3×2） | GDS `COLROW`/AREF 三点语义 | 与 KLayout 写出格式对齐：`p1 = origin + cols*spacing` |
| 扁平化计数多 1 | `Layout#flatten` 只把子单元并进 **top**，源单元仍在 | 只统计 top cell |
| width/space 全报违规 | 版图单位是米，规则写成 µm | 规则统一 SI（0.1e-6） |
| 文字/线宽读不回 | 读写器只处理 BOUNDARY | 全量支持 TEXT/PATH/AREF/SREF |

## 3. 还差的（明确标注，不糊）

| KLayout 能力 | 现状 | 说明 |
|---|---|---|
| OASIS 原生编解码 | 🟡 经 KLayout 桥 | `gdsio.write_oasis/read_oasis` 需要本机 KLayout；原生实现未写 |
| GDS 属性（PROPATTR/PROPVAL）、BOX/NODE | ❌ | 读写器只覆盖 5 类元素 |
| `Edges`/`EdgePairs` 全 API | 🟡 | 只有 width/space 的计数与框 |
| 一般多边形 `sized` | 🟡 | 直角精确，斜边按梯形分解近似 |
| Region 的 `interacting/inside/outside` | ❌ | 需要完整布尔引擎 |
| DRC DSL 覆盖面 | 🟡 | width/space/area/enclosure；没有 `tiles`、`clip`、层次化 DRC |
| LVS 器件库 | 🟡 | 只提 MOS（W/L）；R/C/D/BJT 未做；无参数化器件 |
| Tech/LYP/技术栈 | 🟡 | `.map` 可读；`.lyp`、tech XML、stack 未做 |
| RDB 报告 | ❌ | 我们用 JSON/Markdown |
| GUI（`lay.LayoutView`） | ❌ | 用 SVG/HTML 代替 |
| 规模/性能 | ❌ | Python + 稠密算法；KLayout 是 C++ + 分块并行 |

KLayout 的 `klayout.db` 有 **202 个类**；我们覆盖其中约 8 个等价物（Layout/Cell/Shapes/Region/Texts/Instance/LayerInfo/RecursiveShape 的简化版）。
**"绝对兼容"在这条线上成立的范围是：GDS 语义、布尔/面积、DRC 计数、层次展开——这几项现在是逐项相等的。**

## 4. 怎么继续收敛

1. **OASIS 原生读写**（去掉对 KLayout 的依赖）：varint + 记录表 + CBLOCK，约 600 行；
2. GDS 属性记录（PROPATTR/PROPVAL）与 BOX/NODE → 完整 GDS 语义；
3. `Edges/EdgePairs` 全 API + `interacting/inside`；
4. 层次化 DRC（每层 cell 独立算，再合并）——大版图必须；
5. 器件提取扩到 R/C/D/BJT + 参数（W/L/AD/AS…）；
6. 分块并行 + 空间索引（R-tree/网格），把 O(V·E) 降下来。

每加一项，就多一条 `compat.check` 用例——**兼容性是可回归的数字，不是形容词。**
