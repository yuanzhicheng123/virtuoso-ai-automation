"""Spectre runner：固定模板、Run ID、超时不自动重试、PSF 解析、证据落盘。"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from vlight import psf

SPECTRE = "/eda/cds/SPECTRE201/tools/bin/spectre"


@dataclass
class RunResult:
    run_id: str
    status: str                     # OK / FAILED / TIMEOUT
    returncode: int = 0
    stdout_tail: str = ""
    netlist_hash: str = ""
    results: dict = field(default_factory=dict)
    evidence_dir: str = ""
    seconds: float = 0.0

    def json(self) -> dict:
        return {"run_id": self.run_id, "status": self.status,
                "returncode": self.returncode, "seconds": round(self.seconds, 3),
                "netlist_hash": self.netlist_hash,
                "evidence_dir": self.evidence_dir,
                "stdout_tail": self.stdout_tail[-800:],
                "results": self.results}


class SpectreRunner:
    def __init__(self, root: str | Path, spectre: str = SPECTRE,
                 timeout: float = 120.0, host: str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.spectre = spectre
        self.timeout = timeout
        self.host = host

    def run(self, netlist: str | Path, run_id: str, analyses=None) -> RunResult:
        netlist = Path(netlist)
        text = netlist.read_text()
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        ev = self.root / run_id
        ev.mkdir(parents=True, exist_ok=True)
        raw = ev / "raw"
        if self.host:
            import subprocess as sp
            remote = f"/tmp/vl_phase2/{run_id}"
            sp.run(["ssh", self.host, f"rm -rf {remote} && mkdir -p {remote}"],
                   capture_output=True, text=True, timeout=60)
            sp.run(["ssh", self.host, f"cat > {remote}/net.scs"],
                   input=text, text=True, timeout=60, check=True)
            cmd = [self.spectre, "-64", "-format", "psfascii", "-raw",
                   f"{remote}/raw", f"{remote}/net.scs"]
            t0 = time.time()
            try:
                p = sp.run(["ssh", self.host, "cd %s && %s 2>&1" %
                            (remote, " ".join(cmd))], capture_output=True,
                           text=True, timeout=self.timeout + 60)
                rc, out = p.returncode, p.stdout + p.stderr
                status = "OK" if "spectre completes with 0 errors" in out else "FAILED"
                if status == "OK":
                    raw.mkdir(parents=True, exist_ok=True)
                    tar = sp.run(["ssh", self.host,
                                  f"tar -C {remote} -cf - raw"],
                                 capture_output=True)
                    sp.run(["tar", "-C", str(ev), "-xf", "-"], input=tar.stdout,
                           timeout=120, check=True)
            except sp.TimeoutExpired:
                rc, out, status = -1, f"TIMEOUT after {self.timeout}s", "TIMEOUT"
        else:
            cmd = [self.spectre, "-64", "-format", "psfascii", "-raw", str(raw),
                   str(netlist)]
            t0 = time.time()
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=self.timeout, cwd=str(ev))
                rc, out = p.returncode, (p.stdout + p.stderr)
                status = "OK" if rc == 0 and raw.is_dir() else "FAILED"
            except subprocess.TimeoutExpired:
                rc, out, status = -1, f"TIMEOUT after {self.timeout}s", "TIMEOUT"
        dt = time.time() - t0
        results = {}
        if status == "OK":
            for f in sorted(raw.iterdir()):
                if f.suffix in (".dc", ".tran", ".ac", ".noise"):
                    try:
                        p_ = psf.read_psf(f)
                        results[f.name] = {
                            "x": [float(v) for v in p_.x][:5],
                            "signals": list(p_.y)[:12],
                            "values": {k: float(p_.y[k][0])
                                       for k in list(p_.y)[:8]},
                        }
                    except Exception as exc:      # noqa: BLE001
                        results[f.name] = {"error": f"{type(exc).__name__}: {exc}"}
        (ev / "run.json").write_text(json.dumps({
            "run_id": run_id, "status": status, "returncode": rc,
            "seconds": dt, "netlist_hash": h, "cmd": cmd,
            "results": results}, ensure_ascii=False, indent=1))
        (ev / "spectre.log").write_text(out)
        return RunResult(run_id, status, rc, out, h, results, str(ev), dt)
