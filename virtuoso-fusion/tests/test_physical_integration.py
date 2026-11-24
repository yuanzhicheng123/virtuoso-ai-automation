import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_public_client_oa_facade():
    from virtuoso_fusion import FusionClient
    with FusionClient() as client:
        assert hasattr(client, 'oa')
        assert callable(client.oa.copy_geometry)
        assert callable(client.oa.export_gds)


def test_discovery_reports_presence_not_license_checkout(tmp_path):
    from virtuoso_fusion.environment import discover_tools
    deck = tmp_path / 'input.rule'
    deck.write_text('test rule')
    result = discover_tools(executables={'python': sys.executable, 'missing': str(tmp_path/'absent')}, decks=[deck])
    assert result['tools']['python']['path'] == sys.executable
    assert result['tools']['missing']['path'] is None
    assert result['license_checked_out'] is False
    assert result['decks'][0]['exists'] is True


def test_physical_cli_violations_are_not_success(tmp_path):
    tool = tmp_path / 'calibre'
    tool.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath("drc.rep").write_text("TOTAL DRC RuleChecks Executed: 1\\nTOTAL DRC Results Generated: 2\\nRULECHECK M1.W ... TOTAL Result Count = 2 (2)\\n")\n')
    tool.chmod(0o755)
    deck = tmp_path / 'rules.drc'
    deck.write_text('LAYOUT PATH "old.gds"\nLAYOUT PRIMARY "old"\nDRC SUMMARY REPORT "DRC.rep"\nDRC RESULTS DATABASE "DRC.db" ASCII\n')
    gds = tmp_path / 'layout.gds'
    gds.write_bytes(b'fixture input; temporary test tool only')
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src'))
    proc = subprocess.run([sys.executable, '-m', 'virtuoso_fusion', 'physical', 'drc',
        '--deck', str(deck), '--layout', str(gds), '--primary', 'top',
        '--work-dir', str(tmp_path/'runs'), '--calibre', str(tool)], env=env, text=True, capture_output=True)
    assert proc.returncode == 2, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result['execution_ok'] is True
    assert result['checks_passed'] is False
    assert result['ok'] is False


def test_physical_cli_requires_source_for_lvs(tmp_path):
    proc = subprocess.run([sys.executable, '-m', 'virtuoso_fusion', 'physical', 'lvs',
        '--deck', str(tmp_path/'no.rule'), '--layout', str(tmp_path/'no.gds'),
        '--primary', 'top', '--work-dir', str(tmp_path)],
        env=dict(os.environ, PYTHONPATH=str(ROOT/'src')), capture_output=True, text=True)
    assert proc.returncode != 0
    assert 'source' in proc.stdout.lower() + proc.stderr.lower()
