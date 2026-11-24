# Virtuoso Fusion 0.2.1 全库验证状态

日期：2026-11-23。项目验收状态唯一权威；历次版本和失败记录保留。

**裁决：放行已验证的220工作流；“全库跨环境全部通过”不放行。** 本轮完整核验Fusion、固定上游、安装分发与真机链路，并修复两处Fusion验收/日志问题。上游仍存在两个可复现的条件性问题，不能用主流程通过掩盖。

## 本轮结果

| 范围 | 实际结果 | 本地证据（evidence/full-validation/） |
|---|---|---|
| Fusion全套离线回归 | **119 passed，22 live skipped**；跳过不计通过 | fusion-final-unit.log/xml |
| 新环境安装包逻辑回归 | **98 passed**，pipcheck通过，仓库外site-packages导入 | installed-tests.log、install.log |
| 固定上游完整套件 | **2902 passed，126 skipped，1 failed** | upstream-clean.log/xml |
| 上游原生补充 | registry19通过；真实Xvfb19通过；daemon concurrency2通过、3项双UID前提跳过 | upstream-native-*.log |
| 0.2.1组合真机 | **22/22 passed，零跳过，102.68s** | live/live.xml、result.json |
| RF重跑 | AC/noise/SP/PSS/PNoise/PAC/HB与PNoise边带/PSS-HB交叉检查均通过 | rf-report.json |
| PVT / process MC重跑 | 27/27 PVT；3随机样本+独立nominal；同seed两次一致 | pvt-mc-report.json |
| SSH传输 | OpenSSH、Paramiko两后端实际连接220，上传/读取hash一致 | ssh-transports.json |
| 资源/安装/分发 | 原37资源hash、CLI/MCP、最终wheel与实测代码一致；详情见release记录 | release-verification.json |
| 独立复查 | 两项修复无剩余阻断；定向18项通过 | final-review.md |

固定上游仍为 `3f0bd7d46ca3c5d2c750cea0894bbdefc7b67b9d`，未修改vendor源码或降低权限检查。上游完整测试使用专用pytest9环境和干净固定提交副本；不是前轮645项子集。126项skip涉及95项上游opt-in真机、14项Python2.7、16项Windows/macOS及1项宿主条件；Fusion的22项真机不冒充这些全部执行过。

## 真机覆盖

220根目录 `/home/lihong/virtuoso-fusion-20261123`。最终组合运行：`signoff/full-021-suite/live-e687kd6s`。

- 认证身份、错误token拒绝、generic参数/线程/失败恢复、同客户端OA调用。
- RC原理图/CDF/check/save→原生网表→真实DC/AC；Maestro保存重开、2 sweep×2 corner的4个实际点、RDB/标量结果核对。
- N65 PCell受控尺寸、几何变换/重开、GDS、OA复制；注册层映射在XStream及oa2strm两路实际产生指定231层。
- 真实Calibre DRC 1737规则、**29结果**，正确返回检查失败；单浮空600nm/60nm管LVS CORRECT；PEX三阶段及自定义网表产出**6C、0R**，明确checks_passed=null。
- 新增符号端口/语义标签/termOrder回读；scratch库tech绑定、重命名/删除及category增删改；204800字节传输哈希一致；MCP真实调用与失败信息保留。
- GUI空闲检查；真实执行超时后查询原request receipt，副作用计数仍为1；两个SpectrePool作业各有独立输出，电压1V/2V数值正确。
- 当前已加载上下文普查118193个绑定函数，历史注册表匹配74783、缺43410；名称召回63.2719%，QUERY_ERROR=0。该分母随已加载上下文变化，既非全部Cadence产品全集，也不是调用成功率。

RF：`signoff/full-021-rf/rf-55dr86bi`；PVT/MC：`signoff/full-021-pvt-mc/pvt-mc-7fjz8koy`。这些均是小测试台的软件/数值探针，不是设计规格、统计良率或芯片签核。

## 修复和保留的失败

1. **全部跳过误报PASS**：tools/run_live.py改为解析JUnit，记录tests/passed/skipped/errors/failures。全跳过/空选择为INCOMPLETE，退出非零；probe-only另标范围。真实pytest子进程6项回归先红后绿。
2. **oa2strm时间戳诊断漏报**：支持实际 `YYYY/MM/DD HH:MM:SS WARNING/ERROR ...` 格式并保留原行。新增2项回归；最终真机结果确实包含OS warning，不再丢失。
3. **kwargs保留名**：保持public调用签名兼容；说明self/fn/timeout与已有g_self/g_fn/g_timeout别名，并新增真实注册表编排回归。未把这些函数的编排测试计为真机调用。

首轮workbench运行 `signoff/full-021-workbench1/live-1ma7dfhg` 为4通过1失败：测试未为实际本机GUI传local_gui=True。修正测试显式上下文后独立5/5，再组合22/22；不是忽略guard绕过远程身份检查。0.2.0原17项也于本轮重跑17/17，原记录均保留。

## 尚未消失的上游问题

**A. bsd-csh selector污染stdout。** 本机固定上游全套唯一失败；最小脚本的banner进入stdout。220的csh同例正确把banner送stderr。没有证据证明Spectre数值错误，但跨csh实现的输出契约不一致，不能授予全平台通过。

**B. 0664资源/部署条件下legacy→auto迁移失败。** 在本机已安装依赖上，生成资源继承group-writable模式，即使明确allow_setup_replacement也被私有文件检查拒绝。两次复现仅在临时目录，不改用户部署。220资源为0644，在显式允许替换后迁移通过；全新本机安装及生成都使用进程umask022也通过。未允许替换时的“bundle differs”是正常保护，不算bug。上述受控通过不表示已有0664部署被修复。

详细环境、精确调用路径与复现脚本见 `FULL_SURFACE_AUDIT.md`、`local-authorized-migration.json`、`220-authorized-migration.json`、`fresh-022-migration.json`。不通过放松guard或修改既有生产目录来制造全绿。

## 仍未验证/未实现

- 所有74785注册函数逐个调用、所有参数/上下文/版本/license组合；generic仍不封装OA对象句柄。
- SOS真服务器/workarea未提供，220未发现soscmd；checkin/checkout仅有上游离线证据。跨主机多profile/GUI授权组合及完整故障矩阵未全面实测。
- 全部原理图导入/复杂层级/config、layout via/mosaic及所有PDK callbacks、Maestro分布式job/所有历史/波形导出组合未逐一验收。
- MC失配仍有此前PDK参数未定义问题；ADE MC仅配置持久化；3个工艺样本不代表统计收敛。
- DRC-clean芯片、带端口电路级LVS、非平凡R网络、多corner后仿、EMIR/ESD/DFM、封装/流片，以及数字综合/P&R/STA均不因本库工具接口可调用而自动通过。
- 本轮实际Python为3.11/3.13、Linux；未跑全声明版本矩阵及Windows/macOS、Python2 worker、真实双UID。

## 交付与复现

源码 `/home/lihong/Demo/virtuoso_bridge_end/virtuoso-fusion`；0.2.1发行目录 `/home/lihong/Demo/virtuoso_bridge_end/fusion-full-release`；220的source/venv/dist同步。旧0.1.x和0.2.0发行物保留；内部交付，没有上传GitHub/PyPI。最终commit/包hash在RELEASE.json和release-verification.json。

```sh
python -m pytest tests
python tools/prepare_resources.py --check
# 220工作区，显式打开所有本库live模块
VF_RUNTIME_INVENTORY=1 VF_INVENTORY_VIEWS=1 VF_LIVE_PHYSICAL=1 VF_LIVE_WORKBENCH=1 \
VF_LIVE_MODEL=/eda/1P9M/TSMC065_1P9M/models/spectre/crn65gplus_2d5_lk_v1d0.scs \
venv/bin/python source/tools/run_live.py --root signoff/full-new --cds-lib cds.lib \
  --gui --tests source/tests/live --test-timeout 900
```

最终result.json确认owned Virtuoso/RAMIC/Xvfb均退出。原始OA/PSF/RDB与Calibre数据留220，摘要与哈希保存在本地evidence/full-validation；认证材料和PDK规则不进入发行包。
