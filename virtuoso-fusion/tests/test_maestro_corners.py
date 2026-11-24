"""Regression against the real 220 Detail export, through the public facade."""
import json
from pathlib import Path

import pytest
from virtuoso_bridge import ExecutionStatus, VirtuosoResult
from virtuoso_bridge.virtuoso.maestro.reader.runs import _parse_detail_csv
from virtuoso_fusion import FusionClient

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL = (FIXTURES / "maestro_corner_detail.csv").read_text()


class ExportClient(FusionClient):
    """Replace only transport; exercise the real export reader and Fusion facade."""

    def __init__(self, csv_text):
        super().__init__()
        self.csv_text = csv_text
        self.calls = []

    def execute_skill(self, code, **kwargs):
        self.calls.append(code)
        output = '"failed"' if "maeGetOverallSpecStatus" in code else "nil"
        if "maeGetSetup" in code:
            output = '("AC")'
        return VirtuosoResult(status=ExecutionStatus.SUCCESS, output=output)

    def download_file(self, remote, local):
        Path(local).write_text(self.csv_text)


def read(csv_text=DETAIL, **kwargs):
    with ExportClient(csv_text) as client:
        result = client.maestro.read_results("session", lib="LIB", cell="cell",
                                             history="Interactive.1", **kwargs)
        assert any("maeExportOutputView" in call for call in client.calls)
        return result


def test_real_sweep_corner_export_preserves_four_scalar_values_and_metadata():
    result = read()
    original = _parse_detail_csv(DETAIL, history="Interactive.1")
    for key, value in original.items():
        assert result[key] == value
    assert result["overall_spec"] == "failed"
    assert result["overall_yield"] is None
    assert "raw_csv" not in result
    assert result["point_semantics"] == "sweep_group"
    assert result["detail_columns"] == ["Point", "Test", "Output", "Nominal", "Spec", "Weight", "Pass/Fail", "Min", "Max", "nominal_27"]
    assert result["corner_columns"] == ["Nominal", "nominal_27"]
    scalar_rows = [row for row in result["detail_rows"] if row["name"] == "BW"]
    assert [(row["point"], row["test"], row["parameters"]) for row in scalar_rows] == [
        (1, "AC", {"c_val": "1p"}), (2, "AC", {"c_val": "100f"})]
    values = [value for row in scalar_rows for value in row["corner_values"].values()]
    assert values == ["158.8e6", "158.8e6", "1.588e9", "1.588e9"]
    evidence = json.loads((FIXTURES / "maestro_corner_rdb_evidence.json").read_text())
    rdb_values = [row["value"] for row in evidence["resultValue"] if row["resultID"] == 1]
    assert [float(value) for value in values] == pytest.approx(rdb_values, rel=0.001)
    assert scalar_rows[0]["cells"][7:9] == ["158.8e6", "158.8e6"]
    waveform_rows = [row for row in result["detail_rows"] if row["name"] == "/OUT"]
    assert all(row["corner_values"] == {"Nominal": "", "nominal_27": ""} for row in waveform_rows)


def test_raw_csv_opt_in_preserved():
    assert read(include_raw=True)["raw_csv"] == DETAIL


def test_no_point_format_and_same_output_in_two_tests_are_lossless():
    csv_text = "Test,Output,Nominal,Spec,Weight,Pass/Fail\nAC,gain,2,,,\nTRAN,gain,waveform,,,\n"
    result = read(csv_text)
    assert result["corner_columns"] == ["Nominal"]
    assert [(row["point"], row["test"], row["name"], row["corner_values"]) for row in result["detail_rows"]] == [
        (1, "AC", "gain", {"Nominal": "2"}),
        (1, "TRAN", "gain", {"Nominal": "waveform"}),
    ]


@pytest.mark.parametrize("csv_text", [
    "unexpected report\n",
    "Point,Test,Output,Nominal,Spec,Weight,Pass/Fail\n1,AC,gain,1,,,extra,unheaded\n",
    "Point,Test,Output,Nominal,Spec,Weight,Pass/Fail\n1,AC,gain\n",
])
def test_malformed_export_is_not_silently_accepted(csv_text):
    with pytest.raises(ValueError, match="Detail CSV"):
        read(csv_text)


def test_explicit_point_ids_and_quoted_cells_survive_without_parameter_rows():
    result = read('Point,Test,Output,Nominal,Spec,Weight,Pass/Fail,Min,Max,slow\n'
                  '3,AC,"gain,db",4,,,pass,3,4,3\n'
                  '9,AC,"gain,db",5,,,pass,4,5,4\n')
    assert [row["point"] for row in result["detail_rows"]] == [3, 9]
    assert result["detail_rows"][0]["name"] == "gain,db"
    assert result["detail_rows"][1]["corner_values"] == {"Nominal": "5", "slow": "4"}


@pytest.mark.parametrize("csv_text", [
    'Test,Output,Spec,Weight,Pass/Fail,Min,Max,slow,slow\n',
    'Test,Output,Spec,Weight,Pass/Fail,Min,Max,slow\nAC,gain,,,,1,2\n',
    'Point,Test,Output,Nominal,Spec,Weight,Pass/Fail,slow,slow\n',
    'Point,Test,Output,Nominal,Spec,Weight,Pass/Fail,\n',
    'Point,Test,Output,Nominal,Spec,Weight,Pass/Fail\ninvalid,AC,gain,1,,,\n',
    'Point,Test,Output,Nominal,Spec,Weight,Pass/Fail\n1,AC,"unterminated,1,,,\n',
])
def test_ambiguous_schema_and_invalid_csv_raise(csv_text):
    with pytest.raises(ValueError, match="Detail CSV"):
        read(csv_text)


def test_ic23_corner_column_export_preserves_upstream_points_and_parameters():
    csv_text = (FIXTURES / "maestro_ic23_corner_columns.csv").read_text()
    result = read(csv_text, include_raw=True)
    original = _parse_detail_csv(csv_text, history="Interactive.1")
    for key, value in original.items():
        assert result[key] == value
    assert result["point_semantics"] == "corner_column"
    assert result["corner_columns"] == ["pn_tt_0p8", "pn_ss_0p9"]
    assert result["corner_parameters"] == {
        "pn_tt_0p8": {"design_wrapper.lib.scs": "tt_pre", "vdd": "800e-3"},
        "pn_ss_0p9": {"design_wrapper.lib.scs": "ss_pre", "vdd": "900e-3"},
    }
    assert result["raw_csv"] == csv_text
    waveform, noise, fom = result["detail_rows"]
    assert waveform["point"] is None
    assert waveform["parameters"] == {}
    assert waveform["corner_values"] == {"pn_tt_0p8": "", "pn_ss_0p9": ""}
    assert noise["test"] == "PNOISE"
    assert noise["corner_values"] == {"pn_tt_0p8": "456e-6", "pn_ss_0p9": "365e-6"}
    assert fom["cells"][5:7] == ["5.591", "7.043"]


def test_ic23_same_output_in_two_tests_preserves_both_detail_rows():
    csv_text = (FIXTURES / "maestro_ic23_corner_columns.csv").read_text()
    csv_text += "TRAN,noise,< 1m,,fail,1e-3,2e-3,1e-3,2e-3\n"
    result = read(csv_text)
    noise_rows = [row for row in result["detail_rows"] if row["name"] == "noise"]
    assert [(row["test"], row["corner_values"]) for row in noise_rows] == [
        ("PNOISE", {"pn_tt_0p8": "456e-6", "pn_ss_0p9": "365e-6"}),
        ("TRAN", {"pn_tt_0p8": "1e-3", "pn_ss_0p9": "2e-3"}),
    ]
