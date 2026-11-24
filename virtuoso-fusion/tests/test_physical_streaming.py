"""Real-file PEX inspection keeps include semantics while streaming input."""
from pathlib import Path

import pytest

from virtuoso_fusion.physical import _inspect_netlist


def test_inspection_streams_files_and_counts_each_include_once(tmp_path, monkeypatch):
    netlist = tmp_path / "top.sp"
    parts = tmp_path / "parts"
    parts.mkdir()
    child = parts / "rc network.pxi"
    netlist.write_bytes(b'.include "parts/rc network.pxi"\r\n.INC \'parts/rc network.pxi\'\rRtop a b 2\n* Rignored a b 9\nM1 d g s b nch\n')
    child.write_bytes(b'.inc ../top.sp\n  c1 a b 1f\nR2 b c 3\n* invalid text: \xff\nC2 c d 2f')

    def reject_read_text(path, *args, **kwargs):
        raise AssertionError("PEX input must be iterated, not read with Path.read_text")

    monkeypatch.setattr(Path, "read_text", reject_read_text)
    assert _inspect_netlist(netlist) == {
        "complete": True,
        "files": sorted([str(netlist), str(child)]),
        "missing_files": [],
        "external_files": [],
        "resistors": 2,
        "capacitors": 2,
    }


def test_inspection_tracks_missing_empty_and_external_includes(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    netlist = run / "top.sp"
    empty = run / "empty.pxi"
    empty.touch()
    missing = run / "missing.pxi"
    outside = tmp_path / "old.pxi"
    outside.write_text("Rold a b 99\nCol d e 99f\n")
    netlist.write_text('.include missing.pxi\n.inc empty.pxi\n.inc ../old.pxi\nC1 a b 1f\n')

    result = _inspect_netlist(netlist)
    assert result == {
        "complete": False,
        "files": sorted(map(str, [netlist, missing, empty, outside])),
        "missing_files": [str(empty), str(missing)],
        "external_files": [str(outside)],
        "resistors": 0,
        "capacitors": 1,
    }


@pytest.mark.parametrize("body", ["", "* no parasitics\nM1 d g s b nch\n"])
def test_inspection_requires_nonempty_parasitic_network(tmp_path, body):
    netlist = tmp_path / "top.sp"
    netlist.write_text(body)
    result = _inspect_netlist(netlist)
    assert result["complete"] is False
    assert result["resistors"] == result["capacitors"] == 0
    assert result["missing_files"] == ([] if body else [str(netlist)])
