"""sim —— 仿真执行 + 结果回读 + 扫描（ADE 的 Run/Sweep/Outputs 那层）。

后端两种：
  * 本地：直接 subprocess 调 spectre
  * 远端：ssh 到 EDA 机器（例如 eda-220），把网表丢过去跑，raw 目录 tar 回来

结果统一成 psf.Plot，指标用 calc 表达式算（dB20(V(out)) 这种）。
"""
from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import tarfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import psf
from .psf import Plot
from .tb import Testbench


class SimError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #

@dataclass
class Results:
    plots: dict[str, Plot] = field(default_factory=dict)
    log: str = ""
    netlist: str = ""
    workdir: Path | None = None
    elapsed: float = 0.0
    host: str | None = None

    # -- 取图 ------------------------------------------------------------- #
    def plot(self, key: str | None = None) -> Plot:
        if not self.plots:
            raise SimError("没有结果")
        if key is None:
            return next(iter(self.plots.values()))
        if key in self.plots:
            return self.plots[key]
        for p in self.plots.values():                     # 按分析类型找
            if p.kind == key:
                return p
        raise KeyError(f"没有 plot {key!r}；有 {list(self.plots)}")

    def __getitem__(self, key: str) -> Plot:
        return self.plot(key)

    def __iter__(self):
        return iter(self.plots.values())

    # -- 便捷访问 --------------------------------------------------------- #
    def v(self, node: str, key: str | None = None):
        return self.plot(key).v(node)

    def i(self, branch: str, key: str | None = None):
        return self.plot(key).i(branch)

    def metric(self, expr: str, key: str | None = None) -> float:
        from .calc import evaluate
        val = evaluate(expr, self.plot(key))
        arr = getattr(val, "shape", None)
        if arr:
            return float(val[-1].real if hasattr(val[-1], "real") else val[-1])
        return float(getattr(val, "real", val))

    def metrics(self, exprs: dict[str, str], key: str | None = None) -> dict[str, float]:
        return {k: self.metric(e, key) for k, e in exprs.items()}

    def summary(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "elapsed_s": round(self.elapsed, 2),
            "plots": {k: {"kind": p.kind, "points": len(p.x), "signals": len(p.y)}
                      for k, p in self.plots.items()},
            "workdir": str(self.workdir) if self.workdir else None,
        }

    # -- 落盘 ------------------------------------------------------------- #
    def save(self, out: str | Path) -> Path:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "netlist.scs").write_text(self.netlist)
        (out / "spectre.log").write_text(self.log)
        for name, p in self.plots.items():
            p.to_npz(out / f"{name}.npz")
        return out


def collect_raw(rawdir: Path, log: str = "", netlist: str = "") -> Results:
    plots: dict[str, Plot] = {}
    for f in sorted(rawdir.iterdir()):
        if not f.is_file() or f.name.startswith(("log", ".")):
            continue
        if f.suffix in (".info", ".log"):
            continue
        try:
            p = psf.read_psf(f)
        except Exception:                                  # noqa: BLE001 坏文件跳过
            continue
        if not p.y:
            continue
        key = p.name or f.stem
        plots[key] = p
    return Results(plots=plots, log=log, netlist=netlist, workdir=rawdir.parent)


# --------------------------------------------------------------------------- #
# 执行器
# --------------------------------------------------------------------------- #

@dataclass
class Simulator:
    spectre: str = "spectre"
    host: str | None = None
    workdir: str = "sim"
    args: tuple[str, ...] = ("-64", "+escchars", "+lqtimeout", "30")
    timeout: int = 1800
    keep: bool = True
    ssh_opts: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=8")
    engine: str = "spectre"          # spectre | vlight（自研引擎，无需 license）

    # -- 对外 ------------------------------------------------------------- #
    def run(self, tb: Testbench, name: str | None = None) -> Results:
        if self.engine == "vlight":
            from .spice import Engine
            return Engine(tb.design).run(tb, name)
        netlist = tb.scs()
        tag = name or time.strftime("sim_%Y%m%d_%H%M%S")
        if self.host:
            return self._run_remote(netlist, tag)
        return self._run_local(netlist, tag)

    # -- 本地 ------------------------------------------------------------- #
    def _run_local(self, netlist: str, tag: str) -> Results:
        wd = Path(self.workdir) / tag
        raw = wd / "raw"
        shutil.rmtree(raw, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "netlist.scs").write_text(netlist)
        cmd = [self.spectre, *self.args, "-format", "psfascii",
               "-raw", str(raw), "netlist.scs"]
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                              timeout=self.timeout)
        dt = time.time() - t0
        log = (proc.stdout or "") + (proc.stderr or "")
        (wd / "spectre.log").write_text(log)
        res = collect_raw(raw if raw.is_dir() else wd, log, netlist) if raw.is_dir() \
            else Results(log=log, netlist=netlist, workdir=wd)
        res.elapsed, res.host = dt, None
        if proc.returncode != 0 and not res.plots:
            raise SimError(f"spectre 失败 (rc={proc.returncode})\n{log[-3000:]}")
        return res

    # -- 远端 ------------------------------------------------------------- #
    def _ssh(self, cmd: str, stdin: str | None = None, binary: bool = False,
             timeout: int | None = None) -> bytes | str:
        full = ["ssh", *self.ssh_opts, self.host, cmd]
        proc = subprocess.run(full, input=stdin.encode() if isinstance(stdin, str) else stdin,
                              capture_output=True, timeout=timeout or self.timeout)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")[-2000:]
            raise SimError(f"ssh 失败 (rc={proc.returncode}): {err}")
        return proc.stdout if binary else proc.stdout.decode(errors="replace")

    def _run_remote(self, netlist: str, tag: str) -> Results:
        rdir = f"/tmp/vlight/{tag}_{uuid.uuid4().hex[:6]}"
        self._ssh(f"mkdir -p {shlex.quote(rdir)}")
        self._ssh(f"cat > {shlex.quote(rdir)}/netlist.scs", stdin=netlist)
        cmd = (f"cd {shlex.quote(rdir)} && {self.spectre} {' '.join(self.args)} "
               f"-format psfascii -raw raw netlist.scs")
        t0 = time.time()
        try:
            out = self._ssh(cmd)
        except SimError as exc:
            out = str(exc)
        dt = time.time() - t0
        wd = Path(self.workdir) / tag
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "netlist.scs").write_text(netlist)
        (wd / "spectre.log").write_text(out)
        blob = self._ssh(f"cd {shlex.quote(rdir)} && tar czf - . 2>/dev/null", binary=True)
        if blob:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
                tf.extractall(wd)
        if not self.keep:
            self._ssh(f"rm -rf {shlex.quote(rdir)}")
        raw = wd / "raw"
        res = collect_raw(raw, out, netlist) if raw.is_dir() else Results(log=out, netlist=netlist)
        res.elapsed, res.host, res.workdir = dt, self.host, wd
        if not res.plots:
            raise SimError(f"远端仿真无结果：{out[-3000:]}")
        return res


# --------------------------------------------------------------------------- #
# 扫描 / 角
# --------------------------------------------------------------------------- #

@dataclass
class SweepPoint:
    values: dict[str, Any]
    results: Results | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SweepResult:
    points: list[SweepPoint] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)

    def table(self) -> list[dict[str, Any]]:
        rows = []
        for p in self.points:
            row = dict(p.values)
            row.update(p.metrics)
            if p.error:
                row["error"] = p.error
            rows.append(row)
        return rows

    def to_csv(self, path: str | Path) -> Path:
        import csv
        rows = self.table()
        p = Path(path)
        cols = self.columns + [c for c in (rows[0] if rows else {})
                               if c not in self.columns and c != "error"]
        with p.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols + (["error"] if any(
                "error" in r for r in rows) else []), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return p

    def best(self, metric: str, maximize: bool = True) -> SweepPoint | None:
        cand = [p for p in self.points if metric in p.metrics]
        if not cand:
            return None
        return (max if maximize else min)(cand, key=lambda p: p.metrics[metric])

    def __len__(self) -> int:
        return len(self.points)


def grid(variables: dict[str, Iterable[Any]]) -> list[dict[str, Any]]:
    """笛卡尔积；键名里的 `|` 表示互斥（同一组取一个，不是全排列）。"""
    import itertools
    exclusive: list[list[dict[str, Any]]] = []
    plain: list[tuple[str, list]] = []
    for key, vals in variables.items():
        vals = list(vals)
        if "|" in key:
            exclusive.append([{k: v for k, v in zip(key.split("|"), combo)}
                              for combo in itertools.product(*[vals] * len(key.split("|")))])
        else:
            plain.append((key, vals))
    combos = [dict(zip([k for k, _ in plain], vals))
              for vals in itertools.product(*[v for _, v in plain])] if plain else [{}]
    if exclusive:
        out = []
        for e in itertools.product(*exclusive):
            merged = {}
            for d in e:
                merged.update(d)
            for c in combos:
                out.append({**c, **merged})
        return out
    return combos


def run_sweep(tb: Testbench, variables: dict[str, Iterable[Any]],
              sim: Simulator, outputs: dict[str, str] | None = None,
              parallel: int = 4, progress=None) -> SweepResult:
    """参数扫描：每个点独立 workdir，多线程跑 spectre。"""
    import copy
    pts = grid(variables)
    sweep = SweepResult(columns=list(variables))
    sweep.points = [SweepPoint(values=dict(p)) for p in pts]
    outputs = outputs or {}

    def one(idx: int, point: SweepPoint) -> None:
        tb2 = copy.copy(tb)
        tb2.variables = {**tb.variables, **point.values}
        try:
            res = sim.run(tb2, name=f"{time.strftime('%Y%m%d_%H%M%S')}_{idx:03d}")
            point.results = res
            point.metrics = res.metrics(outputs) if outputs else {}
        except Exception as exc:                          # noqa: BLE001
            point.error = f"{type(exc).__name__}: {exc}"
        if progress:
            progress(idx, len(pts), point)

    if parallel <= 1:
        for i, p in enumerate(sweep.points):
            one(i, p)
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            list(ex.map(lambda ip: one(*ip), list(enumerate(sweep.points))))
    return sweep


# --------------------------------------------------------------------------- #
# 画图（可选依赖）
# --------------------------------------------------------------------------- #

def quickplot(results: Results, signals: list[str], key: str | None = None,
              out: str | None = None, logx: bool = True, db: bool = False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    p = results.plot(key)
    fig, ax = plt.subplots(figsize=(6, 3.6), dpi=130)
    for s in signals:
        y = p.signal(s)
        if db:
            y = 20 * np.log10(np.abs(y) + 1e-30)
        ax.plot(p.x, np.real(y), label=s, lw=1.2)
    if logx and p.xname in ("freq",):
        ax.set_xscale("log")
    ax.set_xlabel(p.xname)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    if out:
        fig.savefig(out)
    return fig


__all__ = ["Results", "Simulator", "SweepResult", "SweepPoint", "run_sweep",
           "grid", "quickplot", "collect_raw", "SimError"]
