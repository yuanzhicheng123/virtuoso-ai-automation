# AC 小信号分析接入（2026-10-27）

## 一、结论：**AC_SMALL_SIGNAL_VERIFIED**

| 阶段 | 结果 |
|---|---|
| 通过注册入口执行 | ✅ `simulate.run_unified`（经 capability_run 分发） |
| Run A（mag=1, phase=0） | ✅ 51 点，10 Hz–1 MHz，复数数组 `complex128` |
| Run B（mag=0.5, phase=45°） | ✅ 同一频率网格 |
| 复数完整保留 | ✅ re/im 均非零（B 的 Vgs=0.3536+0.3536j） |
| 归一化一致性 | ✅ |Vgs_B/Vgs_A|=0.4999994、|Id_B/Id_A|=0.49999995、gm 相对差 **1.01e-6** |
| 低频数值对照 | ✅ gm_ac=6.03725e-4 S vs gm_dc(1mV)=6.03725e-4 S，相对差 **7.44e-7**；h 减半稳定性 1.35e-4 |
| 负向 | T1 零激励拒绝、T2 非法频率拒绝、T4 复数码本单测通过 |

## 二、关键数值（Run A，10 Hz）
- Vgs = 1 + 0j V
- Id = 6.03725e-4 − 8.47e-15j A（Id = −I(VD:p)）
- **gm_external_ac = 6.03725e-4 S**

## 三、定义与限制
- 测的是外部端口跨导 Id/Vgs，**不是**电压增益、S21 或 LNA 性能。
- AC 幅值是线性化归一化激励，不代表大信号摆幅。
- 频率范围 10 Hz–1 MHz 仅用于接口与低频一致性，不代表 PDK RF 模型认证。

## 四、证据
- `ac_compare.json`（A/B 复数与 gm 对照）
- `gm_dc.json`（DC 斜率对照）
- `tb_acA.scs` / `tb_acB.scs` / `tb_gmdc.scs` / `dut.scs`
- `runs/acA`、`runs/acB`、`runs/gmslope`（PSF 原始结果）
