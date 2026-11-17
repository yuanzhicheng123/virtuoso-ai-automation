"""PSF 结果适配：把 Spectre 的 PSF ASCII 结果变成结构化字典。"""
from __future__ import annotations

from pathlib import Path

from vlight import psf


def read(path: str | Path) -> dict:
    p = psf.read_psf(path)
    return {"x": [float(v) for v in p.x],
            "signals": {k: [float(x) for x in p.y[k]] for k in list(p.y)},
            "header": dict(getattr(p, "header", {}) or {})}


def op_point(path: str | Path) -> dict:
    """DC 工作点：节点电压 + 源电流。"""
    p = psf.read_psf(path)
    out = {}
    for k in p.y:
        out[k] = float(p.y[k][0])
    return out


def sweep(path: str | Path, xname: str = "", yname: str = "") -> dict:
    p = psf.read_psf(path)
    return {"x": [float(v) for v in p.x],
            "y": {k: [float(v) for v in p.y[k]] for k in list(p.y)}}
