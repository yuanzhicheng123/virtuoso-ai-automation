"""通用 SKILL 调用层：注册表驱动，覆盖整个函数库（74,785 个符号）。

- 参数顺序按注册表 arglist 还原
- 类型按注册表声明转换（symbol/string/number/list）
- 风险按名称前缀分类（只读 / 写 / GUI 上下文）
"""
import json, os, re

REG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "audit", "2026-10-22", "registry", "api_registry.json")

WRITE_PREFIX = ("dbCreate", "dbSet", "dbReplace", "dbDelete", "dbSave", "dbClose",
                "schCreate", "schDelete", "leCreate", "leDelete", "rodCreate",
                "ddCreate", "ddDelete", "hiSet", "geCreate", "deSet", "techSet",
                "cdfCreate", "cdfSet", "cdfUpdate", "axlSet", "artSet")
PREFIXES = ("n_", "t_", "S_", "l_", "g_", "x_", "d_", "u_", "b_", "o_", "f_", "s_", "w_", "p_")


def load_registry(path=REG):
    with open(path) as f:
        return json.load(f)


def strip_prefix(name):
    for p in PREFIXES:
        if name.startswith(p) and len(name) > 2:
            return name[2:]
    return name


def entry_of(reg, fn):
    return reg.get(fn)


def risk_of(fn):
    return "write" if fn.startswith(WRITE_PREFIX) else "read"


ALTERNATION = ("|", "/", "nil", "...")


def is_optional_marker(param):
    n = param.get("name") or ""
    return any(m in n for m in ALTERNATION)


def is_vararg(param):
    return "..." in (param.get("name") or "")


def order_args(entry, args, kwargs):
    """按注册表参数顺序把 args/kwargs 排成位置参数。"""
    params = [p for p in (entry.get("params") or []) if not is_vararg(p)]
    if not params:
        if kwargs:
            raise TypeError("该函数无注册参数，不能用关键字: %s" % sorted(kwargs))
        return list(args)
    names = [strip_prefix(p["name"]) for p in params]
    slots = [None] * len(params)
    for i, v in enumerate(args):
        if i < len(slots):
            slots[i] = v
    for k, v in kwargs.items():
        k2 = strip_prefix(k)
        if k2 not in names:
            raise TypeError("未知参数 %r（可用: %s）" % (k, names))
        idx = names.index(k2)
        if slots[idx] is not None:
            raise TypeError("参数 %r 重复给出" % k)
        slots[idx] = v
    # 去掉尾部未给出的可选参数
    while slots and slots[-1] is None:
        slots.pop()
    for i, s in enumerate(slots):
        if s is None:
            if not params[i].get("optional") and not is_optional_marker(params[i]):
                raise TypeError("缺少必填参数 %r" % names[i])
    return [s for s in slots if s is not None]


def coerce(entry, values):
    """按注册表类型做最小转换（symbol 需要 SKILL symbol，不能用字符串）。"""
    params = [p for p in (entry.get("params") or []) if not is_vararg(p)]
    out = []
    for i, v in enumerate(values):
        t = (params[i].get("type") if i < len(params) else "unknown") or "unknown"
        if t == "symbol" and isinstance(v, str):
            try:
                from skillbridge import Symbol
                out.append(Symbol(v))
                continue
            except Exception:
                pass
        out.append(v)
    return out


def call(session, reg, fn, *args, **kwargs):
    e = entry_of(reg, fn)
    if e is None:
        raise KeyError("未注册函数: %s" % fn)
    ordered = order_args(e, args, kwargs)
    return session.call(fn, *coerce(e, ordered))


def batch(session, reg, cases, allow_write=False):
    """cases: [(fn, [args...]) ...]；返回逐项结果。"""
    out = []
    for c in cases:
        fn, args = c[0], list(c[1])
        rec = {"fn": fn, "risk": risk_of(fn)}
        if rec["risk"] == "write" and not allow_write:
            rec.update(ok=False, error="DENIED_WRITE")
            out.append(rec); continue
        try:
            v = call(session, reg, fn, *args)
            rec.update(ok=True, value=str(v)[:60])
        except Exception as ex:
            rec.update(ok=False, error="%s: %s" % (type(ex).__name__, str(ex)[:80]))
        out.append(rec)
    return out


def coverage(reg):
    n = len(reg)
    sig = sum(1 for v in reg.values() if v.get("params"))
    ro = sum(1 for k, v in reg.items() if v.get("params") and risk_of(k) == "read")
    gui = sum(1 for v in reg.values()
              if any(c in ("schematic-window", "layout-window") for c in (v.get("contexts") or []))
              and "headless" not in (v.get("contexts") or []))
    return {"total": n, "with_signature": sig, "read_only_with_signature": ro,
            "gui_only": gui, "coverage_all": round(sig / float(n), 4)}
