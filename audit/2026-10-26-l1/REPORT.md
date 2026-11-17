# L1 修复报告：**L1_FIXED**，合法入口恢复

## 根因（有完整 traceback 支撑）
```
channel.py:39  status, response = response.split(' ', maxsplit=1)
ValueError: not enough values to unpack (expected 2, got 1)
```
- 异常发生在 **响应帧拆分**（阶段 B），不是对象转换或日志。
- 触发条件：`Workspace.open("default")` 连到 **已失效/失步的 default 会话 socket**
  （`/tmp/skill-server-default.sock` 文件存在但服务端已退出或状态不对），返回空帧。
- 证据：改用独立新会话 `?id "v01"` 后，同一代码、同一解释器、同一入口，
  `lib → open → inst → nets → conns → pins → wires → width → schCheck → save → reopen` 全部通过。

## 最小修改
1. `v01.py` 增加 `V01_SESSION` 环境变量（默认 `default`），入口显式绑定会话 id；
2. `v01.py` 增加 `V01_CDS_DIR`（默认 `/tmp/sb9`），网表进程使用库所在目录；
3. 日志不再对远程对象调用 `str()`（避免触发额外远程查询）；
4. 异常路径记录完整 traceback。
未捕获异常冒充成功；未绕过 contract；pch/nf=2 拒绝规则保持不变。

## 核心回归（真机）
| 用例 | 结果 |
|---|---|
| 合法 500n | **PASS**：`schCheck=[0,0]`，save/reopen OK，网表 `M0 (D G S B) nch l=60n w=500n` |
| pch | PASS（写入前拒绝：`器件不在契约内`，无库目录） |
| nf=2 | PASS（写入前拒绝：`受保护参数 nf=2 未验证`，无库目录） |

## 重要附带结果
网表端子顺序为 **D G S B**（端口/导线真实生效），不再是 net1..net4。

## 真实命令
```
# 启动独立会话
virtuoso -nograph  (.cdsinit 内: pyStartServer ?id "v01" ?python "/tmp/pywrap.sh")
# 合法请求
V01_SESSION=v01 V01_CDS_DIR=/tmp/sbL1 /tmp/pywrap.sh /tmp/v01.py \
  --config /tmp/v01cfg/g500.json --workdir /tmp/v01g/g500
```

## 未完成（保持原状）
自动句柄、单入口正式接入、R2 对抗、新 R3 仿真、R5、操作库认证 → 仍为 NOT_RUN。
整体 V0.1 仍为 **NOT_READY**。
