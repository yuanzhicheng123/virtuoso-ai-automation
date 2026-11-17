"""psf —— Spectre 结果文件（PSF ASCII / nutascii rawfile）解析。

两种真实格式都支持：

1. `spectre -format psfascii -raw <dir>`  ->  <dir>/<analysis>.<type>
       HEADER / TYPE / TRACE / VALUE / END，信号写成 `"name" value` 或 `"name" (re im)`
2. `spectre -format nutascii -raw <file>` ->  单个文件里多段 Plotname（SPICE rawfile 风格）

数值一律落成 numpy 数组，复数信号是 complex128。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"(?:\s+"[^"]*")?\s+(\([^)]*\)|' + _NUM + r"|nan|inf)")
_HDR = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*(.*)$')
_TRACE = re.compile(r'"((?:[^"\\]|\\.)*)"\s+"?([^"\s]*)"?')

SECTIONS = ("HEADER", "TYPE", "TRACE", "VALUE", "END")


def _num(tok: str):
    tok = tok.strip()
    if tok.startswith("("):
        parts = tok[1:-1].split()
        re_, im = float(parts[0]), float(parts[1]) if len(parts) > 1 else 0.0
        return complex(re_, im)
    try:
        return float(tok)
    except ValueError:
        return float("nan")


@dataclass
class Plot:
    name: str
    analysis: str = ""
    kind: str = ""
    xname: str = ""
    x: np.ndarray = field(default_factory=lambda: np.zeros(0))
    y: dict[str, np.ndarray] = field(default_factory=dict)
    header: dict[str, str] = field(default_factory=dict)

    # -- 访问 ------------------------------------------------------------- #
    @property
    def complex(self) -> bool:
        return any(np.iscomplexobj(v) for v in self.y.values())

    def signal(self, name: str) -> np.ndarray:
        if name in self.y:
            return self.y[name]
        low = {k.lower(): k for k in self.y}
        if name.lower() in low:
            return self.y[low[name.lower()]]
        raise KeyError(f"{self.name}: 没有信号 {name!r}；现有 {list(self.y)[:12]}")

    def v(self, node: str) -> np.ndarray:
        return self.signal(node)

    def i(self, branch: str) -> np.ndarray:
        return self.signal(branch)

    def at(self, f: float | None = None, index: int | None = None) -> dict[str, complex]:
        if index is None:
            index = int(np.argmin(np.abs(self.x - f)))
        return {k: v[index] for k, v in self.y.items()}

    def to_csv(self, path: str | Path) -> Path:
        p = Path(path)
        cols = [self.xname] + list(self.y)
        with p.open("w") as fh:
            fh.write(",".join(cols) + "\n")
            for i in range(len(self.x)):
                row = [f"{self.x[i]:.9g}"]
                for v in self.y.values():
                    row.append(f"{v[i]:.9g}" if not np.iscomplexobj(v)
                               else f"{v[i].real:.9g}{v[i].imag:+.9g}j")
                fh.write(",".join(row) + "\n")
        return p

    def to_npz(self, path: str | Path) -> Path:
        p = Path(path)
        np.savez_compressed(p, x=self.x, **{k.replace("/", "_"): v for k, v in self.y.items()})
        return p

    def __repr__(self) -> str:
        return (f"<Plot {self.name} kind={self.kind} n={len(self.x)} "
                f"signals={len(self.y)}{' complex' if self.complex else ''}>")


def parse_psfascii(text: str) -> Plot:
    """单分析 PSF ASCII -> Plot。

    真实文件里 VALUE 段有两种写法（都遇到过）::

        "in" "V" 1.000000000000000e+00      ; dc：带类型串
        "out" (0.0476190 -8.54855e-06)      ; ac：不带类型串，复数写 (re im)
    """
    plot = Plot(name="", header={})
    section = None
    names: list[str] = []
    raw: list[tuple[str, object]] = []
    types: dict[str, dict] = {}
    cur: str | None = None

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s in SECTIONS:
            section = s
            cur = None
            continue
        if section == "HEADER":
            m = _HDR.match(s)
            if m:
                plot.header[m.group(1)] = m.group(2).strip().strip('"')
        elif section == "TYPE":
            m = re.match(r'^"([^"]+)"\s+(COMPLEX|FLOAT|INT|STRING|BOOLEAN)', s)
            if m:
                cur = m.group(1)
                types[cur] = {"kind": m.group(2), "props": {}}
                continue
            m = re.match(r'^"([^"]+)"\s+"([^"]*)"', s)
            if m and cur:
                types[cur]["props"][m.group(1)] = m.group(2)
        elif section == "TRACE":
            m = _TRACE.match(s)
            if m:
                names.append(m.group(1))
        elif section == "VALUE":
            for m in _PAIR.finditer(s):
                raw.append((m.group(1), _num(m.group(2))))

    plot.name = plot.header.get("analysis name") or \
        plot.header.get("analysis description", "")
    plot.analysis = plot.header.get("analysis description", plot.name)
    plot.kind = plot.header.get("analysis type", "")
    return _assemble(plot, names, raw, types)


def _xname(types: dict[str, dict], names: list[str], order: list[str]) -> str:
    # 有 sweep/time 类型时，VALUE 里排第一列的就是扫描量（名字未必叫 sweep）
    if any(t["props"].get("key") in ("sweep", "time") for t in types.values()):
        return order[0] if order else ""
    for cand in ("freq", "time", "sweep"):
        if cand in names or cand in order:
            return cand
    return order[0] if order else ""


def _assemble(plot: Plot, names: list[str], raw: list[tuple[str, object]],
              types: dict[str, dict]) -> Plot:
    if not raw:
        return plot
    vorder = _infer_order(raw)
    order = names or vorder
    cols: dict[str, list] = {}
    for name, val in raw:                    # 每个点在文件里按信号名顺序出现一次
        cols.setdefault(name, []).append(val)
    n = min(len(v) for v in cols.values())
    dtype = complex if any(isinstance(v[0], complex) for v in cols.values()) else float
    xname = _xname(types, order, vorder)
    single = n == 1
    if single:                               # .dc 工作点：没有扫描量
        plot.xname = "index"
        plot.x = np.zeros(1)
        plot.y = {k: np.asarray(v[:n], dtype=dtype) for k, v in cols.items()}
    else:
        plot.xname = xname
        xs = np.asarray(cols[xname][:n])
        plot.x = xs.real if np.iscomplexobj(xs) else xs
        plot.y = {k: np.asarray(v[:n], dtype=dtype) for k, v in cols.items() if k != xname}
    return plot


def _infer_order(raw: list[tuple[str, object]]) -> list[str]:
    order: list[str] = []
    for name, _ in raw:
        if name not in order:
            order.append(name)
    return order


def parse_rawfile(text: str) -> list[Plot]:
    """nutascii：一个文件里多段 `Plotname:`（DC/AC/Transient 各一段）。"""
    plots: list[Plot] = []
    chunks = re.split(r"^Plotname:\s*", text, flags=re.M)
    for chunk in chunks[1:]:
        lines = chunk.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:])
        hdr = {"analysis description": title}
        for key in ("Flags", "No. Variables", "No. Points"):
            m = re.search(rf"^{key}:\s*(.+)$", body, flags=re.M)
            if m:
                hdr[key.lower()] = m.group(1).strip()
        m = re.search(r"^No\. Variables:\s*(\d+)", body, flags=re.M)
        nvar = int(m.group(1)) if m else 0
        m = re.search(r"^No\. Points:\s*(\d+)", body, flags=re.M)
        npts = int(m.group(1)) if m else 0
        vm = re.search(r"^Variables:(.*?)^Values:", body, flags=re.M | re.S)
        names: list[str] = []
        if vm:
            for line in vm.group(1).splitlines():
                mm = re.match(r"\s*\d+\s+(\S+)", line)
                if mm:
                    names.append(mm.group(1))
        vals_text = body.split("Values:", 1)[1] if "Values:" in body else ""
        tokens: list[list[str]] = []
        for line in vals_text.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.match(r"^\d+\t", line):
                tokens.append([t.strip() for t in s.split("\t")[1:] if t.strip()])
            else:
                tokens.append([t.strip() for t in s.split() if t.strip()])
        flat: list[str] = []
        for t in tokens:
            flat.extend(t)
        complex_ = "complex" in hdr.get("flags", "").lower()
        cols = {n: [] for n in names}
        idx = 0
        i = 0
        while i < len(flat) and i < nvar * npts:
            name = names[i % nvar] if nvar else ""
            tok = flat[i]
            if complex_:
                if "," in tok:
                    re_, im = tok.split(",", 1)
                    cols[name].append(complex(float(re_), float(im)))
                elif i + 1 < len(flat):
                    cols[name].append(complex(float(tok), float(flat[i + 1])))
                    i += 1
            else:
                cols[name].append(float(tok))
            i += 1
            idx += 1
        p = Plot(name=title.split("`")[-1].rstrip("'") or title, header=hdr)
        p.kind = title.split()[0].lower()
        p.analysis = title
        if names:
            p.xname = names[0]
            p.x = np.asarray(cols[names[0]])
            p.y = {k: np.asarray(v) for k, v in cols.items() if k != names[0]}
        plots.append(p)
    return plots


def read_psf(path: str | Path) -> Plot:
    return parse_psfascii(Path(path).read_text(errors="replace"))


def read_rawfile(path: str | Path) -> list[Plot]:
    return parse_rawfile(Path(path).read_text(errors="replace"))


__all__ = ["Plot", "parse_psfascii", "parse_rawfile", "read_psf", "read_rawfile"]
