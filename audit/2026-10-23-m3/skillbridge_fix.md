# SkillBridge 启动修复（P0 / Gate 1）

## 根因（实测）
1. `pyStartServer` 默认 `?python "python"` → 走 PATH 上的解释器；
2. Cadence 会话环境里的 `LD_LIBRARY_PATH` 指向 Cadence 自带库，导致：
   `symbol lookup error: undefined symbol: _Py_LegacyLocaleDetected`
   （其后又出现 `init_fs_encoding: No module named 'encodings'`）；
3. 传输是 **Unix socket** `/tmp/skill-server-default.sock`，不是 TCP —— 只看端口会误判失败。

## 修复（仅改子进程环境）
```sh
# /tmp/pywrap.sh
#!/bin/sh
unset LD_LIBRARY_PATH PYTHONHOME PYTHONPATH
exec /opt/miniconda/envs/cadence_skill/bin/python "$@"
```
```skill
(pyStartServer ?python "/tmp/pywrap.sh" ?logLevel "WARNING" ?timeout 60)
```
启动用 `env -u LD_LIBRARY_PATH virtuoso -nograph`。

## 验收（真实执行）
| 项 | 结果 |
|---|---|
| Python 子进程 | 正常（3.6.8，skillbridge 可导入） |
| socket | `/tmp/skill-server-default.sock` 建立 |
| 只读调用 | `car`→1、`length`→4、`getCurrentTime` 正常 |
| 错误请求 | `zzNotAFunction` 抛出 RuntimeError（含 SKILL 错误文本） |
| 连续 100 次 | 0 错配，总耗时 30.3 ms |
| 重连 | `epoch` 递增、探针 `car`=1 |
