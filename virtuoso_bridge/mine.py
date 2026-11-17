"""签名挖掘：生成 SKILL 脚本 + 解析输出（与 /tmp/run_chunks.sh 同款做法）。"""
import re


def parse_arglists(text):
    out = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        name, raw = line.split("|", 1)
        name = name.strip().strip("'")
        if name:
            out[name] = raw.strip()
    return out


def mine_arglists(names, chunk=1000, out_prefix="/tmp/vb_mine", results_prefix="/tmp/vb_mine"):
    """生成 N 个 .il 脚本（每 chunk 个符号一个文件），返回脚本路径列表。

    用法：在 virtuoso -nograph 会话里依次 load 这些 .il；
    每个脚本会把 `name|arglist` 写进 <results_prefix>/out_<i>.txt。
    """
    scripts = []
    names = list(names)
    for i in range(0, len(names), chunk):
        part = names[i:i + chunk]
        path = "%s_%03d.il" % (out_prefix, i // chunk)
        src = ('(let (p a ok)\n'
               '  (setq p (outfile "%s/out_%03d.txt" "w"))\n'
               '  (setq ok 0)\n'
               '  (foreach nm \'(%s)\n'
               '    (setq a (errset (arglist nm)))\n'
               '    (when (and a (car a)) (setq ok (plus ok 1))\n'
               '      (fprintf p "%%L|%%L\\n" nm (car a))))\n'
               '  (close p)\n'
               '  (printf "@@chunk=%d ok=%%L@@\\n" ok))\n')
        with open(path, "w") as f:
            f.write(src % (results_prefix, i // chunk, " ".join(part), i // chunk))
        scripts.append(path)
    return scripts
