"""bridge —— 在 EDA 机器上跑 headless SKILL，把设计导回本地（逆向入口）。

用法（命令行走 `vlight dump`）::

    text = run_dump("eda-220", ["tsmcN65_LNA2G4"], cdslib="/root/cadence_projects/cds.lib")
    design = import_dump(text)

原理：virtuoso -nograph 会读当前目录的 `.cdsinit`，在里面 load 我们的 .il，
脚本用 dbOpenCellViewByType / cdfGetInstCDF 把 semantic 吐成文本，最后 exit。
这条路避开了 OA 二进制格式，拿到的是工具自己的对象模型。
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field

DUMP_IL = r'''
;; vlight_dump.il —— 生成方: vlight.bridge
printf("VLIGHT-DUMP-BEGIN\n")
printf("SKILLVER=%L\n" getVersion())

procedure(vlNames(l) mapcar(lambda((x) x~>name) l))

procedure(vlDumpCDF(inst)
  let((cdf ps)
    cdf = car(errset(cdfGetInstCDF(inst) t))
    when(cdf
      foreach(p cdf~>parameters
        printf("    P %s %s = %L\n" p~>name p~>type p~>value)))))

procedure(vlDumpProps(inst)
  let((pl)
    pl = car(errset(inst~>prop t))
    when(pl
      foreach(p pl printf("    R %s = %L\n" p~>name p~>value)))))

procedure(vlDumpSch(lib cell)
  let((cv)
    cv = car(errset(dbOpenCellViewByType(lib cell "schematic" "schematic" "r") t))
    if(cv
      progn(
        printf("SCH %s/%s insts=%d nets=%d terms=%d shapes=%d\n"
               lib cell length(cv~>instances) length(cv~>nets)
               length(cv~>terminals) length(cv~>shapes))
        foreach(inst cv~>instances
          printf("  I %s %s/%s/%s xy=%L orient=%L n=%L\n"
                 inst~>name inst~>libName inst~>cellName inst~>viewName
                 inst~>xy inst~>orient inst~>numInst)
          vlDumpCDF(inst)
          vlDumpProps(inst))
        foreach(net cv~>nets
          printf("  N %s sig=%L\n" net~>name net~>sigType)
          foreach(it net~>instTerms
            printf("    T %s.%s\n" it~>inst~>name it~>name)))
        foreach(tm cv~>terminals
          printf("  PIN %s dir=%L net=%L\n" tm~>name tm~>direction
                 (if tm~>net tm~>net~>name "nil")))
        dbClose(cv))
      printf("SCH %s/%s OPEN-FAILED\n" lib cell))))

procedure(vlDumpLib(lib)
  let((dd)
    dd = car(errset(ddGetObj(lib) t))
    if(dd
      progn(
        printf("LIB %s ncells=%d\n" lib length(dd~>cells))
        foreach(c dd~>cells
          printf("  CELL %s views=%L\n" c~>name vlNames(c~>views)))
        foreach(c dd~>cells vlDumpSch(lib c~>name)))
      printf("LIB %s MISSING\n" lib))))

foreach(lib LIBLIST vlDumpLib(lib))

;; 技术文件里的层名（层号以 XXX.layermap 为准，SKILL 侧 layerNumber 不可靠）
let((cv tf)
  cv = car(errset(dbOpenCellViewByType(car(LIBLIST) car(vlNames(ddGetObj(car(LIBLIST))~>cells))
                                       "schematic" "schematic" "r") t))
  when(cv
    tf = car(errset(techGetTechFile(cv) t))
    when(tf
      printf("TECH layers=%d\n" length(tf~>layers))
      foreach(l tf~>layers printf("  L %s\n" l)))))
printf("VLIGHT-DUMP-END\n")
exit
'''


def absolutize_cdslib(text: str, base_dir: str) -> str:
    """cds.lib 里 DEFINE/INCLUDE 的相对路径按原目录展开，否则拷到别的目录就失效。"""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        parts = line.split()
        if len(parts) >= 3 and parts[0].upper() in ("DEFINE", "ASSIGN", "INCLUDE",
                                                    "SOFTINCLUDE"):
            path = parts[1] if parts[0].upper().startswith(("INCLUDE", "SOFT")) else parts[2]
            if path and not path.startswith(("/", "$")):
                path = f"{base_dir.rstrip('/')}/{path}"
                if parts[0].upper().startswith(("INCLUDE", "SOFT")):
                    parts[1] = path
                else:
                    parts[2] = path
                line = " ".join(parts)
        out.append(line)
    return "\n".join(out) + "\n"


@dataclass
class DumpResult:
    text: str
    log: str = ""
    workdir: str = ""

    @property
    def ok(self) -> bool:
        return "VLIGHT-DUMP-END" in self.text


def dump_il(libs: list[str]) -> str:
    quoted = "(" + " ".join('"%s"' % l for l in libs) + ")"
    return f"(setq LIBLIST '{quoted})\n" + DUMP_IL


def _ssh(host: str, cmd: str, stdin: str | None = None, timeout: int = 300,
         opts: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=8")) -> str:
    proc = subprocess.run(["ssh", *opts, host, cmd],
                          input=stdin.encode() if stdin else None,
                          capture_output=True, timeout=timeout)
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"ssh {host} 失败: {proc.stderr.decode(errors='replace')[-1500:]}")
    return proc.stdout.decode(errors="replace")


def run_dump(host: str, libs: list[str], cdslib: str | None = None,
             workdir: str = "/tmp/vlight_dump", virtuoso: str = "virtuoso",
             timeout: int = 300, defines: list[str] | None = None) -> DumpResult:
    """在远端跑一次 headless SKILL 导出，返回文本。

    cdslib: 远端已有的 cds.lib（例如 PDK 的那份，里面有 $CDSHOME 的库）
    defines: 追加的 DEFINE 行，形如 ["tsmcN65_LNA2G4=/root/cadence_projects/tsmcN65_LNA2G4"]
    """
    il = dump_il(libs)
    _ssh(host, f"mkdir -p {shlex.quote(workdir)}")
    _ssh(host, f"cat > {shlex.quote(workdir)}/vlight_dump.il", stdin=il)
    _ssh(host, f"cat > {shlex.quote(workdir)}/.cdsinit",
         stdin=f'load("{workdir}/vlight_dump.il")\n')
    cds_lines = ""
    if cdslib:
        try:
            cds_lines = _ssh(host, f"cat {shlex.quote(cdslib)}")
            base = cdslib.rsplit("/", 1)[0]
            cds_lines = absolutize_cdslib(cds_lines, base)
        except RuntimeError:
            cds_lines = ""
    for d in defines or []:
        name, _, path = d.partition("=")
        cds_lines += f"\nDEFINE {name} {path}\n"
    if cds_lines:
        _ssh(host, f"cat > {shlex.quote(workdir)}/cds.lib", stdin=cds_lines)
    cmd = (f"cd {shlex.quote(workdir)} && {virtuoso} -nograph < /dev/null "
           f"> vlight_dump.log 2>&1; echo RC=$?; cat vlight_dump.log")
    log = _ssh(host, cmd, timeout=timeout)
    text = ""
    if "VLIGHT-DUMP-BEGIN" in log:
        start = log.index("VLIGHT-DUMP-BEGIN")
        end = log.find("VLIGHT-DUMP-END")
        text = log[start:end + len("VLIGHT-DUMP-END")] if end > 0 else log[start:]
    return DumpResult(text=text, log=log, workdir=workdir)


__all__ = ["dump_il", "run_dump", "DumpResult", "DUMP_IL"]
