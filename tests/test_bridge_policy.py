"""virtuoso_bridge 策略/可用性/适配层单元测试（无真机会话）。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import virtuoso_bridge as vb  # noqa: E402
from virtuoso_bridge.gateway_adapter import GatewaySession, Denied  # noqa: E402
from virtuoso_bridge.availability import availability, headless_stats  # noqa: E402
from virtuoso_bridge.policy import Policy  # noqa: E402


class FakeSkill(object):
    def call(self, fn, *a):
        if fn == "plus":
            return sum(a)
        if fn == "nth":
            return a[1][a[0]]
        return (fn, a)


def test_policy_write_needs_allow():
    p = Policy()
    assert p.check("dbCreateNet")[0] is False
    assert p.check("dbCreateNet", allow_write=True)[0] is True
    assert p.check("plus")[0] is True


def test_gateway_session_denies_write():
    gs = GatewaySession(skill=FakeSkill(), registry=vb.ApiRegistry())
    assert gs.call("plus", 1, 2) == 3
    assert gs.call("nth", 1, [10, 20]) == 20
    try:
        gs.call("dbCreateNet", None, "X")
        raise AssertionError("write should be denied")
    except Denied:
        pass


def test_availability_states():
    reg = vb.ApiRegistry()
    assert availability(reg, "plus")["state"] in ("HEADLESS_OK",)
    assert availability(reg, "no_such_fn_xyz")["state"] == "NOT_IN_REGISTRY"


def test_needs_window_marking():
    reg = vb.ApiRegistry()
    st = headless_stats(reg)
    assert st["needs_window"] > 0
    assert st["headless_ok"] > 0
    assert st["total"] == len(reg.raw)


def test_auto_signature_caches(tmp_path):
    import json
    reg = tmp_path / "reg.json"
    reg.write_text("{}")

    class S(object):
        def call(self, fn, *a):
            if fn == "arglist":
                return "(n_x)"
            return "OK"

    gs = vb.GenericSkill(skill=S(), registry=vb.ApiRegistry(str(reg)),
                         skip_path=str(tmp_path / "skip.json"))
    assert gs.call("someFn", 1) == "OK"
    assert json.loads(reg.read_text())["someFn"]["arglist_raw"] == "(n_x)"


def test_skip_list_records_failures(tmp_path):
    import json
    reg = tmp_path / "reg.json"
    reg.write_text("{}")

    class S(object):
        def call(self, fn, *a):
            if fn == "arglist":
                return None
            return "OK"

    skip = tmp_path / "skip.json"
    gs = vb.GenericSkill(skill=S(), registry=vb.ApiRegistry(str(reg)), skip_path=str(skip))
    gs.call("badFn", 1)
    assert "badFn" in json.loads(skip.read_text())["skip"]
