#!/usr/bin/env python3
"""受控 SKILL 调用 CLI：策略 → GenericSkill → 结果。

用法：
  python3 tools/skill_cli.py --fn plus --args 1 2
  python3 tools/skill_cli.py --fn nth --args 1 '[10,20]'
  python3 tools/skill_cli.py --why dbCreateNet
"""
import argparse, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import virtuoso_bridge as vb
from virtuoso_bridge.gateway_adapter import GatewaySession, Denied


def parse(v):
    try:
        return json.loads(v)
    except Exception:
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fn")
    ap.add_argument("--args", nargs="*", default=[])
    ap.add_argument("--kw", nargs="*", default=[], help="k=v 形式")
    ap.add_argument("--session", default=os.environ.get("VB_WORKSPACE", "default"))
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--why", help="解释某函数为何可用/不可用")
    ap.add_argument("--log", default=os.path.join(ROOT, "audit", "skill_calls.log"))
    a = ap.parse_args()

    reg = vb.ApiRegistry()
    if a.why:
        from virtuoso_bridge.availability import availability
        from virtuoso_bridge.policy import Policy
        p = Policy()
        print(json.dumps({"availability": availability(reg, a.why),
                          "policy": p.check(a.why)}, ensure_ascii=False, indent=1))
        return 0
    if not a.fn:
        ap.error("需要 --fn 或 --why")

    gs = GatewaySession(skill=vb.Skill(workspace_id=a.session), registry=reg,
                        allow_write=a.allow_write, log_path=a.log)
    args = [parse(x) for x in a.args]
    kw = dict(x.split("=", 1) for x in a.kw)
    try:
        v = gs.call(a.fn, *args, **kw)
    except Denied as e:
        print(json.dumps({"fn": a.fn, "ok": False, "denied": str(e)}))
        return 3
    except Exception as e:
        print(json.dumps({"fn": a.fn, "ok": False,
                          "error": "%s: %s" % (type(e).__name__, e)}))
        return 2
    print(json.dumps({"fn": a.fn, "ok": True, "value": str(v)[:200]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
