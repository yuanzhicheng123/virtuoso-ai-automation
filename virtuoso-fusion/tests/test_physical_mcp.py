"""Exercise physical MCP tools through JSON-RPC and real child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def stdio(requests):
    process = subprocess.run(
        [sys.executable, '-m', 'virtuoso_fusion.mcp'],
        input=''.join(json.dumps(request) + '\n' for request in requests),
        env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
        capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert all('result' in reply for reply in replies), replies
    return [reply['result'] for reply in replies]


def call(name, arguments):
    return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments}}


def test_physical_tools_schema_and_readonly_discovery(tmp_path):
    deck = tmp_path / 'rules.svrf'
    deck.write_text('// explicit deck\n')
    listing, discovery = stdio([
        {'jsonrpc': '2.0', 'id': 0, 'method': 'tools/list'},
        call('discover_tools', {'executables': {'python': sys.executable},
                                'decks': [str(deck), str(tmp_path / 'missing')]}),
    ])
    definitions = {item['name']: item for item in listing['tools']}
    assert len(definitions) == 31
    for name in ('discover_tools', 'run_physical'):
        assert definitions[name]['inputSchema']['additionalProperties'] is False
    assert definitions['discover_tools']['annotations']['readOnlyHint'] is True
    assert definitions['run_physical']['annotations']['readOnlyHint'] is False
    assert definitions['run_physical']['annotations']['idempotentHint'] is False
    assert set(definitions['run_physical']['inputSchema']['required']) == {
        'kind', 'deck', 'layout', 'primary', 'work_dir'}
    data = discovery['structuredContent']
    assert data['tools']['python']['path'] == sys.executable
    assert [item['exists'] for item in data['decks']] == [True, False]
    assert data['license_checked_out'] is False


@pytest.fixture
def physical_inputs(tmp_path):
    deck = tmp_path / 'rules.svrf'
    deck.write_text('''LAYOUT PATH "old.gds"
LAYOUT PRIMARY "dut"
SOURCE PATH "old.cdl"
SOURCE PRIMARY "dut"
DRC SUMMARY REPORT "old.drc"
LVS REPORT "old.lvs"
PEX NETLIST "old.dist" HSPICE
''')
    layout = tmp_path / 'dut.gds'
    layout.write_bytes(b'fixture')
    source = tmp_path / 'dut.cdl'
    source.write_text('.subckt dut d g s b\n.ends dut\n')
    return {'deck': str(deck), 'layout': str(layout), 'source': str(source),
            'primary': 'dut', 'work_dir': str(tmp_path / 'runs')}


@pytest.mark.parametrize('kind,report,netlist,expected', [
    ('drc', 'TOTAL DRC RuleChecks Executed: 1737\nTOTAL DRC Results Generated: 29\n', '', False),
    ('drc', 'TOTAL DRC RuleChecks Executed: 3\nTOTAL DRC Results Generated: 0\n', '', True),
    ('lvs', 'OVERALL COMPARISON RESULTS\n CORRECT\nCELL SUMMARY\n', '', True),
    ('pex', '', 'C1 d s 1f\nR1 d e 2\n', True),
    ('rcx', '', 'M1 d g s b nch\n', False),
])
def test_physical_stdio_keeps_execution_and_verification_distinct(
        tmp_path, physical_inputs, kind, report, netlist, expected):
    executable = tmp_path / 'calibre'
    executable.write_text(f'''#!{sys.executable}
from pathlib import Path
import re, sys
text = Path(sys.argv[-1]).read_text()
def output(key):
    return Path(re.search(r'(?m)^' + key + r' +"([^"\\n]+)"', text)[1])
if '-drc' in sys.argv:
    output('DRC SUMMARY REPORT').write_text({report!r})
elif '-lvs' in sys.argv:
    output('LVS REPORT').write_text({report!r})
elif '-fmt' in sys.argv:
    output('PEX NETLIST').write_text({netlist!r})
''')
    executable.chmod(0o755)
    result, = stdio([call('run_physical', {
        **physical_inputs, 'kind': kind, 'calibre': str(executable),
        'timeout': 10, 'netlist': 'custom.dist'})])
    data = result['structuredContent']
    assert result.get('isError', False) is not expected
    assert data['execution_ok'] is True
    assert data['ok'] is expected
    assert result['ttlMs'] == 0
    assert json.loads((Path(data['run_dir']) / 'manifest.json').read_text()) == data
    if kind in ('pex', 'rcx'):
        assert data['checks_passed'] is None
        assert data['extraction_complete'] is expected
        assert [phase['phase'] for phase in data['phase_results']] == ['phdb', 'pdb', 'fmt']
        assert Path(data['netlist']).name == 'custom.dist'
    else:
        assert data['checks_passed'] is expected
    if kind == 'drc' and not expected:
        assert data['parsed']['results'] == 29


def test_physical_validation_rejects_unknown_and_missing_before_run(physical_inputs):
    results = stdio([
        call('run_physical', {**physical_inputs, 'kind': 'drc', 'surprise': True}),
        call('run_physical', {'kind': 'lvs', 'deck': physical_inputs['deck']}),
        call('discover_tools', {'unexpected': True}),
    ])
    assert all(result['isError'] for result in results)
    assert not Path(physical_inputs['work_dir']).exists()
