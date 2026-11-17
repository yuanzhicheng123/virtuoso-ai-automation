"""AC 复数分支单元测试（本地 fixture，非真机证据）。"""
import cmath, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vskill.phase2.capabilities import check_ac_config, check_ac_excitation, cplx  # noqa: E402


def test_cplx_roundtrip_quadrants():
    for z in (1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j, 0 + 0j, 0 + 2j):
        d = cplx(z)
        assert abs(d["re"] - z.real) < 1e-12 and abs(d["im"] - z.imag) < 1e-12
        assert abs(d["mag"] - abs(z)) < 1e-12
        assert abs(d["phase_deg"] - cmath.phase(z) * 180 / cmath.pi) < 1e-9


def test_ac_config_rejects_illegal():
    assert check_ac_config({"start": 0, "stop": 1e6}) == "AC_CONFIG_INVALID"
    assert check_ac_config({"start": 1e6, "stop": 10}) == "AC_CONFIG_INVALID"
    assert check_ac_config({"start": 10, "stop": 1e6, "dec": 0}) == "AC_CONFIG_INVALID"
    assert check_ac_config({"start": 10, "stop": 1e6, "dec": 10}) is None


def test_ac_excitation_zero_rejected():
    assert check_ac_excitation(0) == "AC_EXCITATION_ZERO"
    assert check_ac_excitation(1.0) is None
