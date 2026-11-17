# OCEAN 原生 SP 读取（2026-10-28）

## 一、读取器：**PASS**；测试台有效性：**FAIL**；注册入口新运行：**NOT_RUN**

### getData 报错的真实定位
- 在同一个 OCEAN 进程里实测：`getd 'getData` = **t**、`getd 'v` = **t**、`getd 'spm` = **t**
  → 之前"nograph 没有 getData"的结论**不成立**；
- 之前的失败是**调用/命名问题**（信号名用了 `S_1_1`），不是函数缺失；
- 原失败根因：**未完全确认**（只确认不是"函数未定义"）。

### 实际采用的读取接口（本机文档已核验）
`spm(s_ana x_index1 x_index2 [?port1] [?port2])` → 复数波形：
```skill
openResults("<results dir>")
w11 = spm("sp" 1 1)   ; S11；w12=spm("sp" 1 2)；w21=spm("sp" 2 1)；w22=spm("sp" 2 2)
y = drGetWaveformYVec(w11)   x = drGetWaveformXVec(w11)
drGetElem(y k) → complex(real imag)
```
导出：`/tmp/sp_export.csv`（51 行 + 表头，列为
`freq_hz,s11_re,s11_im,s12_re,s12_im,s21_re,s21_im,s22_re,s22_im`）。

## 二、数据完整性（已校验）
| 项 | 结果 |
|---|---|
| 点数 | 51（与 SP 扫描 10 Hz–1 MHz / dec=10 一致） |
| 频率轴 | 严格单调、有限、正值 |
| 四个波形 | 全部取得（`@@w=t t t t@@`） |
| 数值 | 全部有限，无 NaN/Inf |
| 名称映射 | 按名称建矩阵，顺序无关（T2 通过） |
| 截断检测 | 少一行即拒绝（T3 通过） |

## 三、测试台有效性检查：**FAIL（端口实际开路）**
导出值：`S11 = 1+0j`、`S22 = 1+0j`、`S21 = 0`、`S12 = 0`（全部 51 点）。

这三组值对应"两个端口各自开路、互不连通"，与 `schCheck=(0 16)`（16 条浮空）一致：
**端口/源与 DUT 的连接没有真正建立**（仅 `dbCreateConnByName` 不足以形成导线连通，
与 R2 已确认的结论一致）。

→ 因此：
- 读取链**可用**（能取到真实复数矩阵）；
- 但当前 SP 数值 **DIAGNOSTIC_ONLY**，工程测量有效性 **false**；
- G2（经注册入口跑新的原生 SP）**不执行**：先修 OA 测试台，避免把开路夹具的 S 参数当成器件响应。

## 四、证据
- CSV：`audit/2026-10-28-sp-reader/sp_export.csv`
- 校验：`reader_validation.json`
- 负向：`t3_truncated.csv`（截断即拒绝）
- 脚本：`/tmp/ocn_x.ocn`（读取+导出）、`/tmp/ocn_spm.ocn`（接口绑定诊断）
- 源运行：`/tmp/sbTB/vlSPTB/simulation/tb/spectre/schematic`（read_existing，非新仿真）

## 五、下一步（唯一）
修 OA 测试台：用 `schCreateWire` 把 P1/P2/vsource 真正连到 M0 的 G/D/S/B
（复用 R2 已通过的做法），使 `schCheck` 浮空归零；随后再经注册入口跑一次 SP。
