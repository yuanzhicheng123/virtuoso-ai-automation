"""doc_mine —— 从 Cadence 的 HTML 参考手册里挖函数索引（在 EDA 机器上跑）。

用法: python3 doc_mine.py <doc_dir> <out.json>
每个函数页形如 `group_re_name.html`，正文里有 `name( ... ) => ret` 和参数说明。
"""
import json
import os
import re
import sys
from html import unescape

TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
JS_NOISE = ("function", "document.", "img.", "querySelector", "addEventListener",
            "if (", "var ", "return;", "{", "}", "#", "opacity", "cursor")


def plain(html_text):
    t = SCRIPT.sub("\n", html_text)
    t = TAG.sub("\n", t)
    t = unescape(t)
    lines = [ln.strip() for ln in t.splitlines()]
    out = []
    for ln in lines:
        if not ln:
            continue
        if any(tok in ln for tok in JS_NOISE):
            continue
        out.append(ln)
    return out


def guess_name(fname):
    base = fname[:-5]
    for sep in ("_re_", "_rf_", "_ct_", "_cs_", "_ov_"):
        if sep in base:
            return base.split(sep)[-1]
    return base.split("_")[-1]


def parse_file(path, fname):
    lines = plain(open(path, encoding="utf-8", errors="replace").read())
    name = guess_name(fname)
    # 导航区之后才是正文：以 "View/Print PDF" 为锚点
    start = 0
    for i, ln in enumerate(lines[:80]):
        if "View/Print PDF" in ln:
            start = i + 1
            break
    # 找 `name(` 那一行
    for i in range(start, len(lines)):
        ln = lines[i]
        if ln == name + "(" or ln.startswith(name + "(") or ln == name:
            j = i
            # 收集到 `)` 为止
            buf = []
            while j < len(lines) and j < i + 40:
                buf.append(lines[j])
                if lines[j].startswith(")"):
                    break
                j += 1
            syntax = " ".join(buf).replace("( ", "(").replace(" )", ")")
            ret = ""
            if j + 1 < len(lines) and lines[j + 1].startswith("=>"):
                ret = lines[j + 1][2:].strip()
            # 参数：Syntax 括号里的标识符
            m = re.search(r"\((.*?)\)", syntax, re.S)
            args = []
            if m:
                args = [a for a in re.split(r"[\s,]+", m.group(1))
                        if a and a != "..." and re.match(r"^[\w?!<>\[\]]+$", a)]
            if not syntax.startswith(name) or "Product Documentation" in syntax:
                continue
            desc = ""
            for k in range(j + 1, min(j + 12, len(lines))):
                if lines[k].lower().startswith("description"):
                    if k + 1 < len(lines):
                        desc = lines[k + 1]
                    break
            return {"name": name, "syntax": syntax, "args": args,
                    "returns": ret, "desc": desc, "file": fname}
    return None


def parse_multi(path):
    """单页多函数的文档：形如 `name` 换行 `name( args )`。"""
    lines = plain(open(path, encoding="utf-8", errors="replace").read())
    out = {}
    i = 0
    while i + 1 < len(lines):
        name = lines[i]
        nxt = lines[i + 1]
        if (re.match(r"^[a-z][A-Za-z0-9_?!]{2,40}$", name)
                and nxt.startswith(name + "(")):
            buf = [nxt]
            j = i + 1
            while j < len(lines) and j < i + 30 and not lines[j].startswith(")"):
                j += 1
                if j < len(lines):
                    buf.append(lines[j])
            syntax = " ".join(buf).replace("( ", "(").replace(" )", ")")
            m = re.search(r"\((.*?)\)", syntax, re.S)
            args = [a for a in re.split(r"[\s,]+", m.group(1)) if a] if m else []
            ret = ""
            if j + 1 < len(lines) and lines[j + 1].startswith("=>"):
                ret = lines[j + 1][2:].strip()
            out.setdefault(name, {"name": name, "syntax": syntax[:300],
                                  "args": args[:12], "returns": ret, "desc": "",
                                  "file": os.path.basename(path)})
            i = j
        i += 1
    return out


def main():
    root, out = sys.argv[1], sys.argv[2]
    found = {}
    files = 0
    for fname in sorted(os.listdir(root)):
        if not fname.endswith(".html"):
            continue
        files += 1
        try:
            item = parse_file(os.path.join(root, fname), fname)
        except Exception:
            item = None
        if item and item["name"] not in found:
            found[item["name"]] = item
    for fname in sorted(os.listdir(root)):
        if not fname.endswith(".html"):
            continue
        try:
            for k, v in parse_multi(os.path.join(root, fname)).items():
                found.setdefault(k, v)
        except Exception:
            pass
    json.dump({"root": root, "files": files, "count": len(found),
               "functions": found}, open(out, "w"), indent=1)
    print("%s: files=%d functions=%d" % (root, files, len(found)))


if __name__ == "__main__":
    main()
