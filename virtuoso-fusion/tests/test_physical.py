"""Exercise real subprocess scheduling and conservative Calibre report verdicts."""
import json
from pathlib import Path
import sys

import pytest

from virtuoso_fusion.physical import PhysicalVerification, parse_drc, parse_lvs


@pytest.fixture
def inputs(tmp_path):
    pdk = tmp_path / "pdk"
    pdk.mkdir()
    (pdk / "rules").write_text("// unchanged rules\n")
    deck = pdk / "calibre.deck"
    deck.write_text('''LAYOUT PATH "old.gds"
LAYOUT PRIMARY "old"
SOURCE PATH "old.cdl"
SOURCE PRIMARY "old"
DRC SUMMARY REPORT "old.sum" // retain comment
DRC RESULTS DATABASE "old.db" ASCII
LVS REPORT "old.rep"
LVS REPORT MAXIMUM 1000
PEX NETLIST "net.dist" HSPICE LAYOUTNAMES
PEX NETLIST SIMPLE "net.simple" HSPICE
INCLUDE ./rules
''')
    layout = tmp_path / "dut.gds"
    layout.write_bytes(b"test-layout")
    source = tmp_path / "dut.cdl"
    source.write_text(".subckt dut d g s b\n.ends dut\n")
    return dict(deck=deck, layout=layout, primary="dut", source=source,
                work_dir=tmp_path / "runs")


def executable(tmp_path, body):
    path = tmp_path / "fake-calibre"
    path.write_text(f"#!{sys.executable}\n" + '''from pathlib import Path
import re, sys, time
text = Path(sys.argv[-1]).read_text()
def output(key):
    return Path(re.search(r'(?m)^' + key + r' +"([^"\\n]+)"', text)[1])
''' + body)
    path.chmod(0o755)
    return str(path)


def test_drc_zero_exit_with_violations_is_not_clean(tmp_path, inputs):
    tool = executable(tmp_path, '''output("DRC SUMMARY REPORT").write_text("TOTAL DRC RuleChecks Executed: 3\\nTOTAL DRC Results Generated: 2\\nRULECHECK M1.W ... TOTAL Result Count = 2\\n")
''')
    result = PhysicalVerification(tool).run("drc", **inputs)
    assert result["execution_ok"] is True
    assert result["checks_passed"] is False
    assert result["ok"] is False
    assert result["parsed"]["results"] == 2
    assert result["parsed"]["violations"] == {"M1.W": 2}
    assert result["hashes"]["deck_source"]
    patched = Path(result["deck"]).read_text()
    assert f'INCLUDE "{inputs["deck"].parent / "rules"}"' in patched
    assert 'LVS REPORT MAXIMUM 1000' in patched
    assert 'PEX NETLIST SIMPLE "net.simple" HSPICE' in patched
    assert 'DRC SUMMARY REPORT "old.sum"' in inputs["deck"].read_text()
    assert json.loads((Path(result["run_dir"]) / "manifest.json").read_text())["ok"] is False


def test_drc_clean_and_missing_report_in_fresh_runs(tmp_path, inputs):
    tool = executable(tmp_path, 'output("DRC SUMMARY REPORT").write_text("TOTAL DRC RuleChecks Executed: 3\\nTOTAL DRC Results Generated: 0\\n")\n')
    api = PhysicalVerification(tool)
    first = api.run("drc", **inputs)
    assert first["ok"] is True
    executable(tmp_path, 'print("no report")\n')
    second = api.run("drc", **inputs)
    assert first["run_dir"] != second["run_dir"]
    assert second["execution_ok"] is True
    assert second["checks_passed"] is None
    assert second["ok"] is False


@pytest.mark.parametrize("report,expected", [
    ("CELL foo CORRECT\nOVERALL COMPARISON RESULTS\n # INCORRECT #\nCELL SUMMARY\nCORRECT child", False),
    ("OVERALL COMPARISON RESULTS\n # CORRECT #\nCELL SUMMARY\nINCORRECT text", True),
    ("OVERALL COMPARISON RESULTS\n no verdict\nCELL SUMMARY\nCORRECT child", None),
    ("CORRECT child", None),
    ("OVERALL COMPARISON RESULTS\n NOT COMPARED\n", False),
])
def test_lvs_only_overall_section(tmp_path, report, expected):
    path = tmp_path / "lvs.rep"
    path.write_text(report)
    assert parse_lvs(path)["checks_passed"] is expected


def test_lvs_report_execution(tmp_path, inputs):
    tool = executable(tmp_path, 'output("LVS REPORT").write_text("OVERALL COMPARISON RESULTS\\n # CORRECT #\\nCELL SUMMARY\\n")\n')
    result = PhysicalVerification(tool).run("lvs", **inputs)
    assert result["ok"] is True
    assert result["checks_passed"] is True


def test_pex_three_phases_and_recursive_netlist(tmp_path, inputs):
    tool = executable(tmp_path, '''with Path("phases").open("a") as f: f.write(" ".join(sys.argv[1:-1]) + "\\n")
if "-fmt" in sys.argv:
    output("PEX NETLIST").write_text('.include "part.pxi"\\nM1 d g s b nch\\n')
    Path("part.pxi").write_text("C1 d s 1f\\nR1 d e 2\\n")
''')
    result = PhysicalVerification(tool).run("rcx", netlist="custom.sp", **inputs)
    assert result["kind"] == "pex"
    assert result["ok"] is True
    assert result["checks_passed"] is None
    assert result["extraction_complete"] is True
    assert result["parsed"]["capacitors"] == 1
    assert result["parsed"]["resistors"] == 1
    assert Path(result["netlist"]).name == "custom.sp"
    assert [p["phase"] for p in result["phase_results"]] == ["phdb", "pdb", "fmt"]
    assert (Path(result["run_dir"]) / "phases").read_text().splitlines() == ["-xrc -phdb", "-xrc -pdb -rcc", "-xrc -fmt -all"]


@pytest.mark.parametrize("body", ['pass\n', 'output("PEX NETLIST").write_text("M1 d g s b nch\\n")\n', 'output("PEX NETLIST").write_text(".include missing.pxi\\nC1 d s 1f\\n")\n'])
def test_pex_missing_products_not_success(tmp_path, inputs, body):
    result = PhysicalVerification(executable(tmp_path, body)).run("pex", **inputs)
    assert result["execution_ok"] is True
    assert result["extraction_complete"] is False
    assert result["ok"] is False


@pytest.mark.parametrize("body,timeout,timed_out", [("sys.exit(9)\n", 2, False), ("time.sleep(2)\n", .05, True)])
def test_pex_stops_after_failed_phase(tmp_path, inputs, body, timeout, timed_out):
    result = PhysicalVerification(executable(tmp_path, body)).run("pex", timeout=timeout, **inputs)
    assert result["execution_ok"] is False
    assert len(result["phase_results"]) == 1
    assert result["phase_results"][0]["timed_out"] is timed_out
    assert result["ok"] is False


def test_missing_executable_returns_evidence(tmp_path, inputs):
    result = PhysicalVerification(str(tmp_path / "absent")).run("drc", **inputs)
    assert result["execution_ok"] is False
    assert result["phase_results"][0]["error"]
    assert (Path(result["run_dir"]) / "manifest.json").exists()


def test_extensions_are_invoked_and_instance_scoped(tmp_path, inputs):
    api = PhysicalVerification()
    calls = []
    def runner(**context):
        calls.append(context)
        report = context["run_dir"] / "report.txt"
        report.write_text("custom result")
        return {"execution_ok": True, "report": str(report)}
    api.register_flow("custom", runner)
    api.register_parser("custom", lambda report: {"checks_passed": report.read_text() == "custom result"})
    result = api.run("custom", **inputs)
    assert calls[0]["layout"] == inputs["layout"].resolve()
    assert result["ok"] is True
    with pytest.raises(ValueError, match="Unknown"):
        PhysicalVerification().run("custom", **inputs)


@pytest.mark.parametrize("line", ['LAYOUT PATH "one" "two"', 'LAYOUT PATH old\\', 'INCLUDE rules extra'])
def test_unsupported_deck_syntax_fails_before_execution(tmp_path, inputs, line):
    original = inputs["deck"].read_text()
    if line.startswith("LAYOUT"):
        original = original.replace('LAYOUT PATH "old.gds"', line)
    else:
        original = original.replace('INCLUDE ./rules', line)
    inputs["deck"].write_text(original)
    with pytest.raises(ValueError, match="Unsupported"):
        PhysicalVerification("not-called").run("drc", **inputs)


def test_missing_required_io_statement_rejected(inputs):
    inputs["deck"].write_text(inputs["deck"].read_text().replace('LAYOUT PATH "old.gds"\n', ''))
    with pytest.raises(ValueError, match="LAYOUT PATH"):
        PhysicalVerification().run("drc", **inputs)


def test_drc_missing_count_is_unknown(tmp_path):
    path = tmp_path / "report"
    path.write_text("TOTAL DRC Results Generated: 0\n")
    assert parse_drc(path)["checks_passed"] is None


def test_pex_rejects_external_old_include(tmp_path, inputs):
    old = tmp_path / "old.pxi"
    old.write_text("C1 d s 1f\n")
    netlist = f".include {old}\n"
    tool = executable(tmp_path, f'output("PEX NETLIST").write_text({netlist!r})\n')
    result = PhysicalVerification(tool).run("pex", **inputs)
    assert result["ok"] is False
    assert result["parsed"]["external_files"] == [str(old)]


def test_pex_registered_report_parser_preserves_metadata(tmp_path, inputs):
    tool = executable(tmp_path, 'output("PEX NETLIST").write_text("C1 d s 1f\\n")\noutput("LVS REPORT").write_text("extra report")\n')
    api = PhysicalVerification(tool)
    api.register_parser("pex", lambda report: {"custom_report": report.read_text()})
    result = api.run("pex", **inputs)
    assert result["parsed"]["custom_report"] == "extra report"
    assert result["ok"] is True
    assert result["checks_passed"] is None


def test_absolute_database_output_is_rebased_without_touching_old_file(tmp_path, inputs):
    old = tmp_path / "old.db"
    old.write_text("preserved evidence")
    deck = inputs["deck"]
    deck.write_text(deck.read_text().replace('DRC RESULTS DATABASE "old.db" ASCII',
                                           f'DRC RESULTS DATABASE "{old}" ASCII'))
    tool = executable(tmp_path, 'output("DRC RESULTS DATABASE").write_text("new database")\noutput("DRC SUMMARY REPORT").write_text("TOTAL DRC RuleChecks Executed: 1\\nTOTAL DRC Results Generated: 0\\n")\n')
    result = PhysicalVerification(tool).run("drc", **inputs)
    assert old.read_text() == "preserved evidence"
    assert (Path(result["run_dir"]) / "drc.db").read_text() == "new database"
    assert f'DRC RESULTS DATABASE "{result["run_dir"]}/drc.db" ASCII' in Path(result["deck"]).read_text()
    assert result["ok"] is True


@pytest.mark.parametrize("statement", [
    'PEX NETLIST SIMPLE "/old/net.simple" HSPICE',
    'MASK SVDB DIRECTORY "/old/svdb" XRC',
    'PEX REPORT DISTRIBUTED "../old/report.dist" ASCII',
    'PEX REPORT LUMPED "$OUTPUT/report.lump" ASCII',
    'LVS REPORT "/old/lvs.rep"',
])
def test_unmanaged_output_must_be_local_literal_path(inputs, statement):
    deck = inputs["deck"]
    text = deck.read_text()
    if statement.startswith("LVS REPORT"):
        text = text.replace('LVS REPORT "old.rep"', statement)
    elif statement.startswith("PEX NETLIST SIMPLE"):
        text = text.replace('PEX NETLIST SIMPLE "net.simple" HSPICE', statement)
    else:
        text += statement + "\n"
    deck.write_text(text)
    with pytest.raises(ValueError, match="Unsupported output path"):
        PhysicalVerification("not-called").run("drc", **inputs)
