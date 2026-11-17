"""网表适配器：固定 OCEAN 模板导出 Spectre 网表（不接受任意 SKILL 字符串）。

模板只允许三个字段：lib / cell / view（严格校验），其余全部固定。
导出后计算哈希、记录日志，返回路径与证据。
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

OCEAN = "/eda/cds/IC2310/tools.lnx86/dfII/bin/ocean"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TEMPLATE = """simulator('spectre)
design("{lib}" "{cell}" "{view}")
createNetlist(?recreateAll t)
exit
"""


class NetlistError(RuntimeError):
    pass


@dataclass
class NetlistResult:
    lib: str
    cell: str
    view: str
    path: str
    sha256: str
    log_tail: str
    ok: bool


def _check(value: str, what: str) -> str:
    if not NAME.match(value or ""):
        raise NetlistError(f"{what} 非法: {value!r}（只允许字母数字下划线）")
    return value


def export(lib: str, cell: str, view: str = "schematic",
           workdir: str = "/tmp/vl_m1", host: str | None = None,
           timeout: float = 240.0) -> NetlistResult:
    lib, cell, view = _check(lib, "lib"), _check(cell, "cell"), _check(view, "view")
    script = TEMPLATE.format(lib=lib, cell=cell, view=view)
    if host:
        subprocess.run(["ssh", host, f"mkdir -p {workdir}"], timeout=60, check=True)
        subprocess.run(["ssh", host, f"cat > {workdir}/netlist.ocn"],
                       input=script, text=True, timeout=60, check=True)
        p = subprocess.run(["ssh", host,
                            f"cd {workdir}; HOME={workdir}; unset DISPLAY; "
                            f"timeout {int(timeout)} {OCEAN} -nograph "
                            f"< netlist.ocn 2>&1"],
                           capture_output=True, text=True, timeout=timeout + 60)
    else:
        Path(workdir, "netlist.ocn").write_text(script)
        p = subprocess.run([OCEAN, "-nograph"], input=script, capture_output=True,
                           text=True, timeout=timeout, cwd=workdir)
    log = (p.stdout or "") + (p.stderr or "")
    pattern = f"{workdir}/simulation/{cell}/spectre/{view}/netlist/input.scs"
    if host:
        cat = subprocess.run(["ssh", host, f"cat {pattern}"],
                             capture_output=True, text=True, timeout=60)
        text = cat.stdout
    else:
        text = Path(pattern).read_text() if Path(pattern).is_file() else ""
    ok = "simulator lang=spectre" in text and "M" in text
    sha = hashlib.sha256(text.encode()).hexdigest() if text else ""
    return NetlistResult(lib, cell, view, pattern, sha, log[-400:], ok)
