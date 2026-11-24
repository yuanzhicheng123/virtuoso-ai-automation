# RF / PVT / Monte Carlo 真机验证

日期：2026-11-23；220，Spectre 20.1，TSMC N65。仅用于验收软件调用、原始结果读取及下面明确的数值断言，不是芯片性能签核。

## RF 分析

证据根：`/home/lihong/virtuoso-fusion-20261123/signoff/rf/rf-lj2nn3bj`。
使用真实 `crn65gplus_2d5_lk_v1d0.scs` 的 TT 模型、单个 nch（W=1u/L=60n）。`tools/run_rf_signoff.py` 经 `SpectreSimulator` 执行并读取真实 PSF。

| 项目 | 实测判据与结果 |
|---|---|
| DC/AC/noise/SP | AC 26 点、noise 16 点、SP 11 点，s11/s12/s21/s22 有限，输出噪声为正 |
| Driven PSS/PNoise/PAC | 1MHz、栅极0.6V±0.02V；读回0.58/0.62V；5阶谐波，PNoise/PAC各11频点 |
| HB 对照 | 同一DUT和激励，5阶；漏极基波相对PSS差1.2933224203862673e-6，小于1% |
| PNoise边带收敛探针 | maxsideband 7与14输出噪声逐点相对差0，小于1% |
| 工具状态 | 四个deck均success，数值validation PASS，无Spectre warning |

早期 `rf-zaz6ahgl` 的端口参数和边带警告保留，不计为通过。最终 `rf-report.json` 保存每个deck和模型SHA-256、Spectre结构化结果、原始文件列表与测量值。

限制：这是受迫单管小测试台。未验证自激振荡器收敛、相位噪声/积分抖动、PXF/transient noise、噪声贡献完整性、RF/EM联合提取、系统噪声或目标NF指标。PNoise边带对照不替代这些判据。

## PVT

证据：`signoff/pvt-mc/pvt-mc-g900kink/report.json`。
TT/FF/SS × VDS=1.08/1.2/1.32V × -40/27/125°C，VGS固定0.6V；27个独立Spectre OP全部成功，漏极电流为正且结果可读取，无warning。这里电压扫的是测试台VDS，不代表完整电路的供电与偏置联动，也没有设计规格窗口。

同次尝试的 process+mismatch Monte Carlo 失败：PDK `stat_mis` / `mc` 组合出现 SFE-12，`parn1/parn2/parp1/parp2` 未定义。未修改PDK掩盖问题。该失败不影响分开记录的27个PVT结果，但 **mismatch 不予放行**。

## Monte Carlo

最终证据：`signoff/pvt-mc/pvt-mc-64wkwjml/report.json`；此前 process-only 成功尝试 `pvt-mc-r6mgpw3i` 保留。
使用 `stat` + `mc`、`variations=process`、seed=20261123，两次独立运行，各3个随机样本，另外单独记录 nominal。脚本严格区分随机样本文件和nominal文件，检查随机值彼此有变化、有限且为正，两个运行同seed值完全一致。

随机样本漏极电流（A）：0.0002006503267208607、0.0003250061798221272、0.0002985027734569634。Nominal：0.0002749485120634551。

这验证了真实工艺随机采样与可复现性。3样本不能支持良率、分位数或统计收敛签核；不包含mismatch，也不代表Maestro MC调度已验证（Maestro当前仅配置持久化）。

## 复现

在220工作区、安装环境中执行：

```sh
venv/bin/python source/tools/run_rf_signoff.py --root signoff/rf --model /eda/1P9M/TSMC065_1P9M/models/spectre/crn65gplus_2d5_lk_v1d0.scs
venv/bin/python source/tools/run_pvt_mc_signoff.py --root signoff/pvt-mc --model /eda/1P9M/TSMC065_1P9M/models/spectre/crn65gplus_2d5_lk_v1d0.scs
```

两脚本建立新目录且不修改PDK。`--mc-only`只复验MC，不隐含已在该目录重跑PVT。证据契约检查见本地 `evidence/signoff/noise-contract-result.json`：它仅检查声明的证据完整性，不自行判断电气正确性；上表数值来自独立PSF断言。
