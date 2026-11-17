# M2.1 电气语义一致性与防假成功 · 报告

基线：`vskill/phase2/`、`registry/`、`vskill/gateway.py`；隔离库 `/tmp/vlW*`、`/tmp/vl_m*`。
证据：`audit/2026-10-22/m21/evidence.json`、`phase2/runs/*`。
标记：**E** 实际执行 / **R** 运行时观察 / **U** 未执行。

## 一、宽度偏差定位（w=1u → 网表 200n）

### 1.1 采集到的实例状态（E）

| 项 | 值 |
|---|---|
| 实例创建后 `inst~>w` | `"200n"`（CDF 默认） |
| 创建后 `dbGetPropByName(inst,"w")` | **none**（实例上还没有属性） |
| CDF 参数 `w` 类型 | `string` |
| CDF 参数 `w` 默认 | `200n` |

### 1.2 三条写入链的对照（E）

| 写法 | 会话内读回 | 保存重开后 | 网表 |
|---|---|---|---|
| A `dbReplaceProp(inst,"w","string","1u")` | `1u` | `1u` | **200n** |
| B `inst~>w = "1u"` | `1u` | `1u` | **200n** |
| C `cdfFindParamByName(...)~>value="1u"` + `cdfUpdateInstParam` | `1u` | CDF 值 `1u`、属性 `1u` | **200n** |

**根因（部分确认，E）**：写入链本身是通的（三种写法都能持久化），
但**网表器最终用的是 200n**——说明它读的不是实例属性、也不是实例 CDF 覆盖值，
而是**单元级 CDF 默认/派生值**（`sd=200n`、`ad/as=3.5e-14`、`nf=1` 全部与默认一致）。
"缺少 callback"这一假设**不成立**：`cdfUpdateInstParam` 返回 `t`，callback 已执行。

### 1.3 电气影响量化（E）

| 网表 | Id @ VD=1.2V, VG=0.6V |
|---|---|
| 自动网表（w=200n） | **54.586 µA** |
| 人工参考（w=1u） | **274.948 µA** |
| 比值 | **5.04×**（与 1u/200n 一致） |

→ 参数写链缺陷直接造成 **5 倍电流偏差**；这正是否决"创建成功=电气正确"的实证。

### 1.4 剩余动作（U）

- `cdfGetCellCDF(ddGetObj "tsmcN65" "nch")` 读单元级 CDF 参数表与 netlist 表达式；
- 确认网表器取参的准确入口（`cdfGetParamValue` / `simInfo` 表达式），再修写入链；
- 修复后需重跑：保存 → 重开 → 网表 → 仿真四步一致。

## 二、M1→M2 来源链（E）

| 环节 | 文件 | 说明 |
|---|---|---|
| DUT 网表 | `/tmp/vlW4/.../input.scs` | 自动导出，含 `M0 (net4 net2 net1 net3) nch …` |
| TB 封装 | `audit/2026-10-22/m2/tb.scs` | 手工参考，含电源与 op/dc/tran |
| 端口映射 | `net4/2/1/3` ↔ `D/G/S/B` | 由器件端子顺序确定，**不依赖 net1/net2 命名** |
| 最终输入 | 上述 tb.scs | 未对 DUT 参数做任何字符串替换 |

网络比较按**实例端子连接关系**（IR 的 `terminals` 顺序），不是名字。

## 三、独立验收（E）

`vskill/phase2/acceptance.py` + `tests/test_acceptance.py`（7/7 通过）：

| 检查 | 能否抓到 |
|---|---|
| DUT 存在 | ✅ 删掉 M0 后 `dut_present=false` |
| 模型身份 | ✅ 期望 `nch` |
| 参数一致 | ✅ `w` 期望 1u 时报 mismatch |
| 分析完整 | ✅ 缺 `ac` 时 `missing=["ac"]` |
| 结果来源 | ✅ 网表哈希不符时失败 |
| 数值断言 | ✅ min/max 规则 |

三级状态：`PROCESS_FAILED / EVALUATION_INVALID / EVALUATED_UNVERIFIED / METRICS_MET / METRICS_FAILED`。

## 四、检查证据（E/部分 U）

| 项 | 结果 |
|---|---|
| `schCheck` | tb `(0 4)`、tb2 `(0 4)`、vlW4 `(0 4)` |
| Spectre 警告数 | 手工 tb **2**；自动网表 **3** |
| 警告文本 | 在 `spectre.log` 中（未逐条分类，**不豁免**） |
| Tran 断言 | **未执行**（仅确认 `tr1.tran.tran` 生成） |

## 五、M3 独立验证（R，未实测）

skillbridge 1.5.1 代码事实：
- 客户端 `channel.py`：`sock.settimeout(1)`→`None`；收到 `'<timeout>'` 时不重试，提示调大服务端超时；
- 服务端 `python_server.py`：`read_from_skill(timeout)` / `data_ready(timeout)` → 返回 `'failure <timeout>'`；
- **没有发现队列/锁** → 并发调用会共享同一 socket，必须由调用方串行化。

设计（未实施）：会话级串行队列 + `(lib,cell,view)` 对象锁；超时后**冻结写入**，
先恢复通信并回读状态再决定下一步；禁止在失步连接上回读或盲重试（`gateway.py` 已有会话锁与"不自动重试"骨架）。

## 六、对抗回归（E）

| 用例 | 结果 |
|---|---|
| DUT 被删 | ✅ 检出 |
| 错误模型 | ✅ 检出（模型身份检查） |
| 旧 PSF/来源不符 | ✅ 哈希不符即失败 |
| 缺分析 | ✅ 检出 |
| 参数未生效 | ✅ `w` mismatch 检出（并用 5.04× 电流差量化） |

## 七、剩余阻断项

1. 网表取参入口未定位（单元级 CDF / netlist 表达式）——**修复 w 的前提**；
2. `schCheck` 4 条 warning 文本未分类；
3. Spectre 警告未逐条豁免/确认；
4. Tran 断言未执行；
5. skillbridge 并发/超时未实测（无 daemon）；
6. Layout XL 未做。
