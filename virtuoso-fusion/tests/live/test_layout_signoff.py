"""Real OA/XStream acceptance in an isolated authenticated GUI session."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import struct

import pytest

PORT = os.environ.get("VF_LIVE_PORT")
pytestmark = pytest.mark.skipif(not PORT, reason="requires isolated authenticated RAMIC runner")


@pytest.fixture(scope="module")
def work():
    return Path(os.environ["VF_LIVE_ROOT"])


@pytest.fixture(scope="module")
def client(work):
    from virtuoso_fusion import FusionClient
    with FusionClient(port=int(PORT), timeout=30, log_to_ciw=False, evidence_dir=work / "calls",
                      daemon_token=Path(os.environ["VF_LIVE_TOKEN_FILE"]).read_text().strip()) as client:
        assert client.handshake_virtuoso_pid() == int(os.environ["VF_LIVE_PID"])
        yield client


@pytest.fixture(scope="module")
def library(client, work):
    name = "vf_physical_" + work.name.replace("-", "_")
    info = client.library.create(name, str(work / name), technology_library="tsmcN65")
    (work / "library.txt").write_text(str(info))
    return name


def test_geometry_group_transform_saved_reopened(client, library, work):
    from virtuoso_bridge.virtuoso.layout import (
        layout_create_rect, layout_create_fig_group, layout_add_to_group,
        layout_transform_fig_group, layout_read_geometry, parse_layout_geometry_output,
    )
    with client.layout.create(library, "geometry") as layout:
        layout.add("vfRect = " + layout_create_rect("M1", "drawing", 0, 0, 2, 1))
        layout.add(layout_create_fig_group("probe"))
        layout.add(layout_add_to_group("probe", "vfRect"))
    with client.layout.modify(library, "geometry") as layout:
        layout.add(layout_transform_fig_group("probe", offset=(5, 6), orientation="R90"))
    result = client.execute_skill(layout_read_geometry(library, "geometry", include_groups=True))
    assert result.ok, result.errors
    rows = parse_layout_geometry_output(result.output)
    (work / "geometry.json").write_text(json.dumps(rows, indent=2))
    shapes = [row for row in rows if row["kind"] == "shape"]
    assert len(shapes) == 1
    assert shapes[0]["layer"] == "M1"
    assert shapes[0]["bbox"] == [(4.0, 6.0), (5.0, 8.0)]


def test_n65_pcell_saved_reopened(client, library, work):
    from virtuoso_bridge.virtuoso.layout import layout_create_param_inst, layout_read_geometry, parse_layout_geometry_output
    with client.layout.create(library, "nch_probe") as layout:
        layout.add(layout_create_param_inst("tsmcN65", "nch", "layout", "M0", 0, 0, "R0"))
    with client.layout.modify(library, "nch_probe") as layout:
        layout.add('dbReplaceProp(car(cv~>instances) "l" "string" "60n")')
        layout.add('dbReplaceProp(car(cv~>instances) "w" "string" "600n")')
        layout.add('dbReplaceProp(car(cv~>instances) "wf" "string" "600n")')
    result = client.execute_skill(layout_read_geometry(library, "nch_probe"))
    assert result.ok, result.errors
    rows = parse_layout_geometry_output(result.output)
    (work / "pcell-geometry.json").write_text(json.dumps(rows, indent=2))
    assert len(rows) == 1 and rows[0]["cell"] == "nch" and rows[0]["name"] == "M0"
    result = client.execute_skill(
        f'let((cv inst params) cv=dbOpenCellViewByType("{library}" "nch_probe" "layout" "" "r") '
        'inst=car(cv~>instances) params=list(inst~>master~>superMaster~>cellName '
        'length(inst~>master~>shapes) mapcar(lambda((p) list(p~>name p~>value)) inst~>master~>parameters~>value)) dbClose(cv) params)')
    (work / "pcell-parameters.txt").write_text(result.output + "\n" + str(result.errors))
    assert result.ok and '"nch"' in result.output, result
    assert '("l" "60n")' in result.output and '("wf" "600n")' in result.output
    (work / "nch_probe.cdl").write_text(
        "* Independent intended 600nm/60nm floating one-device fixture\n"
        ".SUBCKT nch_probe\nM0 D G S B nch W=600n L=60n\n.ENDS nch_probe\n")


@pytest.mark.parametrize("cell", ["geometry", "nch_probe"])
def test_export_real_gds(client, library, work, cell):
    result = client.layout.export_gds(library, cell, work / (cell + ".gds"),
        stream_map="/eda/1P9M/TSMC065_1P9M/tsmcN65/tsmcN65.layermap",
        log_path=work / (cell + ".xstream.log"), timeout=120, finalization_reserve=20,
        cleanup_policy="never")
    (work / (cell + "-export.json")).write_text(json.dumps(asdict(result), default=str, indent=2))
    assert result.ok, result
    raw = (work / (cell + ".gds")).read_bytes()
    offset = 0
    structures, references, layers = set(), set(), set()
    last_type = None
    while offset < len(raw):
        size, record_type, data_type = struct.unpack(">HBB", raw[offset:offset + 4])
        assert size >= 4 and size % 2 == 0 and offset + size <= len(raw)
        payload = raw[offset + 4:offset + size]
        if record_type == 6:
            structures.add(payload.rstrip(b"\0").decode("ascii"))
        elif record_type == 18:
            references.add(payload.rstrip(b"\0").decode("ascii"))
        elif record_type == 13:
            layers.add(struct.unpack(">h", payload)[0])
        last_type = record_type
        offset += size
        if record_type == 4:
            assert not any(raw[offset:]), "nonzero bytes after ENDLIB"
            break
    assert last_type == 4 and cell in structures - references and layers
    if cell == "geometry":
        assert 31 in layers
    if cell == "nch_probe":
        assert {6, 17, 30, 31} <= layers
    (work / (cell + "-gds-structure.json")).write_text(json.dumps({
        "bytes": len(raw), "structures": sorted(structures), "topcells": sorted(structures - references),
        "layers": sorted(layers)}, indent=2))
