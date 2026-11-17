"""lowlevel —— 挖 SKILL 之下的层：ELF 符号、二进制格式、C/C++ API 面。

在 EDA 机器上跑：
    python3 lowlevel.py /eda/cds/IC2310 out.json

产出：
    libs        共享库清单与导出符号数（抽样）
    oa_classes  liboa* 里能反解出来的 OpenAccess 类名
    magics      .cxt / .dm / .oa / psf 的文件头
    caps        头文件/Python 绑定/SKILL 运行时形态（静态还是动态）
"""
import json
import os
import re
import subprocess
import sys

MAX_LIBS = 240


def sh(cmd, timeout=60):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def symbol_count(path):
    out = sh('nm -D --defined-only "%s" 2>/dev/null | wc -l' % path)
    try:
        return int(out.strip() or 0)
    except ValueError:
        return 0


def oa_classes(path, limit=400):
    out = sh('nm -D --defined-only "%s" 2>/dev/null | awk \'{print $3}\' | c++filt'
             % path, timeout=120)
    names = re.findall(r"OpenAccess(?:_\d+)?::([A-Za-z_][A-Za-z0-9_]*)", out)
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
        if len(seen) >= limit:
            break
    return seen


def find_libs(root):
    libs = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("doc", "data", "images", "install")]
        for f in files:
            if ".so" in f:
                libs.append(os.path.join(base, f))
    return libs


def magic(path, n=32):
    try:
        with open(path, "rb") as fh:
            data = fh.read(n)
        return {"hex": data.hex(), "size": os.path.getsize(path)}
    except Exception:
        return {}


def main():
    root, out = sys.argv[1], sys.argv[2]
    libs = find_libs(root)
    def size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0

    by_size = sorted(libs, key=lambda p: -size(p))[:MAX_LIBS]
    ranked = []
    for p in by_size:
        n = symbol_count(p)
        if n:
            ranked.append({"path": p.replace(root, ""), "symbols": n,
                           "mb": round(size(p) / 1e6, 1)})
    ranked.sort(key=lambda x: -x["symbols"])

    oa = {}
    oa_libs = sorted([p for p in libs if os.path.basename(p).startswith("liboa")],
                     key=lambda p: -size(p))[:6]
    for p in oa_libs:
        base = os.path.basename(p)
        if True:
            cls = oa_classes(p)
            if cls:
                oa[base] = {"symbols": symbol_count(p), "classes": len(cls),
                            "sample": cls[:12]}
            if len(oa) >= 6:
                break

    probe = {
        "cxt": root + "/tools/dfII/etc/context/64bit/schView.cxt",
        "dm": "/root/cadence_projects/tsmcN65_LNA2G4/data.dm",
        "oalib": "/root/cadence_projects/tsmcN65_LNA2G4/.oalib",
        "techfile": "/eda/1P9M/TSMC065_1P9M/techfile",
    }
    magics = {k: magic(v) for k, v in probe.items() if os.path.exists(v)}

    caps = {
        "headers": sh("find %s -name 'oa*.h' 2>/dev/null | wc -l" % root).strip(),
        "oa_python": sh("ls %s/oa_v22.61.003/data/python 2>/dev/null | tr '\\n' ' '" % root).strip(),
        "skill_binary": "statically linked" if not sh(
            "ldd %s/tools/dfII/bin/64bit/skill 2>/dev/null | grep -c 'libskill\\|libil'" % root
        ).strip().isdigit() else "shared",
        "so_total": sh("find %s -name '*.so' 2>/dev/null | wc -l" % root).strip(),
    }
    data = {"root": root, "libs_scanned": len(libs), "top_libs": ranked[:25],
            "oa": oa, "magics": magics, "caps": caps}
    json.dump(data, open(out, "w"), indent=1)
    print("libs=%d top=%s" % (len(libs), ranked[0] if ranked else None))
    print("oa libs:", {k: v["classes"] for k, v in oa.items()})
    print("magics:", {k: v.get("hex", "")[:16] for k, v in magics.items()})


if __name__ == "__main__":
    main()
