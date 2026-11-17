"""证据包：把一次运行的输入、命令、哈希、日志、结果、环境指纹写成一个 manifest。"""
from __future__ import annotations

import hashlib
import json
import platform
import time
from pathlib import Path


class EvidenceManifest:
    def __init__(self, root: str | Path, task: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.data = {"task": task, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                            time.gmtime()),
                     "host": platform.node(), "entries": []}

    @staticmethod
    def sha256(path: str | Path) -> str:
        p = Path(path)
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""

    def add(self, kind: str, path: str | Path | None = None, **extra) -> None:
        entry = {"kind": kind, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())}
        if path is not None:
            entry["path"] = str(path)
            entry["sha256"] = self.sha256(path)
        entry.update(extra)
        self.data["entries"].append(entry)

    def save(self, name: str = "manifest.json") -> Path:
        out = self.root / name
        out.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        return out
