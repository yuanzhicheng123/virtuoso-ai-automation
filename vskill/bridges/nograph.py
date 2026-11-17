"""nograph 桥：不需要 daemon，直接起 `virtuoso -nograph` 跑一段 SKILL。

原理（已在 eda-220 上验证）：
    写一个临时目录：.cdsinit 里 load("script.il")，script.il 里 printf("@@k=v@@")
    然后 `virtuoso -nograph`，从 stdout 抓 @@...@@ 标记。

适合批处理：一次进程跑 N 条调用，比逐条 RPC 更适合 CI。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from ..bridge import Bridge, _decode, to_skill

MARK = re.compile(r"@@(\w+)=([^@]*)@@")


class NographBridge(Bridge):
    def __init__(self, host: str | None = None, virtuoso: str = "virtuoso",
                 cds_lib: str | None = None, timeout: float = 300.0,
                 keep: bool = False):
        self.host = host
        self.virtuoso = virtuoso
        self.cds_lib = cds_lib
        self.timeout = timeout
        self.keep = keep

    # -- 组装脚本 --------------------------------------------------------- #
    def _script(self, exprs: list[tuple[str, list]]) -> str:
        lines = ["(procedure (vlOut k v) (printf \"@@%s=%L@@\\n\" k v))"]
        for i, (fn, args) in enumerate(exprs):
            code = "(%s %s)" % (fn, " ".join(to_skill(a) for a in args))
            lines.append("(vlOut \"r%d\" %s)" % (i, code))
        lines.append("(vlOut \"done\" t)")
        return "\n".join(lines)

    def _ssh(self, cmd: str, timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", self.host, cmd], capture_output=True,
                              text=True, timeout=timeout)

    def _write(self, path: str, content: str) -> None:
        if self.host:
            subprocess.run(["ssh", self.host, "cat > %s" % path],
                           input=content, text=True, timeout=60, check=True)
        else:
            with open(path, "w") as fh:
                fh.write(content)

    def _run(self, script: str) -> tuple[dict[str, Any], str]:
        cds = self.cds_lib or (
            "DEFINE analogLib $CDSHOME/tools/dfII/etc/cdslib/artist/analogLib")
        if self.host:
            out = self._ssh("mktemp -d", 60)
            tmp = out.stdout.strip()
            if not tmp:
                raise RuntimeError("远端建临时目录失败: " + out.stderr[-300:])
        else:
            tmp = tempfile.mkdtemp(prefix="vskill_")
        try:
            self._write(tmp + "/cds.lib", cds + "\n")
            self._write(tmp + "/script.il", script + "\n")
            self._write(tmp + "/.cdsinit",
                        '(setShellEnvVar "CDS_LIB" "./cds.lib")\n'
                        'load("%s/script.il")\n(exit)\n' % tmp)
            inner = ("unset DISPLAY; cd %s && timeout %d %s -nograph 2>&1"
                     % (tmp, int(self.timeout), self.virtuoso))
            if self.host:
                run = self._ssh(inner, self.timeout + 120)
                text = run.stdout + run.stderr
            else:
                run = subprocess.run(["sh", "-c", inner], capture_output=True,
                                     text=True, timeout=self.timeout + 120)
                text = run.stdout + run.stderr
            return {k: v for k, v in MARK.findall(text)}, text
        finally:
            if not self.keep:
                if self.host:
                    self._ssh("rm -rf %s" % tmp, 60)
                else:
                    shutil.rmtree(tmp, ignore_errors=True)

    # -- 桥接口 ----------------------------------------------------------- #
    def call(self, fn: str, args: list) -> Any:
        res, text = self._run(self._script([(fn, args)]))
        if "r0" not in res:
            raise RuntimeError("nograph 没有返回结果:\n" + text[-2000:])
        return _decode(res["r0"])

    def execute_batch(self, exprs: list[tuple[str, list]]) -> list:
        res, text = self._run(self._script(exprs))
        if "done" not in res:
            raise RuntimeError("nograph 批量失败:\n" + text[-2000:])
        return [_decode(res.get("r%d" % i, "nil")) for i in range(len(exprs))]

    def run_script(self, code: str) -> dict:
        """执行任意 SKILL 脚本，返回 @@k=v@@ 标记字典。"""
        res, text = self._run(code)
        if not res:
            raise RuntimeError("脚本没有输出标记:\n" + text[-2000:])
        return res

    def eval(self, code: str) -> Any:
        script = ('(procedure (vlOut k v) (printf "@@%s=%L@@" k v))\n'
                  "(vlOut \"r0\" __CODE__)(vlOut \"done\" t)")
        res, text = self._run(script.replace("__CODE__", code))
        if "r0" not in res:
            raise RuntimeError("nograph eval 失败:\n" + text[-2000:])
        return _decode(res["r0"])


__all__ = ["NographBridge"]
