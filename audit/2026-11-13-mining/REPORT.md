# 签名挖掘：从 32.1% 向全覆盖推进（2026-11-13）

## 一、已验证可行的挖掘路径
在真实 Virtuoso 会话里对函数名调用 `(arglist nm)`，把结果写成 `name|arglist` 文本。

**试点（随机 3000 个无签名函数）：`ok=2280 / 3000 = 76%`** —— 真实运行得到，非估算。
按此比例，50,765 个无签名函数可补齐约 **38,600** 个 → 覆盖率从 **32.1%** 提升到约 **84%**。

证据：`pilot_arglists.txt`（2280 行，`name|(arglist)` 格式，0 行畸形）。

## 二、全量运行遇到的三个 SKILL 事实（本轮实测，未解决）
| 尝试 | 现象 | 结论 |
|---|---|---|
| 单行 `'(a b c …50765 个)` 字面量 | `syntax error at line 5 column 1155278` | 单行长度超限（1.18 MB） |
| `lineread` 读 names.txt | `syntax error at line 49457`；`intern`/`parseString` 报参数类型错 | **`lineread` 会把每行当 SKILL 解析**，遇到 `CbarManager()` 这类名字即崩 |
| `fscanf "%s"` + `parseString`→`arglist` | 读完 50,780 token，但 **ok=0** | `fscanf` 正常；但 `parseString` 的结果不能直接喂 `arglist`（根因未确认：疑为返回 string 而非 symbol） |

## 三、结论与下一步（明确）
- **可行路径已验证**（试点 76% 成功率），只差把"分批字面量"工程化：
  每批 ~2000 个符号生成一个 `.il`，跑 26 批即可覆盖全部；
- 不做：`lineread`/`parseString` 这条路（已被证伪）；
- 完成后合并进 `api_registry_v2.json`，重算 `coverage()`，并把结果接入 `skillcall`。

## 四、证据
`pilot_arglists.txt`（2280 条真实 arglist）
脚本：`/tmp/arglist_pilot.il`（成功）、`/tmp/arglist_full{,2,3,4,5}.il`（失败尝试）
