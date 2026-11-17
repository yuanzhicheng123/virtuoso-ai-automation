"""skill_runtime —— 在活的 SKILL 会话里枚举**全部已定义函数**。

原理（已在 eda-220 上验证）：
    oblist  → 运行时符号表（116,243 个符号）
    getd    → 符号的函数定义（primop:/funobj:），变量和未定义返回 nil
所以：遍历 oblist，getd 非 nil 的就是函数。

用法（在 EDA 机器上，任意能跑 skill/virtuoso 的环境）：
    python3 mine/skill_runtime.py --binary virtuoso --out /tmp/fns.txt
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
import os

SCRIPT = r'''
(let (p n s)
  (setq p (outfile "__OUT__"))
  (setq n 0)
  (foreach s oblist
    (when (getd s)
      (fprintf p "%s\n" s)
      (setq n (add1 n))))
  (close p)
  (printf "@@count=%d@@\n" n))
'''


def dump(binary: str = "skill", out: str = "/tmp/skill_functions.txt",
         timeout: int = 300) -> str:
    tmp = tempfile.mkdtemp(prefix="skfn_")
    script = SCRIPT.replace("__OUT__", out)
    with open(os.path.join(tmp, "s.il"), "w") as fh:
        fh.write(script + "\n(exit)\n")
    subprocess.run(["sh", "-c", "unset DISPLAY; cd %s && timeout %d %s -nograph"
                    % (tmp, timeout, binary)], capture_output=True, text=True,
                   timeout=timeout + 60)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default="skill")
    ap.add_argument("--out", default="/tmp/skill_functions.txt")
    args = ap.parse_args()
    path = dump(args.binary, args.out)
    with open(path) as fh:
        names = [l.strip() for l in fh if l.strip()]
    pub = [n for n in names if not n.startswith("_")]
    print(f"{len(names)} functions ({len(pub)} public) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
