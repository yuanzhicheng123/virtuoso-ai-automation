"""Acceptance verdicts use actual pytest JUnit results, including skipped runs."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    'live_runner', Path(__file__).parents[1] / 'tools/run_live.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize('source,expected_status,expected_counts', [
    ('import pytest\n@pytest.mark.skip(reason="opt-in flag absent")\n'
     'def test_live(): pass\n',
     'INCOMPLETE', dict(tests=1, passed=0, skipped=1, errors=0, failures=0)),
    ('import pytest\ndef test_pass(): pass\n'
     '@pytest.mark.skip(reason="optional")\ndef test_optional(): pass\n',
     'PASS', dict(tests=2, passed=1, skipped=1, errors=0, failures=0)),
    ('def test_fail(): assert False\n',
     'FAIL', dict(tests=1, passed=0, skipped=0, errors=0, failures=1)),
    ('raise RuntimeError("collection failed")\n',
     'FAIL', dict(tests=1, passed=0, skipped=0, errors=1, failures=0)),
    ('# Empty selection\n',
     'INCOMPLETE', dict(tests=0, passed=0, skipped=0, errors=0, failures=0)),
])
def test_real_pytest_report_verdict(tmp_path, source, expected_status, expected_counts):
    test_file = tmp_path / 'test_acceptance.py'
    test_file.write_text(source)
    report = tmp_path / 'live.xml'
    proc = subprocess.run(
        [sys.executable, '-I', '-m', 'pytest', '-c', '/dev/null',
         '-p', 'no:cacheprovider', str(test_file), '--junitxml=' + str(report), '-q'],
        cwd=tmp_path, env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=30)
    result = runner.summarize_test_run(report, proc.returncode)
    assert result['status'] == expected_status, proc.stdout + proc.stderr
    assert result['test_counts'] == expected_counts
    assert result['exit_code'] == proc.returncode
    assert 'skipped tests are not validated' in result['scope']
    if expected_counts['skipped'] == expected_counts['tests'] == 1:
        assert proc.returncode == 0


def test_missing_report_cannot_establish_acceptance(tmp_path):
    with pytest.raises(FileNotFoundError):
        runner.summarize_test_run(tmp_path / 'missing.xml', 0)
