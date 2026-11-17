"""可用性判定：headless 可用 / 需要 GUI 窗口 / 签名缺失。"""
from .registry import ApiRegistry


def availability(reg, fn):
    e = reg.get(fn)
    if e is None:
        return {"fn": fn, "state": "NOT_IN_REGISTRY", "reason": "函数未收录"}
    ctx = e.get("contexts") or []
    needs_window = (any(c in ("schematic-window", "layout-window") for c in ctx)
                    and "headless" not in ctx)
    has_sig = bool(e.get("params") or e.get("arglist_raw") or e.get("zero_args"))
    if needs_window:
        return {"fn": fn, "state": "NEEDS_WINDOW",
                "reason": "需要示意图/版图窗口上下文", "contexts": ctx}
    if not has_sig:
        return {"fn": fn, "state": "NO_SIGNATURE",
                "reason": "无签名，只能按位置试调（不推荐）"}
    return {"fn": fn, "state": "HEADLESS_OK", "reason": "可在 headless 会话调用",
            "contexts": ctx}


def annotate_needs_window(reg, path=None):
    """给注册表打 needs_window 标记（就地修改并返回统计）。"""
    n = 0
    for name, e in reg.raw.items():
        ctx = e.get("contexts") or []
        e["needs_window"] = bool(
            any(c in ("schematic-window", "layout-window") for c in ctx)
            and "headless" not in ctx)
        if e["needs_window"]:
            n += 1
    if path:
        import json
        with open(path, "w") as f:
            json.dump(reg.raw, f, ensure_ascii=False)
    return {"needs_window": n, "total": len(reg.raw)}


def headless_stats(reg):
    ok = win = nosig = 0
    for name, e in reg.raw.items():
        if e.get("needs_window"):
            win += 1
        elif e.get("params") or e.get("arglist_raw") or e.get("zero_args"):
            ok += 1
        else:
            nosig += 1
    return {"headless_ok": ok, "needs_window": win, "no_signature": nosig,
            "total": len(reg.raw)}
