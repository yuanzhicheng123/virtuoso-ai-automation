"""Netlist Semantic Diff：比对两套 IR 的器件/模型/端子/参数/网络。"""
from __future__ import annotations

from .circuit_ir import Circuit


def _num(v) -> float | None:
    try:
        s = str(v).strip().rstrip("fF")
        mult = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
                "u": 1e-6, "n": 1e-9, "p": 1e-12, "a": 1e-18}
        for suf, m in sorted(mult.items(), key=lambda kv: -len(kv[0])):
            if s.lower().endswith(suf):
                return float(s[: -len(suf)]) * m
        return float(s)
    except (TypeError, ValueError):
        return None


def diff(a: Circuit, b: Circuit, rel_tol: float = 1e-6) -> dict:
    issues, notes = [], []
    da = {d.name: d for d in a.devices}
    db = {d.name: d for d in b.devices}
    if set(da) != set(db):
        issues.append({"kind": "device_set", "only_a": sorted(set(da) - set(db)),
                       "only_b": sorted(set(db) - set(da))})
    for name in sorted(set(da) & set(db)):
        x, y = da[name], db[name]
        if x.model != y.model:
            issues.append({"kind": "model", "device": name,
                           "a": x.model, "b": y.model})
        if len(x.terminals) != len(y.terminals):
            issues.append({"kind": "terminal_count", "device": name,
                           "a": list(x.terminals), "b": list(y.terminals)})
        keys = set(x.params) | set(y.params)
        for k in sorted(keys):
            va, vb = x.params.get(k), y.params.get(k)
            if k in ("w", "l", "m", "nf"):
                na, nb = _num(va), _num(vb)
                if na is None or nb is None:
                    if str(va) != str(vb):
                        issues.append({"kind": "param", "device": name,
                                       "param": k, "a": va, "b": vb})
                elif abs(na - nb) > max(abs(na), abs(nb)) * rel_tol:
                    issues.append({"kind": "param", "device": name, "param": k,
                                   "a": va, "b": vb,
                                   "delta": f"{(nb-na)/na:+.3%}" if na else "n/a"})
    if len(a.nets) != len(b.nets):
        notes.append({"kind": "net_count", "a": len(a.nets), "b": len(b.nets)})
    return {"ok": not issues, "issues": issues, "notes": notes,
            "devices_a": len(a.devices), "devices_b": len(b.devices),
            "hash_a": a.hash(), "hash_b": b.hash()}
