"""fnd_parse —— 只读解析 Cadence SKILL Finder 的 .fnd 索引。

.fnd 是 Lisp 形式的 (name syntax description) 三元组列表，例如：
    ("geNewWindow" "geNewWindow( [ w_windowId ] )\n=> w_windowId / nil"
     "Makes a copy of a window.")

只读：仅读取文件、输出 JSON，不接触任何设计数据。
"""
from __future__ import annotations

import json
import os
import re
import sys

TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def parse(text: str):
    toks = TOKEN.findall(text)
    pos = 0

    def read():
        nonlocal pos
        tok = toks[pos]; pos += 1
        if tok == "(":
            out = []
            while pos < len(toks) and toks[pos] != ")":
                out.append(read())
            pos += 1
            return out
        if tok.startswith('"'):
            body = tok[1:-1]
            return body.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return tok

    forms = []
    while pos < len(toks):
        forms.append(read())
    return forms


def split_args(syntax: str) -> list[str]:
    m = re.search(r"\((.*?)\)", syntax, re.S)
    if not m:
        return []
    body = m.group(1)
    args, depth, buf = [], 0, ""
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch.isspace() and depth == 0:
            if buf:
                args.append(buf); buf = ""
        else:
            buf += ch
    if buf:
        args.append(buf)
    out = []
    for a in args:
        if a.startswith("["):
            out.append({"name": a.strip("[]"), "optional": True})
        elif a in ("{", "}"):
            continue
        elif a.startswith("{"):
            out.append({"name": a.strip("{}"), "optional": False, "enum": True})
        else:
            out.append({"name": a, "optional": False})
    return out


def main() -> int:
    root, out = sys.argv[1], sys.argv[2]
    funcs, files = {}, 0
    for dirpath, _dirs, names in os.walk(root):
        for fn in sorted(names):
            if not fn.endswith(".fnd"):
                continue
            path = os.path.join(dirpath, fn)
            files += 1
            manual = fn[:-4]
            tool = os.path.basename(os.path.dirname(path))
            try:
                forms = parse(open(path, errors="replace").read())
            except Exception as exc:                       # noqa: BLE001
                print(f"parse fail {path}: {exc}", file=sys.stderr)
                continue
            for form in forms:
                if not (isinstance(form, list) and len(form) >= 2
                        and isinstance(form[0], str)):
                    continue
                name = form[0]
                if not re.match(r"^[A-Za-z_][\w?!]*$", name):
                    continue
                entry = funcs.setdefault(name, {
                    "name": name, "manuals": [], "syntax": form[1],
                    "desc": form[2] if len(form) > 2 else "",
                    "tools": [], "args": split_args(form[1])})
                if manual not in entry["manuals"]:
                    entry["manuals"].append(manual)
                if tool not in entry["tools"]:
                    entry["tools"].append(tool)
    json.dump({"root": root, "files": files, "count": len(funcs),
               "functions": funcs}, open(out, "w"), ensure_ascii=False)
    print(f"files={files} functions={len(funcs)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
