"""skill —— SKILL 子集解释器（Virtuoso 的自动化/参数化那一层）。

不是全语言，但覆盖了真实脚本里 90% 的写法：

    过程定义   procedure / lambda / return
    绑定       let / let* / setq / prog / progn
    控制       if / when / unless / cond / foreach / while / for / and / or
    数据       list / cons / car / cdr / append / nth / mapcar / assoc / member
    输出       printf / sprintf / pprint（%L 按 SKILL 字面量打印）
    访问       obj~>prop（getq）
    数据库     dbOpenCellViewByType / dbCreateInst / dbCreateNet / dbCreateTerm /
               dbCreateConnByName / dbSave / dbClose / dbCreateRect

所以为 Virtuoso 写的小脚本（建 cell、摆实例、连线、存盘、打印）可以直接在这跑，
落的是 vdb 的库格式，接着就能 netlist + 仿真。
"""
from __future__ import annotations

import math as _math
import sys
from dataclasses import dataclass
from typing import Any, Callable

from .vdb import (Design, Instance, Layout, Library, Net, Schematic, Shape,
                  parse_skill, unparse)


class SkillError(RuntimeError):
    pass


class Symbol(str):
    """SKILL 符号（和字符串区分开）。"""

    __slots__ = ()


@dataclass
class Procedure:
    name: str
    params: list[str]
    body: list
    env: "Env"

    def __call__(self, *args):
        env = Env(dict(zip(self.params, args)), self.env, self.name)
        return eval_body(self.body, env)


class Env:
    def __init__(self, vars: dict | None = None, parent: "Env | None" = None,
                 name: str = "env"):
        self.vars = vars or {}
        self.parent = parent
        self.name = name

    def find(self, key: str) -> "Env | None":
        e: Env | None = self
        while e:
            if key in e.vars:
                return e
            e = e.parent
        return None

    def get(self, key: str):
        e = self.find(key)
        if e is None:
            raise SkillError(f"unbound variable {key}")
        return e.vars[key]

    def set(self, key: str, val):
        e = self.find(key)
        if e is not None:
            e.vars[key] = val
        else:
            self.vars[key] = val


class Return(Exception):
    def __init__(self, value):
        self.value = value


# --------------------------------------------------------------------------- #
# 读取器
# --------------------------------------------------------------------------- #

def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == ";":                       # 行注释
            while i < n and text[i] != "\n":
                i += 1
        elif c in "()":
            tokens.append(c)
            i += 1
        elif text.startswith("~>", i) or text.startswith("->", i):
            tokens.append("~>")          # defstruct 的 -> 和 cellview 的 ~> 都当取属性
            i += 2
        elif c == "'":
            tokens.append("'")
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            tokens.append('"' + "".join(buf) + '"')
            i = j + 1
        else:
            j = i
            while (j < n and text[j] not in ' \t\r\n()";'
                   and not text.startswith("~>", j) and not text.startswith("->", j)):
                j += 1
            # 紧跟 "(" 的原子标记为函数调用（SKILL 里 f(x) 与 f (x) 不同）
            glue = "\x01" if j < n and text[j] == "(" else ""
            tokens.append(text[i:j] + glue)
            i = j
    return tokens


def read(text: str) -> list:
    """SKILL 读取器：支持 f(x)、a~>b、x = expr、'quote。"""
    toks = tokenize(text)
    pos = 0

    def atom(tok: str):
        if tok.startswith('"'):
            return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if tok == "nil":
            return None
        if tok == "t":
            return True
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            return Symbol(tok)

    PREC = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 3, ">": 3,
            "<=": 3, ">=": 3, "+": 4, "-": 4, "*": 5, "/": 5, "%": 5}

    def parse():
        return parse_expr(0)

    def parse_expr(minp: int):
        nonlocal pos
        left = parse_unary()
        while pos < len(toks) and toks[pos] in PREC and PREC[toks[pos]] >= minp:
            op = toks[pos]
            pos += 1
            right = parse_expr(PREC[op] + 1)
            left = [Symbol(op), left, right]
        return left

    def parse_unary():
        nonlocal pos
        base = parse_one()
        while pos < len(toks) and toks[pos] == "~>":       # a~>b~>c / a->b
            pos += 1
            prop = toks[pos]
            pos += 1
            base = [Symbol("getq"), base, [Symbol("quote"), atom(prop)]]
        if (pos + 1 < len(toks) and toks[pos] == "=" and toks[pos + 1] != "="
                and (isinstance(base, Symbol) or
                     (isinstance(base, list) and isinstance(base[0], Symbol)
                      and base[0] == "getq"))):
            pos += 1
            return [Symbol("setq"), base, parse()]
        return base

    def parse_one():
        nonlocal pos
        tok = toks[pos]
        pos += 1
        if tok == "'":
            return [Symbol("quote"), parse()]
        if tok == "(":
            out = []
            while pos < len(toks) and toks[pos] != ")":
                out.append(parse())
            pos += 1
            return out
        glued = tok.endswith("\x01")
        val = atom(tok.rstrip("\x01"))
        if glued and pos < len(toks) and toks[pos] == "(":     # f(a b) -> (f a b)
            pos += 1
            args = []
            while pos < len(toks) and toks[pos] != ")":
                args.append(parse())
            pos += 1
            return [val] + args
        if (isinstance(val, Symbol) and pos + 1 < len(toks)
                and toks[pos] == "=" and toks[pos + 1] != "="):   # x = expr
            pos += 1
            return [Symbol("setq"), val, parse()]
        return val

    forms = []
    while pos < len(toks):
        forms.append(parse())
    return forms


# --------------------------------------------------------------------------- #
# 求值
# --------------------------------------------------------------------------- #

def eval_body(body: list, env: Env):
    val = None
    for form in body:
        val = ev(form, env)
    return val


def _is_symbol(x) -> bool:
    return isinstance(x, Symbol)


SPECIALS: dict[str, Callable] = {}


def special(name: str):
    def deco(fn):
        SPECIALS[name] = fn
        return fn
    return deco


def _eval_arg(a, env: Env):
    """OCEAN 的关键字参数 ?start 不参与求值，原样传下去。"""
    if isinstance(a, Symbol) and str(a).startswith("?"):
        return a
    return ev(a, env)


def ev(x, env: Env):
    if _is_symbol(x):
        return env.get(x)
    if isinstance(x, (int, float, bool)) or x is None:
        return x
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        if not x:
            return None
        head = x[0]
        if _is_symbol(head):
            sp = SPECIALS.get(head)
            if sp is not None:
                return sp(x[1:], env)
            fn = env.get(head)
            if not callable(fn):
                raise SkillError(f"{head} 不是函数")
            return fn(*[_eval_arg(a, env) for a in x[1:]])
        fn = ev(head, env)
        if not callable(fn):
            raise SkillError(f"{head} 求值结果不可调用")
        return fn(*[ev(a, env) for a in x[1:]])
    raise SkillError(f"无法求值: {x!r}")


@special("quote")
def _quote(args, env):
    return args[0] if args else None


@special("if")
def _if(args, env):
    if ev(args[0], env):
        return ev(args[1], env) if len(args) > 1 else None
    return ev(args[2], env) if len(args) > 2 else None


@special("when")
def _when(args, env):
    return eval_body(args[1:], env) if ev(args[0], env) else None


@special("unless")
def _unless(args, env):
    return None if ev(args[0], env) else eval_body(args[1:], env)


@special("cond")
def _cond(args, env):
    for clause in args:
        if ev(clause[0], env):
            return eval_body(clause[1:], env)
    return None


@special("progn")
@special("prog")
def _progn(args, env):
    return eval_body(args, env)


@special("let")
@special("let*")
def _let(args, env):
    seq = env
    bindings = args[0] or []
    child = Env({}, env)
    for b in bindings:
        if _is_symbol(b):
            child.vars[b] = None
        else:
            name = b[0]
            val = ev(b[1], env) if len(b) > 1 else None
            child.vars[name] = val
    seq = child
    return eval_body(args[1:], seq)


@special("setq")
def _setq(args, env):
    val = None
    for i in range(0, len(args), 2):
        target = args[i]
        val = ev(args[i + 1], env)
        if isinstance(target, Symbol):
            env.set(target, val)
        elif isinstance(target, list) and target and target[0] == "getq":
            obj = ev(target[1], env)
            prop = target[2][1] if isinstance(target[2], list) else target[2]
            set_attr(obj, str(prop), val)
        else:
            raise SkillError(f"setq 的目标不可写: {target!r}")
    return val


@special("setf")
def _setf(args, env):
    return _setq(args, env)


@special("foreach")
def _foreach(args, env):
    var = args[0]
    seq = ev(args[1], env) or []
    val = None
    for item in seq:
        env.set(var, item)
        val = eval_body(args[2:], env)
    return val


@special("while")
def _while(args, env):
    val = None
    guard = 0
    while ev(args[0], env):
        val = eval_body(args[1:], env)
        guard += 1
        if guard > 1_000_000:
            raise SkillError("while 循环次数过多")
    return val


@special("for")
def _for(args, env):
    var, start, stop = args[0], ev(args[1], env), ev(args[2], env)
    val = None
    for i in range(int(start), int(stop) + 1):
        env.set(var, i)
        val = eval_body(args[3:], env)
    return val


@special("return")
def _return(args, env):
    raise Return(ev(args[0], env) if args else None)


@special("&&")
def _and2(args, env):
    return _and(args, env)


@special("||")
def _or2(args, env):
    return _or(args, env)


@special("and")
def _and(args, env):
    val = True
    for a in args:
        val = ev(a, env)
        if not val:
            return val
    return val


@special("or")
def _or(args, env):
    val = None
    for a in args:
        val = ev(a, env)
        if val:
            return val
    return val


@special("lambda")
def _lambda(args, env):
    return Procedure("lambda", list(args[0]), args[1:], env)


@special("procedure")
def _procedure(args, env):
    if isinstance(args[0], list):          # procedure(foo(a) ...) 的粘连写法
        name, params, body = args[0][0], args[0][1:], args[1:]
    else:
        name, params, body = args[0], list(args[1] or []), args[2:]
    proc = Procedure(name, params, body, env)
    env.set(name, proc)
    return Symbol(name)


@special("getq")
def _getq(args, env):
    obj = ev(args[0], env)
    prop = args[1]
    if isinstance(prop, list) and prop and _is_symbol(prop[0]) and prop[0] == "quote":
        prop = prop[1]
    return get_attr(obj, str(prop))


@special("setf_getq")           # 兼容写法，实际用 db 修改接口
def _noop(args, env):
    return None


# --------------------------------------------------------------------------- #
# 属性访问
# --------------------------------------------------------------------------- #

_ATTR_MAP = {
    "name", "cellname", "libname", "viewname", "xy", "orient", "numinst",
    "instances", "nets", "terminals", "instterms", "direction", "net",
    "sigtype", "schematic", "pins", "params", "cell", "lib", "view",
    "shapes", "layout", "bbox",
}


class SkillStruct:
    """defstruct 的极简替身：属性读写走 _props。"""

    def __init__(self, **kw):
        object.__setattr__(self, "_props", dict(kw))

    def __getattr__(self, name):
        props = object.__getattribute__(self, "_props")
        if name in props:
            return props[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_props")[name] = value

    def __repr__(self):
        return "<struct " + ",".join(sorted(object.__getattribute__(self, "_props"))) + ">"


def set_attr(obj, prop: str, value) -> None:
    from .registry import RemoteHandle
    if isinstance(obj, RemoteHandle):
        if obj.backend is None:
            raise SkillError(f"远端对象 {obj} 没有绑定代理")
        obj.backend.set_attr(obj, prop, value)
        return
    if isinstance(obj, SkillStruct):
        obj._props[prop] = value
    elif hasattr(obj, prop):
        setattr(obj, prop, value)
    elif isinstance(obj, dict):
        obj[prop] = value
    else:
        raise SkillError(f"{type(obj).__name__} 没有可写属性 {prop}")


def get_attr(obj, prop: str):
    from .registry import RemoteHandle
    if isinstance(obj, RemoteHandle):
        if obj.backend is None:
            raise SkillError(f"远端对象 {obj} 没有绑定代理")
        return obj.backend.get_attr(obj, prop)
    key = prop.lower().replace("_", "")
    if key in ("name", "cellname", "libname", "viewname", "xy", "orient",
               "numinst", "direction", "sigtype"):
        if key == "cellname":
            return obj.cell
        if key == "libname":
            return obj.lib
        if key == "viewname":
            return obj.view
        if key == "numinst":
            return obj.num_inst
        if key == "sigtype":
            return obj.sig_type
        return getattr(obj, key if key != "name" else "name", None)
    if key == "instances" and isinstance(obj, SchView):
        return [InstView(i, obj) for i in obj.sch.insts]
    if key == "instances" and isinstance(obj, LayView):
        return obj.layout.instances
    if key == "nets":
        return obj.sch.nets if isinstance(obj, SchView) else None
    if key == "terminals":
        return obj.sch.pins if isinstance(obj, SchView) else None
    if key == "instterms":
        if isinstance(obj, InstView):
            return [InstTermView(obj, t) for t in master_terminals(obj)]
        if isinstance(obj, Net):
            return list(obj.terms)
    if key == "net" and isinstance(obj, (Pin,)):
        return next((n for n in obj_pins_net(obj)), None)
    if key == "bbox" and isinstance(obj, LayView):
        return obj.layout.bbox()
    return getattr(obj, prop, None)


def master_terminals(iv: "InstView") -> list[str]:
    """实例的端子来自 master（PDK 器件看映射表，子电路看引脚表），不是连接表。"""
    from .netlist import DeviceMap
    spec = DeviceMap().get(iv.inst.lib, iv.inst.cell)
    if spec is not None:
        return list(spec.nodes)
    design = iv.cv.design
    if design is not None:
        sch = design.find(iv.inst.lib, iv.inst.cell)
        if sch is not None:
            return [p.name for p in sch.pins]
    return list(iv.inst.terms)


def obj_pins_net(pin: Pin):
    return []                                    # 由 SchView.pin_net 提供


class SchView:
    """dbOpenCellViewByType 的返回值（对应一个 cellview）。"""

    def __init__(self, lib: Library, cell: str, view: str, schematic: Schematic,
                 design: Design | None = None):
        self.lib, self.cell, self.view = lib, cell, view
        self.sch = schematic
        self.design = design
        self.dirty = False

    def pin_net(self, name: str) -> str | None:
        return name if any(p.name == name for p in self.sch.pins) else None

    def __repr__(self):
        return f"<cellview {self.lib.name}/{self.cell}/{self.view}>"


class LayView:
    def __init__(self, lib: Library, cell: str, view: str, layout: Layout):
        self.lib, self.cell, self.view = lib, cell, view
        self.layout = layout
        self.dirty = False

    def __repr__(self):
        return f"<layoutView {self.lib.name}/{self.cell}/{self.view}>"


class InstView:
    def __init__(self, inst: Instance, cv: SchView):
        self.inst, self.cv = inst, cv

    @property
    def name(self):
        return self.inst.name

    @property
    def cell(self):
        return self.inst.cell

    @property
    def lib(self):
        return self.inst.lib

    @property
    def view(self):
        return self.inst.view

    @property
    def xy(self):
        return list(self.inst.xy)

    @property
    def orient(self):
        return self.inst.orient

    @property
    def num_inst(self):
        return self.inst.num_inst


class InstTermView:
    def __init__(self, iv: InstView, term: str):
        self.iv, self.term = iv, term

    @property
    def name(self):
        return self.term

    @property
    def inst(self):
        return self.iv

    @property
    def net(self):
        return self.iv.inst.terms.get(self.term)


# --------------------------------------------------------------------------- #
# 内置函数
# --------------------------------------------------------------------------- #

def _list(*a):
    return None if not a else (a[0] if len(a) == 1 else list(a))


BUILTINS: dict[str, Callable] = {}


def builtin(name: str, *aliases: str):
    def deco(fn):
        BUILTINS[name] = fn
        for al in aliases:
            BUILTINS[al] = fn
        return fn
    return deco


builtin("car")(lambda x: x[0] if x else None)
builtin("cdr")(lambda x: x[1:] if x else None)
builtin("cadr")(lambda x: x[1] if x and len(x) > 1 else None)
builtin("caddr")(lambda x: x[2] if x and len(x) > 2 else None)
builtin("cddr")(lambda x: x[2:] if x else None)
builtin("caar")(lambda x: x[0][0] if x and x[0] else None)
builtin("cons")(lambda a, b: [a] + (b or []))
builtin("list")(lambda *a: list(a) if a else None)
builtin("append")(lambda *a: sum((list(x or []) for x in a), []))
builtin("length")(lambda x: len(x) if x else 0)
builtin("nth")(lambda i, x: x[int(i)] if x and 0 <= int(i) < len(x) else None)
builtin("reverse")(lambda x: list(reversed(x or [])))
builtin("member")(lambda a, x: (lambda i: x[i:] if i is not None else None)(
    next((i for i, v in enumerate(x or []) if v == a), None)))
builtin("assoc")(lambda k, x: next((p for p in (x or []) if p and p[0] == k), None))
builtin("mapcar")(lambda f, *xs: [f(*args) for args in zip(*xs)])
builtin("mapc")(lambda f, *xs: [f(*a) for a in zip(*xs)][-1] if xs and xs[0] else None)
builtin("apply")(lambda f, args: f(*args))
builtin("null")(lambda x: x is None or x == [] or x is False)
builtin("not")(lambda x: x is None or x == [] or x is False)
builtin("eq")(lambda a, b: a is b or a == b)
builtin("equal")(lambda a, b: a == b)
builtin("neq")(lambda a, b: a != b)
builtin("numberp")(lambda x: isinstance(x, (int, float)))
builtin("stringp")(lambda x: isinstance(x, str) and not isinstance(x, Symbol))
builtin("symbolp")(lambda x: isinstance(x, Symbol))
builtin("listp")(lambda x: isinstance(x, list) or x is None)
builtin("atom")(lambda x: not isinstance(x, list))
builtin("setarray")(lambda *a: None)

# 常用数学（文档里大量出现）
for _n, _f in (("sin", _math.sin), ("cos", _math.cos), ("tan", _math.tan),
               ("asin", _math.asin), ("acos", _math.acos), ("atan", _math.atan),
               ("sinh", _math.sinh), ("cosh", _math.cosh), ("tanh", _math.tanh),
               ("atan2", _math.atan2), ("log2", _math.log2),
               ("random", lambda *a: __import__("random").random())):
    BUILTINS[_n] = _f
builtin("linRg")(lambda a, b, n: [a + (b - a) * i / max(n - 1, 1) for i in range(int(n))])
builtin("sub1")(lambda x: x - 1)

# --- 真实脚本里高频、之前缺的内置 -------------------------------------- #
builtin("caddr")(lambda x: x[2] if x and len(x) > 2 else None)
builtin("cadddr")(lambda x: x[3] if x and len(x) > 3 else None)
builtin("cdddr")(lambda x: x[3:] if x else None)
builtin("cddddr")(lambda x: x[4:] if x and len(x) > 4 else None)
builtin("caadr")(lambda x: x[0][1] if x and x[0] and len(x[0]) > 1 else None)
builtin("cdadr")(lambda x: x[1][1:] if x and len(x) > 1 and x[1] else None)
builtin("last")(lambda x: x[-1] if x else None)
builtin("butlast")(lambda x: x[:-1] if x else None)
builtin("append1")(lambda x, y: list(x or []) + [y])
builtin("nappend")(lambda *a: sum((list(x or []) for x in a), []))
builtin("copy")(lambda x: list(x) if isinstance(x, list) else x)
builtin("nreverse")(lambda x: list(reversed(x or [])))
builtin("setof")(lambda f, x: [v for v in (x or []) if f(v)])
builtin("exists")(lambda f, x: next((v for v in (x or []) if f(v)), None))
builtin("forall")(lambda f, x: all(f(v) for v in (x or [])))
builtin("remove")(lambda a, x: [v for v in (x or []) if v != a])
builtin("sort")(lambda x, cmp=None: sorted(x or [], key=cmp) if cmp else sorted(x or []))
builtin("mapcan")(lambda f, *xs: sum((list(r or []) for r in
                                      [f(*a) for a in zip(*xs)]), []))
builtin("funcall")(lambda f, *a: f(*a))
builtin("error")(lambda *a: (_ for _ in ()).throw(SkillError(" ".join(map(str, a)))))
builtin("warn")(lambda fmt, *a: _printf(str(fmt) + "\n", a))
builtin("fprintf")(lambda port, fmt, *a: _printf(str(fmt), a))
builtin("errset")(lambda x, *a: [x])
builtin("getVersion")(lambda: "vlight-skill/1.0")
builtin("envGetVal")(lambda *a: None)
builtin("envSetVal")(lambda *a: True)
builtin("hiGetCurrentWindow")(lambda: None)      # 无 GUI，返回 nil
builtin("centerBox")(lambda *a: a[0] if a else None)
builtin("dbTransformPoint")(lambda pt, xf=None: pt)
builtin("ncons")(lambda x: [x])
builtin("nconc")(lambda *ls: sum((list(x or []) for x in ls), []))
builtin("make_sevVariableStruct", "make_sevOutputStruct",
        "make_sevAnalysisStruct", "make_sevModelStruct")(lambda *a, **k: SkillStruct())
builtin("remexit")(lambda: None)
builtin("exit")(lambda: None)

for _name, _fn in (("+", lambda *a: sum(a)), ("-", lambda a, *r: a - sum(r) if r else -a),
                   ("*", lambda *a: _product(a)), ("/", lambda a, b: a / b if b else 0)):
    BUILTINS[_name] = _fn
BUILTINS.update({
    "mod": lambda a, b: a % b, "abs": abs, "min": lambda *a: min(a),
    "max": lambda *a: max(a), "sqrt": _math.sqrt, "exp": _math.exp,
    "log": _math.log, "floor": _math.floor, "round": round, "float": float,
    "int": int, "fix": lambda x: int(x), "pow": pow,
    "<": lambda a, b: a < b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "strcat": lambda *a: "".join(str(x) for x in a),
    "strlen": lambda s: len(str(s)),
    "substring": lambda s, i, n=None: str(s)[int(i):] if n is None else str(s)[int(i):int(i) + int(n)],
    "atof": lambda s: float(s),
    "atoi": lambda s: int(float(s)),
    "lowercase": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "sprintf": lambda nil, fmt, *a: _format(fmt, a),
    "printf": lambda fmt, *a: _printf(fmt, a),
    "println": lambda *a: print(*[_to_text(x) for x in a]),
    "print": lambda *a: print(*[_to_text(x) for x in a]),
    "pprint": lambda x: print(skill_repr(x)) or x,
    "getqq": lambda x: x,
})


def _product(a):
    out = 1
    for v in a:
        out *= v
    return out


def skill_repr(v) -> str:
    """SKILL 打印格式：符号不加引号，字符串加引号，nil/t 原样。"""
    if isinstance(v, Symbol):
        return str(v)
    if v is None:
        return "nil"
    if v is True:
        return "t"
    if isinstance(v, bool):
        return "t" if v else "nil"
    if isinstance(v, str):
        return '"%s"' % v.replace('"', '\\"')
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "(" + " ".join(skill_repr(x) for x in v) + ")"
    if isinstance(v, SkillStruct):
        return "<struct>"
    return str(v)


def _to_text(x) -> str:
    if isinstance(x, str) and not isinstance(x, Symbol):
        return x
    return skill_repr(x)


def _format(fmt: str, args) -> str:
    out, ai = [], 0
    i = 0
    fmt = str(fmt)
    while i < len(fmt):
        if fmt[i] == "%" and i + 1 < len(fmt):
            code = fmt[i + 1]
            if code == "%":
                out.append("%")
                i += 2
                continue
            val = args[ai] if ai < len(args) else None
            ai += 1
            if code == "L":
                out.append(skill_repr(val))
            elif code in "sS":
                out.append(_to_text(val))
            elif code in "dDiI":
                out.append(str(int(val)))
            elif code in "fFeEgG":
                out.append(f"{float(val):{code}}")
            elif code == "n":
                out.append("\n")
            else:
                out.append(str(val))
            i += 2
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out)


_PRINT = {"text": ""}


def _printf(fmt, args):
    txt = _format(fmt, args)
    _PRINT["text"] += txt
    sys.stdout.write(txt)
    return True


# --------------------------------------------------------------------------- #
# 数据库 API
# --------------------------------------------------------------------------- #

_LAST_SESSION = None


class Session:
    """一次 SKILL 运行的环境：持有 Design（库集合）。"""

    def __init__(self, design: Design | None = None, out: list[str] | None = None,
                 registry=None, bind_registry: bool = True):
        from .registry import DEFAULT
        self.design = design or Design()
        self.output: list[str] = out if out is not None else []
        self.env = Env(dict(BUILTINS), None, "global")
        self.registry = registry if registry is not None else DEFAULT
        global _LAST_SESSION
        _LAST_SESSION = self
        self._install()
        if bind_registry:
            self.registry.load_default_index()
            for name, fn in list(self.env.vars.items()):      # 先标 native
                if callable(fn) and self.registry.has(name):
                    self.registry.register(name, "native", fn)
            self.registry.mark_implemented(set(SPECIALS) | set(BUILTINS))
            self.registry.bind_all(self.env.vars)             # 其余补桩

    # -- 注册内置 ---------------------------------------------------------- #
    def _install(self):
        e = self.env.vars
        e.update({
            "dbOpenCellViewByType": self.db_open,
            "dbCreateInst": self.db_create_inst,
            "dbCreateNet": self.db_create_net,
            "dbCreateTerm": self.db_create_term,
            "dbCreateConnByName": self.db_create_conn,
            "dbCreateInstTerm": self.db_create_conn,
            "dbSave": self.db_save,
            "dbClose": self.db_close,
            "dbCreateRect": self.db_create_rect,
            "dbCreatePolygon": self.db_create_poly,
            "dbGetNet": self.db_get_net,
            "ddGetObj": self.dd_get_obj,
            "ddUpdateLibList": self.dd_update_lib_list,
            "ddSetForcedLib": self.dd_set_forced_lib,
            "schCreateWire": self.sch_create_wire,
            "schCreateWireLabel": self.sch_create_wire_label,
            "dbCreateLabel": self.db_create_label,
            "geAddInst": self.db_create_inst,
            "dbReplaceProp": self.db_replace_prop,
            "vlightSetParam": self.db_replace_prop,
            "schCheck": self.sch_check,
            "getq": lambda o, p: get_attr(o, str(p)),
            "car": e["car"], "cdr": e["cdr"],
            "sprintf": e["sprintf"], "printf": e["printf"],
        })

    # -- 库/视图 ----------------------------------------------------------- #
    def lib(self, name: str, create: bool = True) -> Library:
        if name in self.design.libs:
            return self.design.libs[name]
        if not create:
            raise SkillError(f"库 {name} 不存在")
        return self.design.add(Library(name, f"<skill>/{name}"))

    def db_open(self, libname, cell, view="schematic", viewtype=None, mode="r"):
        lib = self.lib(str(libname), create=str(mode) in ("w", "a"))
        c = lib.cell(str(cell), create=str(mode) in ("w", "a"))
        if str(view) == "layout" or str(viewtype) == "maskLayout":
            lay = c.layouts.get(str(view))
            if lay is None:
                if str(mode) not in ("w", "a"):
                    raise SkillError(f"{libname}/{cell}/{view} 不存在")
                lay = Layout(str(cell), lib.name)
                c.layouts[str(view)] = lay
            return LayView(lib, str(cell), str(view), lay)
        sch = c.schematics.get(str(view))
        if sch is None:
            if str(mode) not in ("w", "a"):
                raise SkillError(f"{libname}/{cell}/{view} 不存在")
            sch = Schematic(str(cell), lib.name)
            c.schematics[str(view)] = sch
        return SchView(lib, str(cell), str(view), sch, self.design)

    # -- 原理图编辑 -------------------------------------------------------- #
    def db_create_inst(self, cv: SchView, master, name, xy=None, orient="R0"):
        if isinstance(master, str) and " " in master:
            mlib, mcell = master.split()[:2]
        elif isinstance(master, str) and "/" in master:
            mlib, mcell = master.split("/")[:2]
        elif isinstance(master, (list, tuple)) and len(master) >= 2:
            mlib, mcell = str(master[0]), str(master[1])
        else:
            mlib, mcell = str(cv.lib.name), str(master)
        pos = (0.0, 0.0)
        if isinstance(xy, (list, tuple)) and len(xy) >= 2:
            pos = (float(xy[0]), float(xy[1]))
        inst = cv.sch.add_inst(
            Instance(str(name), mlib, mcell, "symbol", pos, str(orient)))
        cv.dirty = True
        return InstView(inst, cv)

    def db_create_net(self, cv: SchView, name):
        cv.dirty = True
        return cv.sch.add_net(str(name))

    def db_create_term(self, arg, name, direction="inputOutput"):
        if isinstance(arg, Net):
            net, cv = arg, getattr(arg, "_cv", None)
            net.name = str(name)
            return net
        if isinstance(arg, SchView):
            return arg.sch.add_pin(str(name), str(direction))
        raise SkillError("dbCreateTerm 第一个参数要是 net 或 cellview")

    def db_create_conn(self, inst_term, net):
        if isinstance(inst_term, InstTermView):
            iv = inst_term.iv
            netname = net.name if isinstance(net, Net) else str(net)
            iv.cv.sch.connect(iv.inst.name, inst_term.term, netname)
            iv.cv.dirty = True
            return iv.cv.sch.net(netname)
        if isinstance(inst_term, (list, tuple)) and len(inst_term) == 2:
            raise SkillError("dbCreateConnByName 需要 instTerm 或 (inst term)")
        raise SkillError("未知的 instTerm")

    def db_save(self, cv):
        if isinstance(cv, SchView):
            lib = cv.lib
            cell = lib.cell(cv.cell, create=True)
            cell.schematics[cv.view] = cv.sch
        elif isinstance(cv, LayView):
            lib = cv.lib
            cell = lib.cell(cv.cell, create=True)
            cell.layouts[cv.view] = cv.layout
        else:
            return True
        cv.dirty = False
        return True

    def db_close(self, cv):
        return self.db_save(cv)

    def db_replace_prop(self, inst, name, value=None, prop_type=None):
        """实例参数写入（对应 CDF 参数改写；网表就靠它取值）。"""
        target = inst.inst if isinstance(inst, InstView) else inst
        if not isinstance(target, Instance):
            raise SkillError("dbReplaceProp 第一个参数要是 instance")
        key = str(name)
        val = value
        if isinstance(val, str) and not isinstance(val, Symbol):
            pass
        target.params[key] = val
        return val

    # -- dd* / sch* 常用入口 ------------------------------------------- #
    def dd_get_obj(self, libname, cell=None, view=None):
        lib = self.lib(str(libname), create=False)
        if cell is None:
            class _LibRef:
                def __init__(self, l):
                    self._l = l
                    self.name = l.name
                    self.cells = list(l.cells.values())
            return _LibRef(lib)
        c = lib.cell(str(cell))
        return c

    def dd_update_lib_list(self, *a):
        return True

    def dd_set_forced_lib(self, *a):
        return True

    def sch_create_wire(self, cv, points, layer="wire", purpose="drawing", width=0.0):
        from .vdb import Shape
        if not isinstance(cv, SchView):
            raise SkillError("schCreateWire 需要 schematic cellview")
        cv.sch.shapes.append(Shape(kind="wire", layer=str(layer), purpose=str(purpose),
                                   points=[(float(p[0]), float(p[1])) for p in points]))
        cv.dirty = True
        return cv.sch.shapes[-1]

    def sch_create_wire_label(self, cv, net, points):
        from .vdb import Shape
        cv.sch.shapes.append(Shape(kind="label", layer="wire", text=str(net),
                                   points=[(float(points[0]), float(points[1]))]))
        return cv.sch.shapes[-1]

    def db_create_label(self, cv, layer, purpose, xy, text, *a):
        from .vdb import Shape
        target = cv.layout if isinstance(cv, LayView) else cv.sch
        target.shapes.append(Shape(kind="label", layer=str(layer), purpose=str(purpose),
                                   text=str(text),
                                   points=[(float(xy[0]), float(xy[1]))]))
        return target.shapes[-1]

    def db_get_net(self, cv: SchView, name):
        return cv.sch.net(str(name))

    def db_create_rect(self, cv: LayView, layer, purpose, bbox):
        x0, y0, x1, y1 = [float(v) for v in bbox]
        cv.layout.add(str(layer), [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                      str(purpose or "drawing"))
        cv.dirty = True
        return cv.layout.shapes[-1]

    def db_create_poly(self, cv: LayView, layer, purpose, points):
        cv.layout.add(str(layer), [(float(p[0]), float(p[1])) for p in points],
                      str(purpose or "drawing"))
        cv.dirty = True
        return cv.layout.shapes[-1]

    def sch_check(self, cv: SchView):
        issues = cv.sch.check()
        for msg in issues:
            print(f"*WARNING* {msg}")
        return issues

    # -- 运行 -------------------------------------------------------------- #
    def run(self, text: str):
        forms = read(text)
        val = None
        for form in forms:
            try:
                val = ev(form, self.env)
            except Return as r:
                return r.value
            except SkillError:
                raise
        return val

    def run_file(self, path: str):
        return self.run(open(path, encoding="utf-8", errors="replace").read())


def run_skill(text: str, design: Design | None = None) -> tuple[Any, Session]:
    s = Session(design)
    return s.run(text), s


__all__ = ["Session", "run_skill", "read", "eval_body", "ev", "Env", "Symbol",
           "SkillError", "SchView", "LayView", "InstView", "InstTermView",
           "BUILTINS", "SPECIALS", "Procedure"]
