# Phase 2 · M1（网表）+ M2（Spectre）报告

环境：eda-220 · Virtuoso **IC2310** · Spectre **20.1.0.068** · PDK **tsmcN65**（corner tt）
隔离目录：`/tmp/vl_m1`（网表）、`/tmp/vl_phase2`（仿真运行）　证据级：D/R/E/U

## A. 基线核查

| 项 | 结果 | 级 |
|---|---|---|
| Cadence | IC2310（`/eda/cds/IC2310`） | D |
| Spectre | 20.1.0.068 64bit（`/eda/cds/SPECTRE201`） | R |
| PDK | tsmcN65，模型 `models/spectre/crn65gplus_2d5_lk_v1d0.scs`（section=tt） | D |
| License | `Virtuoso Framework License (111)` 可 checkout | R |
| OCEAN | `/eda/cds/IC2310/tools.lnx86/dfII/bin/ocean` | R |
| schCheck (0 4) | **0 error / 4 warning**（返回值）；warning 文本未取得（CDS.log 被后续退出覆盖） | R / U |
| Golden Case | `vlM1/tb/schematic`：1 inst `M0`=`tsmcN65/nch`，4 net（D/G/S/B 被网表器重命名为 net1..4），属性 w=1u l=60n | E |
| Bridge | virtuoso-bridge 0.7.0 **损坏**（namespace 包，无 `__init__.py`）；skillbridge 1.5.1 可用；**无 daemon 监听** | R |
| 注册表口径 | 函数 74,785；文档 9,563；签名 24,267；变量 22,367；未绑定 103,537；已执行 377 | D/R |

## B. M1 网表能力

**流程**（固定模板，不接受任意 SKILL）：
```python
from vskill.phase2.netlist_adapter import export
export("vlM1", "tb", "schematic", workdir="/tmp/vl_m1", host="eda-220")
```
模板仅三个参数（lib/cell/view，正则白名单），其余固定为 OCEAN：
```ocean
simulator('spectre)
design("vlM1" "tb" "schematic")
createNetlist(?recreateAll t)
exit
```

**结果（E）**：
- 产物：`/tmp/vl_m1/simulation/tb/spectre/schematic/netlist/input.scs`（71 行，sha256 `4b922d4f9f488690…`）
- 器件行：`M0 (net4 net2 net1 net3) nch l=60n w=200n m=1 nf=1 sd=200n ad=3.5e-14 …`
- 模型：30 个 `include … section=tt_*`（PDK 内容未输出，仅引用路径）

**Circuit IR**：1 device / 4 nets / 模型 `nch`。

**Semantic Diff（OA ↔ 网表）**：
| 项 | OA | 网表 | 判定 |
|---|---|---|---|
| `w` | 1u | **200n** | ❌ 差 -80% |
| `l` | 60n | 60n | ✅ |
| `m` / `nf` | 未读 | 1 / 1 | 信息项 |
| 器件/端子 | 1 / 4 | 1 / 4 | ✅ |

**结论（E）**：`dbReplaceProp(inst "w" …)` **没有进入网表器**；网表用的是 CDF 参数（默认 200n）。
根因方向：属性命名空间/CDF 参数通道不同，需改用 CDF 参数写接口（下阶段）。

**负向**：库/单元/视图名注入（`bad lib; rm -rf /`）被正则拒绝（单元测试）。

## C. M2 Spectre 仿真

**测试平台**：`audit/2026-10-22/m2/tb.scs`（自建，include PDK tt section，1 个 nch，W=1u/L=60n，VD=1.2V，VG 脉冲 0.6→0.9V，10 ps 边沿）

**结果（E）**：
- 10 次运行 **10/10 OK**，`spectre completes with 0 errors`（2 warnings）
- 结果文件：`op1.dc` / `sw1.dc` / `tr1.tran.tran` / `op1.info`
- OP：`d=1.2 V`、`g=0.6 V`、`VD:p=-274.95 µA`（≈ Id 275 µA）、`VG:p≈0`
- DC sweep（VG 0→1.2，步 0.1）：`|Id|` 0.024 → 2.356 → 66.9 → 274.9 → 555.5 → **1153.6 µA**，单调上升
- Tran：40 ns，`tr1.tran.tran` 64 KB
- 稳定性：10 次运行的工作点数值**逐位一致**（< 1e-12）

**负向（E）**：
| 用例 | 结果 |
|---|---|
| 模型 section 不存在 | ✅ FAILED（spectre 报错） |
| 参数 w=0 | ✅ FAILED |
| 器件行语法破坏 | ⚠️ **spectre 报 0 errors**（器件被忽略、0 器件也能"成功"） |

→ 印证"仿真启动成功 ≠ 指标通过"：必须加**器件计数断言**与 IR 比对。

## D. 稳定性与追踪

- 每次运行有 Run ID（`m2_00` … `m2_09`、`neg_*`），状态 `OK/FAILED/TIMEOUT`
- 超时**不自动重试**（runner 返回 TIMEOUT）
- 结果不明时回读 `run.json` + 原始 PSF
- 10 次端到端回归全部通过且数值一致
- 证据 manifest：`phase2/runs/manifest.json`（输入哈希、命令、日志、结果）

## 交付物

| 交付 | 路径 |
|---|---|
| netlist_adapter | `vskill/phase2/netlist_adapter.py` |
| circuit_ir | `vskill/phase2/circuit_ir.py` |
| netlist_semantic_diff | `vskill/phase2/semantic_diff.py` |
| spectre_runner | `vskill/phase2/spectre_runner.py` |
| psf_results_adapter | `vskill/phase2/psf_results_adapter.py` |
| evidence_manifest | `vskill/phase2/evidence.py` |
| regression_tests | `audit/2026-10-22/phase2/run_phase2.py` + `tests/test_phase2.py` |
| 本报告 | `audit/2026-10-22/PHASE2_M1_M2_REPORT.md` |

## M3（只提方案，不改启动入口）

1. 以 **skillbridge 1.5.1**（或修好 `__init__.py` 的 virtuoso-bridge 0.7.0）在 GUI 会话起 daemon；
2. `vskill.gateway` 不变：仍是白名单 + 风险 + 快照 + 会话锁，只把 `session` 换成持久会话；
3. 并发：按 `(lib, cell, view)` 加锁；超时只回读不重试写操作；
4. 会话生命周期：一 Run 一会话标签，证据里记 `session_id`。

## M4（只读核查）

- `xlUI.cxt` / `xlUI.ini` 存在；`pxl`、`BOXLAY` 二进制存在 → **XL 上下文在安装内**
- XL 许可证未验证（无 lmstat 授权探测）
- 本轮**未做任何版图修改**

## 缺口

1. `w` 写入未进网表（CDF 参数通道）——下阶段首要修复；
2. 网表网络名依赖 label/port（当前被重命名为 net1..4）；
3. `schCheck` 4 条 warning 文本未取得；
4. Tran PSF 未做数值断言（只确认文件生成）；
5. ADE/Maestro、daemon、Layout XL 均未执行。
