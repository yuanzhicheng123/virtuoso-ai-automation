"""Installed-wheel acceptance; launch only with tools/run_live.py."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys

import pytest

PORT = os.environ.get("VF_LIVE_PORT")
pytestmark = pytest.mark.skipif(not PORT, reason="requires isolated authenticated RAMIC runner")


@pytest.fixture(scope="module")
def work():
    return Path(os.environ["VF_LIVE_ROOT"])


@pytest.fixture(scope="module")
def client(work):
    from virtuoso_fusion import FusionClient

    with FusionClient(port=int(PORT), timeout=10, log_to_ciw=False, evidence_dir=work / "core-calls",
                      daemon_token=Path(os.environ["VF_LIVE_TOKEN_FILE"]).read_text().strip()) as client:
        assert client.handshake_virtuoso_pid() == int(os.environ["VF_LIVE_PID"])
        yield client


def test_installed_namespaces_and_identity(client, work):
    import virtuoso_fusion
    import virtuoso_bridge
    from virtuoso_fusion import ApiRegistry

    for module in (virtuoso_fusion, virtuoso_bridge):
        assert Path(module.__file__).is_relative_to(sys.prefix), module.__file__
        assert "site-packages" in Path(module.__file__).parts
    coverage = ApiRegistry().coverage()
    assert coverage["total"] == 74785
    assert coverage["with_signature"] == 61979
    result = client.call("getWorkingDir")
    assert result.ok and json.loads(result.output) == str(work / "runtime")
    (work / "installed.json").write_text(json.dumps({
        "fusion": virtuoso_fusion.__file__, "upstream": virtuoso_bridge.__file__,
        "python": sys.executable, "signed_pid": client.handshake_virtuoso_pid(),
    }, indent=2))


def test_variadic_keywords_symbols_and_repeated_calls(client):
    from virtuoso_fusion import Symbol

    for index in range(30):
        result = client.call("plus", index, 1, 2, 3)
        assert result.ok and result.output.strip() == str(index + 6)
    result = client.call("nth", index0=1, list=[10, 20, 30])
    assert result.ok and result.output.strip() == "20"
    result = client.call("getVersion", opt=True)
    assert result.ok and "IC23" in result.output
    result = client.call("symbolToString", Symbol("vfProbeSymbol"))
    assert result.ok and json.loads(result.output) == "vfProbeSymbol"


def test_same_client_concurrent_calls(client):
    with ThreadPoolExecutor(max_workers=4) as executor:
        replies = list(executor.map(lambda value: client.call("plus", value, 100), range(24)))
    assert all(reply.ok for reply in replies)
    assert [int(reply.output.strip()) for reply in replies] == list(range(100, 124))


def test_error_propagation_and_next_request(client):
    failed = client.call("quotient", 1, 0)
    assert not failed.ok and failed.errors
    result = client.call("plus", 20, 22)
    assert result.ok and result.output.strip() == "42"


def test_upstream_library_schematic_and_generic_share_session(client, work):
    from virtuoso_bridge.virtuoso.schematic import schematic_create_inst_by_master_name

    lib = "vf_" + work.name.replace("-", "_")
    lib_path = work / lib
    assert not lib_path.exists()
    info = client.library.create(lib, str(lib_path))
    assert info.name == lib and Path(info.path) == lib_path
    assert lib in client.library.list()
    with client.schematic.create(lib, "probe") as schematic:
        schematic.add(schematic_create_inst_by_master_name("analogLib", "res", "symbol", "R0", 0, 0, "R0"))
    readback = client.schematic.read(lib, "probe", include_param_evidence=True)
    assert [item["name"] for item in readback["instances"]] == ["R0"]
    result = client.call("ddGetObj", lib, "probe", "schematic")
    assert result.ok and not result.is_nil
    raw = client.execute_skill(
        f'let((cv names) cv=dbOpenCellViewByType("{lib}" "probe" "schematic" "" "r") '
        'names=cv~>instances~>name dbClose(cv) names)', retry_connect=False)
    assert raw.ok and '"R0"' in raw.output
    (work / "oa-readback.json").write_text(json.dumps(readback, indent=2))
    events = [json.loads(line) for line in next((work / "core-calls").glob("*.jsonl")).read_text().splitlines()]
    assert len({event["client_id"] for event in events}) == 1
    assert any("ddCreateLib" in event["skill"] for event in events)
    assert any("plus(" in event["skill"] for event in events)


def test_real_spectre_op_dc_transient(work):
    from virtuoso_fusion import SpectreSimulator
    from virtuoso_bridge.spectre.psf import read_psf_ascii, result_file, scalar, vector

    model = Path(os.environ["VF_LIVE_MODEL"])
    assert model.is_file()
    template = Path(__file__).parents[1] / "fixtures/nch_smoke.scs"
    netlist = work / "nch_smoke.scs"
    netlist.write_text(template.read_text().replace("@MODEL@", str(model)))
    sim = SpectreSimulator.local(timeout=60, work_dir=work / "spectre", license_queue_timeout=0)
    result = sim.run_simulation(netlist)
    assert result.ok, result.errors
    raw = Path(result.metadata["output_dir"])
    op = read_psf_ascii(result_file(raw, "op1.dc"))
    sweep = read_psf_ascii(result_file(raw, "sw1.dc"))
    tran = read_psf_ascii(result_file(raw, "tr1.tran.tran"))
    assert scalar(op, "g") == pytest.approx(0.6, abs=1e-6)
    assert len(vector(sweep, "g")) == 13
    times = [item.real for item in vector(tran, "time")]
    gate = [item.real for item in vector(tran, "g")]
    drain = [item.real for item in vector(tran, "VD:p")]
    assert len(times) > 100 and all(b > a for a, b in zip(times, times[1:]))
    assert min(gate) == pytest.approx(0.6, abs=1e-6)
    assert max(gate) == pytest.approx(0.9, abs=1e-6)
    assert max(drain) - min(drain) > 1e-6
    (work / "spectre-assertions.json").write_text(json.dumps({
        "gate_min": min(gate), "gate_max": max(gate), "tran_samples": len(times),
        "status": "PASS", "metadata": result.metadata,
        "scope": "standalone transistor testbench; not OA-to-netlist or PLL signoff",
    }, indent=2))
