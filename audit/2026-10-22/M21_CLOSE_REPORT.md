# M2.1 关闭报告：参数写链根因 + 验收加固

环境：eda-220 / IC2310 / tsmcN65 nch　隔离库：`/tmp/vlW5`、`/tmp/vlW6`
证据：本目录 `m21/evidence.json`、`phase2/runs/*`、`/tmp/vlW*/CDS.log`

## 一、已确认事实（E：实际执行）

### 1.1 根因：网表用的是 `wf`，不是 `w`

`cdfGetInstCDF(inst)~>simInfo` 中 spectre 段的 `propMapping`：
```
propMapping (nil m simM nf fingers w wf)
```
→ `w` 映射到 **`wf`**（每指宽度）。

三实例对照（同一 Cell、同一 netlist 过程）：

| 实例 | 写入 | 网表结果 |
|---|---|---|
| `M_W` | 仅 `w="1u"` | **w=200n**（未生效） |
| `M_WF` | 仅 `wf="500n"` | **w=500n** ✅ |
| `M_BOTH` | `w="1u"` 且 `wf="1u"` | **w=1u** ✅ |

### 1.2 往返与跨进程（E）

`vlW6/tb`：`wf=500n` → 网表 `w=500n`；改 `wf=800n`（新进程写入+保存）→ 网表 `w=800n`；
改回 `wf=500n` → 网表 `w=500n`。三次净表分别在独立 OCEAN 进程中完成。

### 1.3 采样到的事实（E）

| 项 | 值 |
|---|---|
| 库真实路径 | `/tmp/vlW4/vlW4` |
| `createNetlist` 返回路径 | `/tmp/vlW4/simulation/tb/spectre/schematic/netlist/input.scs` |
| 后续读取路径 | 同一路径（一致） |
| 实例属性 | `inst~>w="1u"`、`~>l="60n"`（写后） |
| 实例/单元 CDF `~>param` | **长度 0**（但 `cdfFindParamByName` 可用） |
| 单元 CDF `w` 值 | `200n` |
| 写后重开 | CDF 值 `1u`、属性 `1u`（持久化成立） |

## 二、分支排除（E）

| 假设 | 证据 | 结论 |
|---|---|---|
| 缺 callback（`cdfUpdateInstParam` 假成功） | 调用返回 `t`；重开 CDF 值=1u | **排除** |
| 属性没持久化 | 重开后 `inst~>w`=1u、`dbGetPropByName`=1u | **排除** |
| 网表读单元级 CDF 默认 | 单元 CDF w=200n，但写 `wf` 即可改变网表 | **排除**（读的是 wf） |
| 网表端口/路径错位 | 返回路径与实际读取路径一致 | **排除** |
| **网表参数是 `wf`** | 三实例对照 + 往返 | **确认** |

## 三、实际修复（E）

写链改为：
```skill
(inst~>wf = "500n")       ; 网表参数（生效）
(inst~>w  = "500n")       ; 显示参数（同步）
(cdfUpdateInstParam inst)
(schCheck cv) (dbSave cv)
```
代码契约：`vskill/phase2/params.py`（`set_width_skill` / `contract`，含已验证与未验证项）。

**未修改任何 PDK 默认值，未替换网表字符串。**

## 四、回归结果

| 用例 | 结果 |
|---|---|
| 非默认参数（500n/800n/1u） | ✅ 网表跟随 |
| 双实例隔离（同 cell 内 M_WF vs M_W vs M_BOTH） | ✅ 各自独立生效 |
| A→B→A 往返（500→800→500，跨进程） | ✅ |
| 保存/重开 | ✅（重开后 CDF/属性一致） |
| 全新进程重新网表化 | ✅（三次净表均在新进程） |
| GUI 参考对照 | **未执行**（无 GUI 交互会话） |
| `nf>1` 时 `w = wf*nf` | **未执行** |

## 五、验收加固（E）

`vskill/phase2/acceptance.py` 新增：
- `completeness()`：必选检查（dut_present/params/analyses/result_source）缺失、`ok=None`(UNKNOWN)、`NOT_RUN`、空指标集合 → **一律不通过**；
- `param_gate()`：参数不一致 → 返回 `DIAGNOSTIC_ONLY`，仿真数值只作诊断，不作设计验收；
- `run_all()` 在完整性/参数闸门未过时直接给 `EVALUATION_INVALID` / `DIAGNOSTIC_ONLY`。

测试：`tests/test_acceptance.py` 9/9 通过（含 completeness 与 param gate）。

## 六、警告与 Tran（部分未执行）

| 项 | 状态 |
|---|---|
| schCheck 计数 | tb `(0 4)`、vlW5 `(0 12)`（3 实例） |
| schCheck 文本 | **未取得**：`CDS.log` 只有 `Errors: 0 Warnings: 0` 汇总与 Qt 崩溃栈，无 schCheck 消息 → **不豁免，标记为未知** |
| Spectre 警告数 | 手工 tb 2 条、自动网表 3 条（日志在 `runs/*/spectre.log`） |
| Spectre 警告文本分类 | **未逐条处理**（阻断项） |
| Tran 断言（时间轴/信号/有限值/行为） | **未执行**（仅确认 `tr1.tran.tran` 生成） |

## 七、skillbridge 故障注入（未执行）

无常驻 daemon（skillbridge 未启动），按要求只在独立测试会话中做；本轮**未执行**，设计已定：
1. 发送前断线：调用前 kill 连接 → 断言 `PROCESS_FAILED` 且**不重试**；
2. 保存后丢响应：服务端在 `dbSave` 后丢弃响应 → 客户端冻结写入，重连后**先回读**；
3. 执行超时：服务端 `read_from_skill(timeout)` 返回 `'failure <timeout>'` → 标记 `TIMEOUT`，禁止盲重试。

## 八、剩余阻断

1. schCheck 4/12 条 warning 文本（需 CIW 日志接口）；
2. Spectre 警告逐条分类；
3. Tran 行为断言；
4. `nf>1` 宽度关系；
5. GUI 交互净表对照；
6. skillbridge 故障注入（需 daemon）。
