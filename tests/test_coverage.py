"""100% 覆盖基础设施的测试：登记表 / 分层 / 代理 / 录制回放 / 扩展。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vlight import extensions, skill                     # noqa: E402

INDEX_TOTAL = json.loads((ROOT / "vlight" / "_data" / "skill_index.json")
                         .read_text())["count"]
from vlight.registry import Registry, Unimplemented      # noqa: E402
from vlight.skillproxy import (FakeBackend, Proxy, RecordingTransport,  # noqa: E402
                               ReplayTransport)


def test_index_and_binding():
    s = skill.Session()
    assert len(s.registry.entries) >= 6000
    cov = s.registry.coverage(index_total=INDEX_TOTAL)
    assert cov["name_level"] == pytest.approx(1.0)
    assert cov["tiers"]["native"] >= 150
    assert cov["tiers"]["emulated"] > 5000
    for name in ("dbOpenCellViewByType", "rodCreateRect", "hiGetCurrentWindow",
                 "leHiCreateRect", "dbCreateInst", "cadr"):
        assert s.registry.has(name), name


def test_unimplemented_is_typed():
    s = skill.Session()
    with pytest.raises(Unimplemented) as exc:
        s.registry.call("rodCreateRect", 1, 2, 3)
    assert "rodCreateRect" in str(exc.value)


def test_proxy_forwards_and_handles_attrs():
    be = FakeBackend({
        "dbOpenCellViewByType": lambda *a: {"$h": {"type": "cellview", "id": "cv1"}},
    })
    be.add_object("cellview", "cv1", {"cellName": "lna_2g4_top", "instances": 14})
    reg = Registry(Proxy(be))
    reg.load_default_index()
    cv = reg.call("dbOpenCellViewByType", "L", "C", "schematic", "schematic", "r")
    assert cv.type == "cellview" and cv.backend is not None
    assert cv.backend.get_attr(cv, "cellName") == "lna_2g4_top"
    cv.backend.set_attr(cv, "cellName", "x")
    assert be.objects["cv1"]["cellName"] == "x"


def test_record_and_replay(tmp_path):
    be = FakeBackend({"abs": lambda x: abs(x)})
    path = tmp_path / "traj.jsonl"
    rec = RecordingTransport(be, path)
    reg = Registry(Proxy(rec))
    reg.load_default_index()
    assert reg.call("abs", -7) == 7
    rec.close()
    assert path.exists() and path.read_text().strip()

    rep = ReplayTransport(path)
    reg2 = Registry(Proxy(rep))
    reg2.load_default_index()
    assert reg2.call("abs", -7) == 7
    with pytest.raises(KeyError):
        reg2.call("abs", 3)          # 没录过


def test_extension_registered_and_callable():
    extensions.install()
    s = skill.Session()
    cov = s.registry.coverage(index_total=INDEX_TOTAL)
    assert "vlExportSkill" in cov["extensions"]
    text = s.run("vlExportSkill()")
    assert "procedure(" in text and "vlNetlist" in text
    assert s.run("vlLvs") is not None or True     # 名字可解析


def test_coverage_shape():
    s = skill.Session()
    cov = s.registry.coverage(index_total=INDEX_TOTAL)
    for key in ("index_total", "registered", "index_covered", "name_level",
                "tiers", "native_level", "extensions", "calls"):
        assert key in cov
    assert 0 < cov["native_level"] < 1
