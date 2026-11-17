# 从 Cadence 里把 SKILL 函数挖全：运行时枚举

之前只有文档里的 6,153 个名字。这一轮换方法：**在活的 SKILL 会话里直接枚举符号表**。

## 1. 方法（已实测可行）

```skill
;; oblist = 运行时全部符号（116,243 个）
;; getd   = 取符号的函数定义：primop:car / funobj:myProc，变量和未定义返回 nil
(let (p n s)
  (setq p (outfile "/tmp/vl_functions.txt"))
  (setq n 0)
  (foreach s oblist
    (when (getd s) (fprintf p "%s\n" s) (setq n (add1 n))))
  (close p)
  (printf "@@count=%d@@" n))
```

工具：`mine/skill_runtime.py`（在 EDA 机器上跑，任意 `skill`/`virtuoso` 都行）。

## 2. 实测数字（eda-220，Virtuoso IC2310，`-nograph`）

| 指标 | 数量 |
|---|---|
| 运行时符号（`oblist`） | **116,243** |
| 其中有函数定义（`getd` ≠ nil） | **48,198** |
| 公开函数（不带 `_` 前缀） | **15,011** |
| 内部/私有（`_ci`、`_cp`、`_vr`…） | 33,187 |
| 文档挖到过 | 6,153 |
| 文档 ∩ 运行时 | **4,587** |
| **运行时独有（文档里没有）** | **43,611** |

按家族（公开为主）：`sch` 1,084 · `hi` 1,007 · `db` 949 · `ge` 652 · `pc` 578 ·
`dd` 380 · `tech` 356 · `le` 267 · `vfo` 154 · `dr` 149 · `rod` 89 · `cdf` 43。

合并进库后：**16,577 个名字**（文档 6,153 + 运行时新增 10,424），
`vskill` 的 Python 表同步到 16,549 个。

## 3. 关键发现：`-nograph` 是**裁剪环境**

`schCreateWire` / `schCreatePin` / `schCreateWireLabel` **不在** 这个 48,198 里——
它们在 GUI 上下文（`schView.cxt` 等）里。这正好解释了上一轮为什么画不了线：
不是参数写错，是**函数根本不在这个环境**。

同理 `leHiCreateRect`（版图编辑器）也不在。

结论：**要挖全，必须在多个上下文里各跑一次枚举**：

| 环境 | 覆盖 |
|---|---|
| `virtuoso -nograph` | 48,198（数据面 `db*`/`tech*`/`cdf*`/`rod*` 齐全） |
| GUI 会话（带显示/Xvfb） | 加上 `sch*`/`le*`/`hi*` 的编辑器上下文 |
| 各工具（ADE/XL/Assura…） | 各自 `.cxt` 加载后再次枚举 |

## 4. 可复现

```bash
# 1) 在 EDA 机器上枚举
python3 mine/skill_runtime.py --binary virtuoso --out /tmp/fns.txt

# 2) 取回并合并（本地）
python3 - <<'PY'
import json
rt = {l.strip() for l in open('/tmp/fns.txt') if l.strip()}
pub = {n for n in rt if not n.startswith('_')}
idx = json.load(open('vlight/_data/skill_index.json'))
for n in pub:
    idx['functions'].setdefault(n, {"syntax": "", "args": [], "manual": "runtime"})
idx['count'] = len(idx['functions'])
json.dump(idx, open('vlight/_data/skill_index.json','w'))
print(len(rt), len(pub), idx['count'])
PY

python3 tools/gen_vskill.py
```

## 5. 下一步（把"全"做实）

1. **GUI 上下文枚举**：起一个带 Xvfb 的会话，重复 `oblist + getd`，把 `sch*`/`le*` 收进来；
2. **每个 `.cxt` 单独枚举**：`load` 上下文后 `oblist` 求差，得到"哪个工具提供哪些函数"；
3. **给每个函数打标签**：从 `getd` 的结果（`primop:` / `funobj:` / `macro`）拿到类型，
   再结合文档索引补签名；
4. **真机用例覆盖**：每挖到一个常用函数，就补一条 live 用例（现在有 11 条）。

## 6. 边界

- 枚举到的是**这个版本、这个会话**的函数；换版本/换工具会变。
- `getd` 只说明"有定义"，不说明"能调用成功"（还要正确的参数类型）。
- 内部函数（33,187 个，`_` 前缀）不应暴露给用户，但保留在清单里做审计。
