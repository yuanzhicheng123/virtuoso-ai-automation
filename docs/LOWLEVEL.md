# SKILL 之下：还能往下挖五层

上一轮挖到 SKILL 函数（6,162 个名字）。再往下是**运行时、C++ API、数据格式、进程协议、ELF**。
每层都能挖，挖到的东西解锁不同能力。

## 0. 分层地图（实测数字）

| 层 | 是什么 | 挖到什么 | 实测 |
|---|---|---|---|
| **L1 SKILL 运行时** | 221 个 `.cxt`（编译后的上下文）+ `skill`/`virtuoso` 二进制 | 私有容器格式的头部/符号表 | 头 `00 00 32 00 ac 7f…`，含 `08 07 06 05 04 03 02 01` 标记；`skill` 静态链接（无独立 `libskill.so`） |
| **L2 C++ API** | OpenAccess `liboa*.so`、dfII 各库 | 导出符号 + 反解出的类名/方法 | **2,661 个 `.so`**；`liboaDesign.so` **19,613 符号 / ≥120 类**；`liboaBase.so` **11,058 / ≥120 类** |
| **L3 数据格式** | `.oa` 库、`data.dm`、`techfile`、PSF/PSFXL | magic、字段布局、可读文本 | `.dm` magic `67 45 23 01`；`.oalib` 是 XML；techfile 纯文本；PSF ASCII 已解析，binary PSF 待做 |
| **L4 进程/协议** | skillbridge、`cdsMsgServer`、FlexNet | RPC 协议、句柄语义 | 220 上已有 `skillbridge` 常驻服务（Python 3.6） |
| **L5 ELF/依赖** | 3,864 个 `.so`（去重后 2,661） | 符号、依赖图、版本矩阵 | `libvsacpp` 68,097 符号 / 36.6 MB；`libstpcad` 64,744；`libviva` 46,140 |

## 1. 每层怎么挖

### L1 `.cxt`（编译后的 SKILL）
- `file` 说是 `data`，不是 ELF → 私有容器
- 头部：长度字段 + 指针 + 版本标记 `08 07 06 05 04 03 02 01`
- 挖法：`strings` 拿符号名（已验证，能拿到 `schView` 等）；用 `load()` 喂给 SKILL 观察报错；
  段边界用长度字段递推（头部 4 字节长度 + 4 字节偏移）
- **风险**：字节码/常量池的语义没有文档，只能做"符号级"挖掘，不能反编译

### L2 OA / dfII 的 C++ 符号
```bash
nm -D --defined-only liboaDesign.so | awk '{print $3}' | c++filt \
  | grep -o 'OpenAccess_4::[A-Za-z_]*' | sort -u
```
实测（前 120 个）：`oaBlockage`、`oaBoundary`、`oaFigGroup`、`oaInstTerm`、
`oaBusNetDef`、`oaViaHeader`、`oaAssignment`、`oaScanChain`、`oaConstraint`、`oaProp`…
→ 这就是 OA 的对象模型，和 `skdfref` 的 `db*` 文档一一对应（`dbCreateRect` ↔ `oaRect`）。

**注意命名空间带版本号**：`OpenAccess_4::`，不是 `OpenAccess::`；正则要带 `(?:_\d+)?`。

### L3 数据格式
| 文件 | magic | 可挖 |
|---|---|---|
| `data.dm` | `67 45 23 01` | DM 元数据（库/单元/视图的版本链） |
| `.oalib` | XML | 库属性（DM 系统） |
| `techfile` | 文本 `;***` | 层/用途/via（已解析 94/200/17） |
| `*.psf` | `HEADER`（ASCII） | 已是文本；binary PSF 有独立 magic |
| `sch.oa` | OA 二进制 | 需 L2 的类布局才能读 |

### L4 协议
- 220 上跑着 `skillbridge`（Python 3.6 + SKILL 服务端），协议是 TCP+JSON/字符串；
  直接复用比自己造轮子划算（我们的 `skillproxy` 已经是 JSON-RPC，接一层适配即可）
- `cdsMsgServer`：Virtuoso 的消息总线，用来做 GUI 事件与宏

### L5 ELF
`ldd virtuoso` 列出运行期依赖；`nm -D` 统计符号面；`readelf -d` 看 SONAME/版本。
用途：ABI 兼容矩阵、找"哪个库实现了哪个功能"（比如 `libviva` = 波形、`libvsacpp` = VSA 引擎）。

## 2. 各层解锁什么

| 层 | 解锁的能力 |
|---|---|
| L1 | 不启 GUI 直接加载编译后的上下文（启动更快、依赖更少） |
| L2 | **用 ctypes 直接调 OA**，绕过 SKILL 读写 `.oa`（真正的"直接读版图数据库"） |
| L3 | 自己生成/解析 `.oa`、techfile、PSF binary（后仿结果直接读） |
| L4 | 100% 函数代理（已落地） |
| L5 | 依赖/版本矩阵，判断某个 API 在哪个版本可用 |

## 3. 把"直接读写 .oa"做出来的步骤（L2 落地路线）

1. 从 `liboa*.so` 的符号表恢复类/方法名（已做：≥240 个类名）
2. `ctypes.CDLL("liboaDesign.so")` + `oaInit()` 初始化
3. 关键入口：`oaLib::open` / `oaDesign::open` / `oaCellView::open`（符号名可在 nm 里确认）
4. 结构体布局：头文件没装（**实测 0 个 `oa*.h`**），只能靠文档（`oainstall`/`skdfref`）+ 试错
5. 验收：读一个真实 library，导出 cell/net/inst 与 SKILL dump 一致

**这条路的风险**：没有头文件 = 没有 ABI 契约；升级 OA 版本（现在是 22.61.003）符号会变。
所以更稳的是"符号级调用 + 版本探测 + 回退到 SKILL dump"。

## 4. 现状与缺口（诚实）

| 项 | 现状 |
|---|---|
| OA 头文件 | **0 个**（只有 `vsa.h` 等三个非 OA 头）→ 无法编译 C++ 扩展 |
| OA Python 绑定 | 只有 `oaDebugCDS.py`，**没有**通用 `oa` 模块 |
| SKILL 运行时 | 静态链接，**找不到** `ilEvalString` 一类可嵌入符号 → 只能进程外代理 |
| `.cxt` 反编译 | 未做（私有字节码） |
| binary PSF | 未做（ASCII 已支持） |
| `.oa` 直读 | 未做（依赖 L2 的类布局） |

## 5. 复现

```bash
# 在 220 上跑底层普查
python3 mine/lowlevel.py /eda/cds/IC2310 /tmp/lowlevel.json

# 单看 OA 的类名
nm -D --defined-only liboaDesign.so | awk '{print $3}' | c++filt \
  | grep -o 'OpenAccess_4::[A-Za-z_]*' | sort -u | head
```

产物：`out/mine/lowlevel.json`（库清单/符号数/magic）、`out/mine/oa_classes.json`。

## 6. 一句话

**再往下不是"更多函数"，而是"另一套接口"**：OA 的 C++ 对象模型、私有二进制格式、进程协议。
能挖到"类名 + 符号 + magic"，但**没有头文件就没有 ABI 契约**——所以最优解是
"符号级探测 + 版本回退"，而不是硬啃二进制布局。
