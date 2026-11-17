# GUI 解锁：`deOpenCellView` 打开窗口后，函数从 48,198 涨到 74,785

## 1. 卡了几轮的问题，根因是找错了函数

| 尝试 | 结果 | 原因 |
|---|---|---|
| `loadContext("schView")` | `schCreateWire` 仍为 nil | 不是加载上下文的入口 |
| `deOpen(cv)` / `hiOpenWindow(cv)` | 无效 | **`deOpen` 是"打开文件"**（`deOpen(fileSpec 'interactive)`），`hiOpenWindow` 是"打开应用窗口" |
| **`deOpenCellView(lib cell view viewType winSpec mode)`** | ✅ 返回 `window:2`，`schCreateWire` 立即变 t | 这才是"在窗口里打开一个 cellview" |

签名（来自 `skdfref/dm_re_deOpenCellView.html`）：

```skill
deOpenCellView( t_libName t_cellName t_viewName t_viewType g_winSpec t_accessMode )
  => w_windowId / nil
```

## 2. 实测（eda-220，Xvfb + GUI Virtuoso）

| 会话 | 函数数 | `schCreateWire` | `leHiCreateRect` |
|---|---|---|---|
| `virtuoso -nograph` | 48,198 | ❌ | ❌ |
| GUI + **schematic 窗口** | **56,512** | ✅ | ❌ |
| GUI + **layout 窗口** | **71,647** | ❌ | ✅ |
| **三环境并集** | **74,785** | ✅ | ✅ |

新增家族（schematic 窗口）：`asi` **2,150**（仿真控制）、`sch` **526**（含 `schCreateInst`/`schCreateNetExpression`）、
`schHi*` 68、`ocn` 47。
layout 窗口再补 **23,449** 个（`leHi*`、`le*` 交互）。

## 3. 现在的库

```
函数总数（并集） 74,785
vskill 表        75,487（含少量文档独有）
schCreateWire / schCreatePin / leHiCreateRect  全部在表里
```

## 4. 工具层同步更新

新增两个 GUI 专属工具（`tags=("gui",)`）：

| 工具 | SKILL | 前置条件 |
|---|---|---|
| `vl.sch.wire` | `schCreateWire(cv net "full" points)` | GUI 会话 + `deOpenCellView` |
| `vl.sch.pin` | `schCreatePin(cv name dir nil point orient)` | 同上 |

调用前会检查 `getd('schCreateWire)`，不在就明确报"需要 GUI 编辑窗口"，不会静默失败。

## 5. 复现（一条命令）

```bash
Xvfb :77 -screen 0 1280x1024x24 &
cd $TMP && cat > .cdsinit <<'EOF'
(setShellEnvVar "CDS_LIB" "./cds.lib")
load("/tmp/dump.il")
EOF
DISPLAY=:77 virtuoso
```

`dump.il`：
```skill
(ddCreateLib "vlPy" "./vlPy")
(dbOpenCellViewByType "vlPy" "inv" "schematic" "schematic" "a")
(deOpenCellView "vlPy" "inv" "schematic" "schematic" nil "a")   ; ← 关键
(sleep 3)
(let (p s) (setq p (outfile "/tmp/fns.txt"))
  (foreach s oblist (when (getd s) (fprintf p "%s\n" s)))
  (close p))
(exit)
```

## 6. 还差的

| 项 | 说明 |
|---|---|
| `leHi*` 的 arglist | 编辑器函数多在 `-nograph` 之外，签名没 dump 过；下一轮补 |
| GUI 会话的常驻 | 现在每次 dump 起一个进程；要变成工具需 daemon（vcli/virtuoso-bridge） |
| `asi*` 2,150 个仿真控制函数 | 已进清单，未验证；这是"跑仿真"的入口 |
| L4 与界面等价 | 有了窗口，截图比对可以开始做了 |
