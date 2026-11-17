"""GenericSkill：注册表驱动的通用调用（参数顺序、symbol 转换、风险分级）。"""
from .registry import ApiRegistry, risk_of, DEFAULT_REGISTRY
from .skill import Skill

PREFIXES = ("n_", "t_", "S_", "l_", "g_", "x_", "d_", "u_", "b_", "o_", "f_", "s_", "w_", "p_")
DEFAULT_SKIP = __import__("os").path.join(
    __import__("os").path.dirname(__import__("os").path.dirname(
        __import__("os").path.abspath(__file__))),
    "registry", "arglist_skip.json")
ALT = ("|", "/", "nil", "...")


def strip_prefix(name):
    for p in PREFIXES:
        if name.startswith(p) and len(name) > 2:
            return name[2:]
    return name


def is_optional_marker(p):
    n = p.get("name") or ""
    return any(m in n for m in ALT)


class GenericSkill(object):
    def __init__(self, skill=None, registry=None, allow_write=False,
                 auto_signature=True, skip_path=None):
        self.skill = skill or Skill()
        self.reg = registry if isinstance(registry, ApiRegistry) else ApiRegistry(registry)
        self.allow_write = allow_write
        self.auto_signature = auto_signature
        self.skip_path = skip_path or DEFAULT_SKIP
        self.log = []

    # -- skip list --------------------------------------------------- #
    def _load_skip(self):
        import json, os
        if not os.path.isfile(self.skip_path):
            return set()
        try:
            with open(self.skip_path) as f:
                return set(json.load(f).get("skip", []))
        except Exception:
            return set()

    def _add_skip(self, fn, reason):
        import json, os
        cur = self._load_skip()
        if fn in cur:
            return
        cur.add(fn)
        os.makedirs(os.path.dirname(self.skip_path), exist_ok=True)
        with open(self.skip_path, "w") as f:
            json.dump({"skip": sorted(cur), "last_reason": reason}, f, ensure_ascii=False, indent=1)

    # -- 参数编排 ---------------------------------------------------- #
    def order_args(self, fn, args, kwargs):
        params = [p for p in self.reg.params(fn) if "..." not in (p.get("name") or "")]
        if not params:
            if kwargs:
                raise TypeError("%s 无注册参数，不能使用关键字" % fn)
            return list(args)
        names = [strip_prefix(p["name"]) for p in params]
        slots = [None] * len(params)
        given = set()
        for i, v in enumerate(args):
            if i < len(slots):
                slots[i] = v
                given.add(i)
        for k, v in kwargs.items():
            k2 = strip_prefix(k)
            if k2 not in names:
                raise TypeError("%s 未知参数 %r（可用 %s）" % (fn, k, names))
            i = names.index(k2)
            if i in given:
                raise TypeError("%s 参数 %r 重复" % (fn, k))
            slots[i] = v
            given.add(i)
        while slots and slots[-1] is None and (len(slots) - 1) not in given:
            slots.pop()
        for i, s in enumerate(slots):
            if i in given:
                continue
            if not params[i].get("optional") and not is_optional_marker(params[i]):
                raise TypeError("%s 缺少必填参数 %r" % (fn, names[i]))
        return slots

    def coerce(self, fn, values):
        params = [p for p in self.reg.params(fn) if "..." not in (p.get("name") or "")]
        out = []
        for i, v in enumerate(values):
            t = (params[i].get("type") if i < len(params) else "unknown") or "unknown"
            if t == "symbol" and isinstance(v, str):
                try:
                    from skillbridge import Symbol
                    out.append(Symbol(v)); continue
                except Exception:
                    pass
            out.append(v)
        return out

    # -- 按需挖签名（避免"硬挖"整库） ---------------------------------- #
    def ensure_signature(self, fn, persist=True):
        """若注册表无签名，就地对当前会话调用 arglist 并缓存（一次调用，不批量）。"""
        e = self.reg.raw.get(fn)
        if e is not None and (e.get("params") or e.get("arglist_raw") or e.get("zero_args")):
            return {"fn": fn, "source": "registry"}
        try:
            try:
                from skillbridge import Symbol
                arg = Symbol(fn)
            except Exception:
                arg = fn
            raw = self.skill.call("arglist", arg)
        except Exception as ex:
            return {"fn": fn, "source": "none", "error": str(ex)[:80]}
        if raw is None:
            return {"fn": fn, "source": "none", "error": "arglist=nil"}
        text = str(raw)
        rec = self.reg.raw.setdefault(fn, {"name": fn})
        if text in ("nil", "()", ""):
            rec["zero_args"] = True
            rec["param_source"] = "arglist-live"
        else:
            rec["arglist_raw"] = text
            rec["params_source"] = "arglist-live"
        if persist and self.reg.path:
            import json
            with open(self.reg.path, "w") as f:
                json.dump(self.reg.raw, f, ensure_ascii=False)
        return {"fn": fn, "source": "live", "arglist": text}

    # -- 调用 -------------------------------------------------------- #
    def call(self, fn, *args, **kwargs):
        if risk_of(fn) == "write" and not self.allow_write:
            raise PermissionError("拒绝写操作 %s（allow_write=False）" % fn)
        if self.auto_signature and not (self.reg.get(fn) and
                                        (self.reg.get(fn).get("params") or
                                         self.reg.get(fn).get("arglist_raw") or
                                         self.reg.get(fn).get("zero_args"))):
            if fn in self._load_skip():
                raise KeyError("%s 在 skip list 中（arglist 不可得），请显式传参或人工确认" % fn)
            info = self.ensure_signature(fn)
            if info.get("source") == "none":
                self._add_skip(fn, info.get("error", "arglist unavailable"))
                self.log.append({"fn": fn, "ok": False, "skipped": True})
                # 无签名仍允许按位置调用（保持向后兼容）
        ordered = self.order_args(fn, args, kwargs)
        v = self.skill.call(fn, *self.coerce(fn, ordered))
        self.log.append({"fn": fn, "args": len(ordered), "ok": True})
        return v

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return lambda *a, **k: self.call(item, *a, **k)
