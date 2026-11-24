> 本文为0.2.1历史完整审计；0.3.0最新状态及上游提交见 [STATE.md](STATE.md)。

# 全库公开接口与固定上游验证盘点

日期：2026-11-23。本文件为覆盖审计证据；最终放行状态以 `STATE.md` 为准。静态存在、离线通过、真机调用和芯片设计签核是不同结论，不能互相替代。

## 固定上游完整回归

对象为 `virtuoso-bridge-lite` 提交 `3f0bd7d46ca3c5d2c750cea0894bbdefc7b67b9d`。此次执行全部 `tests`，不是此前 645 项选定子集；上游原检出源码未修改。

最终原样上游运行使用 git archive 副本（`tar.umask=0022`），专用无 system-site-packages 环境 Python 3.11.5 / pytest 9.2.0、`TMPDIR=/tmp`、进程 umask 022。pytest 从该固定副本的 `src` 导入实现。wheel 与 sdist 在同内容副本使用 setuptools 84.2.1 构建，并启用上游 distribution 检查。

| 范围 | 原样上游结果 | 证据 |
|---|---|---|
| 全套 pytest，含分发检查 | **2902 passed，126 skipped，1 failed** | `evidence/full-validation/upstream-clean.log`、`.xml` |
| 原生 registry/protocol，真实进程、socket、flock | 19 passed | `upstream-native-registry-tmp.log` |
| 原生完整 daemon 启动竞争 | 2 passed，3 skipped | `upstream-native-concurrency.log` |
| 原生 X11 关闭行为，专用真实 Xvfb | 19 passed | `upstream-native-x11.log` |
| 上游 wheel/sdist 包文件逐项内容检查 | 2 passed | `upstream-dist-modern-tests.log` |
| 专用依赖环境一致性 | `uv pip check` 通过 | 环境列表见 `upstream-summary.json` |

126 项 skip：95 项 opt-in Cadence/IPC/GUI 真机检查，14 项 Python 2.7，16 项 macOS/Windows，1 项特定不支持宿主检查。另原生 concurrency 的 3 项需要真实双 UID；未新建系统账户，未把单用户模拟视为双用户验证。Python 2.7 worker、Windows 和 macOS 未执行。本轮 220 的 Fusion 0.2.1 组合真机回归为 22/22、零跳过，另有 RF/PVT/MC 与 SSH 验证；这些结果单列，不能替代所有上述上游 opt-in 案例。

保留的原始失败及最小复现：

- `CadenceEnvironment.wrap` 在本机 `/usr/bin/bsd-csh` 下，selector 的 `echo selector-banner` 泄漏到命令 stdout；期望只返回目标命令输出。最小复现为 `test_cadence_env.py::test_default_csh_selector_preserves_quoted_paths_and_values`。这是上游运行时路径，`SpectreSimulator` 和 CLI 实际使用该环境封装；当前只证明输出通道问题，没有证明数值结果错误。最终 clean 全套唯一失败就是此项。**220 的 csh 同例正常**：目标输出在 stdout，selector-banner 在 stderr，见 `220-csh-probe.txt`；不能把本机 bsd-csh 问题泛化为 220 故障。
- 本机旧安装依赖资源模式为 `0664`。`SSHClient.ensure_local_setup` legacy 分支通过 `shutil.copy2` 保留该模式；启用自动实例发现后，`ramic_deploy._read` 拒绝 group-writable 部署文件。最小复现 `reproduce_authorized_migration.py` **明确调用 `allow_setup_replacement()`**，只作用于本次 `/tmp` 私有 fixture；`local-authorized-migration.json` 仍为 `Unsafe deployment file`，确认限制没有消失。**220 安装资源为 `0644`，显式允许替换后迁移通过**（`220-authorized-migration.json`）。新本机环境安装和部署均使用进程 umask 022，也迁移通过（`fresh-022-migration.json`）。未允许替换时的 `bundle differs`/`--replace-setup` RuntimeError 是正常保护，不能报为缺陷；早期 `installed-migration.json` 未授权替换，只作原始观察保留，最终判断以上述授权复现为准。`FusionClient.from_env` 单纯构造尚不部署，后续 warm/start 才到该路径；已验证的显式 endpoint 与 isolated runner 不走 legacy→auto 迁移。**受控 0644 环境通过不表示旧 0664 安装/部署自动修复，也不保证任意安装 umask**。没有改变既有用户部署、vendor 源码或权限守卫。
- 初始默认 `TMPDIR=/home/lihong/.cache/tmp` 的 0775 祖先导致私有目录守卫拒绝；仅给验证进程选 `/tmp` 后对应失败消失，没有 chmod 现有目录或放松守卫。
- `fusion-dev` 的旧 pytest 7.4 将两模块的辅助 `setup` 当作 nose setup，造成 42 个 setup error；当前 pytest 9 环境消失。旧 setuptools 65 构建 sdist 名为 `virtuoso-bridge-1.0.0.tar.gz`，而上游测试硬编码下划线；现代构建验证通过。均保留原始日志，不计为产品逻辑修复。

## 公开表面

`public-surface.json` 已由新环境安装的 **0.2.1 wheel**（`fusion-full-install-mk_n1qn9`），通过隔离模式 `python -I` 重新反射生成，记录完整方法名、签名和定义类，防止将继承接口遗漏。`audit_public_surface.py` 可复跑。它只做实例构造与反射，没有连接 Cadence。数量只用于盘点，不是执行成功率。

| 表面 | 当前直接公开方法数 | 说明 |
|---|---:|---|
| FusionClient | 37 | 包含继承连接、文件/脚本、窗口、文档、SKILL 操作 |
| library / schematic / layout / symbol | 14 / 11 / 4 / 5 | create/modify 返回 editor，几何功能由 builder 函数传入 add |
| maestro | 51 | Fusion 重写 read_results 保留多 corner 字段，其余继承 |
| sos / dialogs / requests | 11 / 8 / 1 | 工作区版本管理、弹窗授权和不确定请求回执 |
| oa / generic / registry | 3 / 4 / 6 | OA复制/导出、通用编码与注册表查询；generic.executor 为绑定 callable 槽位 |
| SpectreSimulator / SpectrePool | 10 / 3 | 独立仿真、任务池与并发入口 |
| PhysicalVerification | 3 | run、runner 注册、parser 注册 |

另已盘点 layout/schematic/symbol 模块公开 builder/reader/parser 函数，及 Editor 公开方法；详见 JSON。客户端 facade 方法数量不涵盖全部 SKILL builder，更不能代表 Cadence 原生函数全集。

## 真机覆盖与剩余验证边界

本轮 0.2.1 最终组合真机在 220 `signoff/full-021-suite/live-e687kd6s` 完成 **22/22 passed、零跳过**，覆盖原 17 项及新增 workbench 5 项。新增 workbench 独立运行也为 5/5（`signoff/full-021-workbench2/live-88zpo7q2`）。RF 四组探针在 `signoff/full-021-rf/rf-55dr86bi` 重跑通过；27 点 PVT 与 3 个 process MC 样本及同 seed 重复在 `signoff/full-021-pvt-mc/pvt-mc-7fjz8koy` 重跑通过。真实 OpenSSH 与 Paramiko 两后端连接 220 的传输哈希均通过，见 `ssh-transports.json`。最终汇总以 `STATE.md` 为准。

| 本轮证据 | 内容 |
|---|---|
| `live/live.xml`、`live/result.json` | 最终 22 项组合真机及 owned 进程清理 |
| `rf-report.json`、`pvt-mc-report.json` | RF、27 点 PVT、process MC 及交叉核对 |
| `ssh-transports.json` | OpenSSH / Paramiko 实际 220 传输哈希 |
| `runtime-coverage-summary.json` | 当前加载上下文绑定 118193 个，注册表匹配 74783、缺 43410，QUERY_ERROR=0 |

函数普查的分母随已加载上下文变化；上轮与本轮数量不能解释成能力增减或函数执行成功率。注册名匹配也不等于实际执行这些函数。下表按最新真机证据刷新，“尚缺”并非接口必然不可用。

| 模块 | 已有项目真机证据 | 离线或尚缺真机证明的范围 |
|---|---|---|
| generic / 会话 | 认证身份、序列调用、同客户端多线程、错误恢复、标量/列表/Symbol参数 | 74785 注册名未逐个实际执行；OA句柄、所有参数/上下文/版本/许可证组合未覆盖 |
| library / schematic | scratch库tech绑定、库重命名/删除、category增删改与成员关系；RC原理图、CDF参数、check/save、原生网表与DC/AC闭环 | 复杂层级/config、SPICE导入、manifest/planner全部路径、所有PDK callback未覆盖 |
| layout / oa / GDS | 矩形、group transform、N65 PCell尺寸、重开、OA复制、XStream与oa2strm实际层映射 | 全部via/mosaic/polygon/选区/BBox修复、任意层级复制、其他PDK/格式未完整验证 |
| symbol | 新增真实符号端口、语义标签、termOrder与读回验证 | 全部符号图元、生成/更新策略及复杂层级端口组合未逐一真机验证 |
| maestro | GUI保存重开、2 sweep×2 corner的4个实际RC运行点；多corner读回对照RDB | MC配置保存重开仅CONFIG_ONLY；ADE实际MC、全部引擎/历史/波形导出/迁移/分布式job组合未覆盖 |
| Spectre / pool | 0.2.1重跑OP/DC/Tran、AC/noise/SP、PSS/PNoise/PAC/HB、27点PVT，3个process MC样本同seed复现；新增两个SpectrePool实际作业独立输出，1V/2V结果正确 | mismatch模型失败；3样本不是良率收敛；振荡器phase noise、系统jitter/完整NF指标及全分析组合未覆盖 |
| physical | 真DRC 1737规则/29结果正确不放行、单管LVS CORRECT、PEX三阶段/6C/0R | 电路级带端口LVS、非平凡R网络、多corner后仿、DRC-clean设计及全芯片规格签核未完成 |
| dialogs / requests | 真实GUI空闲检查、X11 harness；真实执行超时后查询原request receipt，副作用计数保持1（exactly-once探针） | 用户GUI授权全流程、跨主机不确定请求恢复的完整故障矩阵未覆盖 |
| SOS | 上游离线测试；220未发现soscmd | 真实SOS服务器/workarea/checkin/checkout尚无真机证明 |
| 文件传输 / MCP / SSH | 204800字节本地上传/下载哈希一致；MCP真实调用及错误信息；错误token拒绝；OpenSSH与Paramiko实际220传输哈希一致 | 所有工具schema/输入组合、多profile/多物理宿主及全故障矩阵未覆盖 |
| 分发/多平台 | Linux Python3.11/3.13；0.2.1新环境wheel、源码包内容与资源一致性；220与新umask022环境的授权迁移通过 | 全声明Python版本矩阵、Windows/macOS、Python2守护进程、双UID/多物理主机尚未完整复测；旧0664部署限制仍在 |

数字综合/P&R/STA、EMIR/ESD/antenna/density/DFM、封装与流片验收不因 raw SKILL 或 Calibre 命令调度而自动具备。本轮完整回归可以评估软件库已声明的功能与已知故障，不能输出“完整芯片全流程/所有 Virtuoso 函数全通过”的结论。
