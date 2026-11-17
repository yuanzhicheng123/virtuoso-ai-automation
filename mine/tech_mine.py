"""tech_mine —— 把 Virtuoso 的 techfile(.tf) 拆成结构化 JSON。

    purposes   : 名字 -> 编号（layer purpose 的编号）
    layers     : 层名 -> 用到的 purpose 列表
    vias       : viaDefs 里的标准 via（层对、cut 层、尺寸、行列、间距、enclosure）

用法: python3 tech_mine.py tsmcN65.techfile out.json
"""
from __future__ import annotations

import json
import re
import sys


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        out.append(line.split(";", 1)[0])
    return "\n".join(out)


def tokenize(text: str):
    """切词并标记「紧贴 ( 的原子」（name( 形式）。"""
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "()":
            toks.append(c)
            i += 1
        elif c == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j + 1
            toks.append(text[i:j])
            i = j
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()":
                j += 1
            glue = "\x01" if j < n and text[j] == "(" else ""
            toks.append(text[i:j] + glue)
            i = j
    return toks


def parse_sexpr(text: str):
    toks = tokenize(text)
    pos = 0

    def parse():
        nonlocal pos
        tok = toks[pos]
        pos += 1
        if tok == "(":
            out = []
            while pos < len(toks) and toks[pos] != ")":
                out.append(parse())
            pos += 1
            return out
        glued = tok.endswith("\x01")
        atom = tok.rstrip("\x01").strip('"')
        # name( ... ) -> [name, ...]（只有紧贴才算）
        if glued and pos < len(toks) and toks[pos] == "(":
            pos += 1
            out = []
            while pos < len(toks) and toks[pos] != ")":
                out.append(parse())
            pos += 1
            return [atom] + out
        return atom

    forms = []
    while pos < len(toks):
        forms.append(parse())
    return forms


def find_all(node, key):
    """深度优先找所有以 key 开头的子表。"""
    out = []
    if isinstance(node, list) and node:
        if isinstance(node[0], str) and node[0] == key:
            out.append(node)
        for child in node:
            out.extend(find_all(child, key))
    return out


def fnum(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def main():
    src, out = sys.argv[1], sys.argv[2]
    forms = parse_sexpr(strip_comments(open(src, errors="replace").read()))
    purposes, layers, vias = {}, {}, []

    for block in find_all(forms, "techPurposes"):
        for item in block[1:]:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
                if item[0] in ("PurposeName", "---------"):
                    continue
                purposes[item[0]] = fnum(item[1])
    def collect_pairs(node):
        out = []
        if isinstance(node, list):
            if (len(node) == 2 and all(isinstance(v, str) for v in node)
                    and node[0] not in ("techPurposes", "techLayerProperties",
                                        "PurposeName", "---------")):
                out.append((node[0], node[1]))
            for child in node:
                out.extend(collect_pairs(child))
        return out

    for block in find_all(forms, "layerDefinitions"):
        for name, purpose in collect_pairs(block):
            layers.setdefault(name, [])
            if purpose not in layers[name]:
                layers[name].append(purpose)
    for block in find_all(forms, "standardViaDefs"):
        for item in block[1:]:
            if not (isinstance(item, list) and len(item) >= 6):
                continue
            cut = item[3] if isinstance(item[3], list) else []
            rows = item[4] if isinstance(item[4], list) else []
            enc1 = item[5] if isinstance(item[5], list) else []
            enc2 = item[6] if len(item) > 6 and isinstance(item[6], list) else []
            vias.append({
                "name": item[0], "layer1": item[1], "layer2": item[2],
                "cut_layer": cut[0] if cut else "",
                "cut_w": fnum(cut[1]) if len(cut) > 1 else 0.0,
                "cut_h": fnum(cut[2]) if len(cut) > 2 else 0.0,
                "rows": int(fnum(rows[0], 1)) if rows else 1,
                "cols": int(fnum(rows[1], 1)) if len(rows) > 1 else 1,
                "space": [fnum(v) for v in (rows[2] if len(rows) > 2
                                            and isinstance(rows[2], list) else [])],
                "enc1": [fnum(v) for v in enc1],
                "enc2": [fnum(v) for v in enc2],
            })
    data = {"source": src, "purposes": purposes,
            "layers": {k: v for k, v in layers.items() if v},
            "vias": vias,
            "counts": {"purposes": len(purposes), "layers": len(layers),
                       "vias": len(vias)}}
    json.dump(data, open(out, "w"), indent=1)
    print("purposes=%d layers=%d vias=%d -> %s"
          % (len(purposes), len(layers), len(vias), out))


if __name__ == "__main__":
    main()
