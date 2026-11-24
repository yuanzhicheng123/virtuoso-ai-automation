"""Real isolated OA/native-netlist and Maestro acceptance, never chip signoff."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3

import pytest

PORT = os.environ.get("VF_LIVE_PORT")
pytestmark = pytest.mark.skipif(not PORT, reason="requires isolated authenticated GUI runner")


@pytest.fixture(scope="module")
def work():
    root = Path(os.environ["VF_LIVE_ROOT"])
    (root / "test-source.py").write_bytes(Path(__file__).read_bytes())
    return root


@pytest.fixture(scope="module")
def client(work):
    from virtuoso_fusion import FusionClient
    with FusionClient(port=int(PORT), timeout=30, log_to_ciw=False, evidence_dir=work / "calls",
                      daemon_token=Path(os.environ["VF_LIVE_TOKEN_FILE"]).read_text().strip()) as client:
        assert client.handshake_virtuoso_pid() == int(os.environ["VF_LIVE_PID"])
        result = client.execute_skill(
            'progn(foreach(k list("VSEL_UseNextLicense" "VLSL_UseNextLicense" "VLSXL_UseNextLicense" '
            '"ADEL_UseNextLicense" "ADEXL_UseNextLicense" "ADE_UseNextLicense" '
            '"Explorer_UseNextLicense") envSetVal("license" k \'string "always")) '
            f'envSetVal("asimenv.startup" "projectDir" \'string "{work}/simulation") t)',
            retry_connect=False)
        assert result.ok, result.errors
        yield client


@pytest.fixture(scope="module")
def rc(client, work):
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_create_pin as pin,
        schematic_label_instance_term as label,
    )
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params
    lib = "vf_maestro_" + work.name.replace("-", "_")
    client.library.create(lib, str(work / lib))
    cell = "rc_native"
    with client.schematic.create(lib, cell) as sch:
        for master, name, x, y in (("vdc", "V0", 0, 0), ("res", "R0", 1.5, 0.5),
                                   ("cap", "C0", 3, 0), ("gnd", "G0", 0, -1)):
            sch.add(inst("analogLib", master, "symbol", name, x, y, "R0"))
        for name, term, net in (("V0", "PLUS", "VIN"), ("V0", "MINUS", "gnd!"),
                                ("R0", "PLUS", "VIN"), ("R0", "MINUS", "OUT"),
                                ("C0", "PLUS", "OUT"), ("C0", "MINUS", "gnd!")):
            sch.add(label(name, term, net))
        sch.add(pin("OUT", 4.5, 0.5, "R0", direction="output"))
    assert client.open_window(lib, cell).ok
    evidence = {
        "V0": set_instance_params(client, "V0", vdc="1", acm="1", param_filters=None),
        "R0": set_instance_params(client, "R0", r="1k", param_filters=None),
        "C0": set_instance_params(client, "C0", c="1p", param_filters=None),
    }
    (work / "cdf-writes.json").write_text(json.dumps(evidence, indent=2))
    report = client.schematic.check_and_save(lib, cell)
    assert report.status == "saved" and report.check_error_count == 0, report
    readback = client.schematic.read(lib, cell, include_param_evidence=True)
    (work / "oa-readback.json").write_text(json.dumps(readback, indent=2, default=str))
    return lib, cell


def test_native_oa_netlist_spectre(client, rc, work):
    from virtuoso_fusion import SpectreSimulator
    from virtuoso_bridge.spectre.psf import read_psf_ascii, result_file, scalar, vector
    lib, cell = rc
    exported = client.schematic.export_netlist(lib, cell, work / "native-netlist", run_dir=work / "native-export-run", timeout=120)
    (work / "export.json").write_text(json.dumps(exported, indent=2, default=str))
    native = work / "native-netlist/input.scs"
    content = native.read_text()
    assert re.search(r"^R0 \(VIN OUT\) resistor r=1[kK]", content, re.M), content
    assert re.search(r"^C0 \(OUT 0\) capacitor c=1p", content, re.M), content
    assert re.search(r"^V0 \(VIN 0\) vsource .*dc=1", content, re.M), content
    deck = work / "native-ac.scs"
    deck.write_text(content + '\nop_native dc\nac_native ac start=1 stop=10G dec=100\nsave VIN OUT\n')
    missing = client.maestro.check_netlist_options(str(deck), 'ac', ['start', 'stop', 'dec'])
    assert missing == []
    assert client.maestro.check_netlist_options(str(deck), 'ac', ['absent_probe_option']) == ['absent_probe_option']
    (work / 'netlist-option-checks.json').write_text(json.dumps({'present':['start','stop','dec'],'missing_probe':['absent_probe_option']}))
    result = SpectreSimulator.local(timeout=120, work_dir=work / "native-spectre", license_queue_timeout=0).run_simulation(deck)
    assert result.ok, result.errors
    raw = Path(result.metadata["output_dir"])
    op = read_psf_ascii(result_file(raw, "op_native.dc"))
    ac = read_psf_ascii(result_file(raw, "ac_native.ac"))
    assert scalar(op, "OUT") == pytest.approx(1, abs=1e-6)
    frequency = vector(ac, "freq")
    gain = vector(ac, "OUT")
    fc = 1 / (2 * math.pi * 1e3 * 1e-12)
    nearest = min(range(len(frequency)), key=lambda index: abs(frequency[index].real - fc))
    assert abs(gain[nearest]) == pytest.approx(1 / math.sqrt(2), abs=0.01)
    (work / "native-assertions.json").write_text(json.dumps({
        "status": "PASS", "scope": "OA analogLib RC native export and Spectre DC/AC",
        "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
        "deck_sha256": hashlib.sha256(deck.read_bytes()).hexdigest(),
        "cutoff_hz": fc, "nearest_hz": frequency[nearest].real,
        "gain_at_nearest": abs(gain[nearest]), "metadata": result.metadata,
    }, indent=2, default=str))


def test_maestro_config_sweep_results(client, rc, work):
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params
    from virtuoso_bridge.virtuoso.maestro import MonteCarloConfig
    lib, cell = rc
    assert client.open_window(lib, cell).ok
    set_instance_params(client, "C0", c="c_val", param_filters=None)
    report = client.schematic.check_and_save(lib, cell)
    assert report.status == "saved" and report.check_error_count == 0
    session = client.maestro.open_session(lib, cell)
    client.maestro.create_test("AC", lib=lib, cell=cell, session=session)
    client.maestro.set_analysis("AC", "tran", enable=False, session=session)
    client.maestro.set_analysis("AC", "ac", session=session,
        options='(("start" "1") ("stop" "10G") ("incrType" "Logarithmic") '
                '("stepTypeLog" "Points Per Decade") ("dec" "40"))')
    client.maestro.add_output("BW", "AC", output_type="point",
        expr='bandwidth(mag(VF("/OUT")) 3 "low")', session=session)
    client.maestro.set_spec("BW", "AC", gt="1G", session=session)
    client.maestro.set_var("c_val", "1p,100f", session=session)
    client.maestro.set_corner("nominal_27", session=session)
    client.maestro.save_setup(lib, cell, session=session)
    client.maestro.close_session(session)
    gui = client.maestro.open_gui_session(lib, cell)
    assert "1p,100f" in client.maestro.get_var("c_val", session=gui)
    snapshot = client.maestro.snapshot(output_root=str(work / "setup-snapshot"))
    (work / "maestro-setup.json").write_text(json.dumps(snapshot, indent=2, default=str))
    before = client.maestro.list_histories(gui)
    (work / "histories-before.json").write_text(json.dumps([h.model_dump() for h in before], indent=2, default=str))
    client.maestro.set_job_control_mode("Local", session=gui)
    history, status = client.maestro.run_and_wait(session=gui, timeout=300)
    history = history.strip('"')
    assert history not in {h.name for h in before}, (history, before)
    points = client.maestro.check_points(history, lib=lib, cell=cell)
    numbered_points = [point for point in points if point['point'].isdigit()]
    assert {point['point'] for point in numbered_points} == {'1', '2', '3', '4'}
    assert len(numbered_points) == 4 and all(point['status'] == 'ok' for point in points), points
    (work / 'upstream-point-checks.json').write_text(json.dumps(points, indent=2))
    results = client.maestro.read_results(gui, lib=lib, cell=cell, history=history, include_raw=True)
    (work / "maestro-results.json").write_text(json.dumps({"history": history, "status": status, "results": results}, indent=2, default=str))
    by_c = {p["parameters"]["c_val"]: p["outputs"]["BW"] for p in results["points"]}
    assert set(by_c) == {"1p", "100f"}, results
    for c_val, farad in (("1p", 1e-12), ("100f", 100e-15)):
        assert float(by_c[c_val]["value"]) == pytest.approx(1 / (2 * math.pi * 1e3 * farad), rel=0.02)
    assert by_c["1p"]["pass_fail"] == "fail" and by_c["100f"]["pass_fail"] == "pass"
    assert results["point_semantics"] == "sweep_group"
    assert results["corner_columns"] == ["Nominal", "nominal_27"]
    rows = [row for row in results["detail_rows"] if row["name"] == "BW"]
    assert len(rows) == 2
    for row in rows:
        expected = 1 / (2 * math.pi * 1e3 * {"1p": 1e-12, "100f": 100e-15}[row["parameters"]["c_val"]])
        assert row["test"] == "AC"
        assert set(row["corner_values"]) == {"Nominal", "nominal_27"}
        for value in row["corner_values"].values():
            assert float(value) == pytest.approx(expected, rel=0.02)
    rdb = work / lib / cell / "maestro/results/maestro" / (history + ".rdb")
    with sqlite3.connect(f"file:{rdb}?mode=ro", uri=True) as conn:
        assert conn.execute("select count(*) from point").fetchone()[0] == 4
        assert conn.execute("select count(*) from resultValue where errorID is not null").fetchone()[0] == 0
        statuses = conn.execute("select error.message from testStatus join error using(errorID)").fetchall()
        assert statuses == [("done",)] * 4
    logs = list((work / "simulation" / lib / cell / "maestro/results/maestro" / history).glob("[1-4]/AC/psf/spectre.out"))
    assert len(logs) == 4
    assert all("spectre completes with 0 errors" in log.read_text() for log in logs)
    original_netlist = logs[0].parent.parent / 'netlist'
    copied_netlist = work / 'copied-point-netlist'
    client.maestro.copy_point_netlist(str(original_netlist), str(copied_netlist))
    assert (copied_netlist / 'input.scs').read_bytes() == (original_netlist / 'input.scs').read_bytes()
    (work / 'point-netlist-copy.json').write_text(json.dumps({'source':str(original_netlist),'copy':str(copied_netlist),'sha256':hashlib.sha256((copied_netlist/'input.scs').read_bytes()).hexdigest()}))

    (work / "maestro-run-pass.json").write_text(json.dumps({"status": "PASS", "history": history, "scope": "actual two-point RC AC sweep; not PDK corners or chip signoff"}, indent=2))
    mc = client.maestro.configure_monte_carlo(MonteCarloConfig(points=3, seed=20261123), session=gui, timeout=30)
    (work / "mc-config.json").write_text(mc.model_dump_json(indent=2))
    assert mc.after.points == 3 and mc.after.seed == 20261123
    client.maestro.set_current_run_mode("Single Run, Sweeps and Corners", session=gui)
    client.maestro.save_setup(lib, cell, session=gui)
    client.maestro.close_gui_session(gui, save=False)
    reopened = client.maestro.open_gui_session(lib, cell)
    persisted = client.maestro.get_monte_carlo_config(reopened, timeout=30)
    assert persisted.points == 3 and persisted.seed == 20261123
    (work / "mc-persisted-config.json").write_text(persisted.model_dump_json(indent=2))
    client.maestro.close_gui_session(reopened, save=False)
