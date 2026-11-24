"""Opt-in MCP handler-to-EDA checks; transport framing is tested separately."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('VF_LIVE_MCP_SURFACE') != '1' or not os.environ.get('VF_LIVE_PORT'),
    reason='requires VF_LIVE_MCP_SURFACE=1 and an isolated authenticated live runner',
)


class SurfaceEvidence:
    def __init__(self, server, root, token):
        self.server, self.root, self.token = server, root, token
        self.report = {
            'scope': 'real dispatch_tool handler-to-EDA/API calls; not stdio transport coverage or chip signoff',
            'tools': {name: {'status': 'SKIP', 'reason': 'outside this live test file scope', 'calls': []}
                      for name in server.TOOL_NAMES},
        }
        self.save()

    def save(self):
        text = json.dumps(self.report, indent=2, default=str).replace(self.token, '[REDACTED]')
        (self.root / 'mcp-surface-report.json').write_text(text + '\n')

    @contextmanager
    def call(self, name, arguments, *, expect_error=False):
        entry = self.report['tools'][name]
        record = {'request': arguments, 'status': 'FAIL'}
        entry['calls'].append(record)
        started = time.monotonic()
        try:
            response = self.server.dispatch_tool(name, arguments, {})
            recorded = dict(response)
            recorded['content'] = [
                {**{key: value for key, value in item.items() if key != 'data'},
                 'data_omitted': True, 'base64_sha256': hashlib.sha256(item['data'].encode()).hexdigest()}
                if item.get('type') == 'image' else item
                for item in response.get('content', [])
            ]
            record['result'] = recorded
            assert bool(response.get('isError')) is expect_error, recorded
            yield response.get('structuredContent', {})
        except pytest.skip.Exception as error:
            record.update(status='SKIP', reason=str(error))
            raise
        except Exception as error:
            record.update(status='FAIL', reason=f'{type(error).__name__}: {error}')
            raise
        else:
            record.update(status='PASS', reason='response and tool-specific assertions passed')
        finally:
            record['seconds'] = time.monotonic() - started
            statuses = [item['status'] for item in entry['calls']]
            entry['status'] = 'FAIL' if 'FAIL' in statuses else 'SKIP' if 'SKIP' in statuses else 'PASS'
            entry['reason'] = next(item['reason'] for item in entry['calls']
                                   if item['status'] == entry['status'])
            self.save()

    def skip(self, name, reason):
        self.report['tools'][name].update(status='SKIP', reason=reason)
        self.save()
        pytest.skip(reason)


@pytest.fixture(scope='module')
def surface():
    from virtuoso_fusion import FusionClient, mcp
    root = Path(os.environ['VF_LIVE_ROOT'])
    token = Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip()
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('VB_WORK_DIR', str(root))
        environment.setenv('VB_REMOTE_SCRATCH_ROOT', str(root / 'mcp-scratch'))
        with FusionClient(port=int(os.environ['VF_LIVE_PORT']), timeout=30, log_to_ciw=False,
                          daemon_token=token, evidence_dir=root / 'mcp-calls') as client:
            assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
            previous = dict(mcp.server._CLIENT)
            mcp.server._CLIENT['obj'] = client
            evidence = SurfaceEvidence(mcp.server, root, token)
            try:
                lib = 'vf_mcp_' + root.name.replace('-', '_')
                client.library.create(lib, str(root / lib))
                with client.schematic.create(lib, 'baseline') as editor:
                    editor.add('t')
                yield client, lib, root, evidence
            finally:
                evidence.save()
                mcp.server._CLIENT.clear()
                mcp.server._CLIENT.update(previous)


@pytest.mark.parametrize('name', ['status', 'capture_session'])
def test_cli_delegates_require_separate_isolated_configuration(surface, name):
    _, _, _, evidence = surface
    evidence.skip(name, 'handler starts an independent CLI and ignores the bound client; '
                  'VF_LIVE_* does not isolate CLI profile/state, so default sessions are not accessed')


@pytest.mark.parametrize('name', ['list_libraries', 'list_cells'])
def test_library_reads(surface, name):
    _, lib, _, evidence = surface
    with evidence.call(name, {} if name == 'list_libraries' else {'lib': lib}) as data:
        if name == 'list_libraries':
            assert lib in data['libs'] and 'analogLib' in data['libs']
        else:
            assert data['lib'] == lib and 'baseline:schematic' in data['cells']


@pytest.mark.parametrize('name', ['lookup_skill', 'describe_skill'])
def test_targeted_skill_finder(surface, name):
    _, _, _, evidence = surface
    argument = 'query' if name == 'lookup_skill' else 'func'
    with evidence.call(name, {argument: 'getVersion'}) as data:
        if not data['hits']:
            pytest.skip('getVersion has no SKILL Finder documentation in this environment')
        assert 'getVersion' in json.dumps(data['hits'])


def test_evaluate_error_and_recovery(surface):
    client, _, root, evidence = surface
    with evidence.call('evaluate', {'expr': 'quotient(1 0)', 'timeout': 10}, expect_error=True) as data:
        assert data['errors']
        # An immediate SKILL error is known; only an unknown timeout needs receipt recovery.
        assert data['metadata']['skill_error']
        events = (root / 'mcp-calls' / (client._fusion_id + '.jsonl')).read_text().splitlines()
        original = json.loads(events[-1])
        assert original['skill'] == 'quotient(1 0)'
        assert data == original['result']
    with evidence.call('evaluate', {'expr': 'plus(20 22)', 'timeout': 10}) as data:
        assert data['output'].strip() == '42'


def test_individual_schematic_handlers(surface):
    client, lib, root, evidence = surface
    target = {'lib': lib, 'cell': 'individual_rc'}
    with evidence.call('new_schematic', target) as data:
        assert data == target
    for master, name, x in [('res', 'R0', 0), ('cap', 'C0', 3)]:
        with evidence.call('add_part', {**target, 'master_lib': 'analogLib',
                           'master_cell': master, 'name': name, 'x': x, 'y': 0}) as data:
            assert data['inst'] == name
    with evidence.call('add_wire', {**target, 'from_inst': 'R0', 'from_term': 'MINUS',
                                   'to_inst': 'C0', 'to_term': 'PLUS'}) as data:
        assert data['ok'] is True
    for inst, term, name in [('R0', 'PLUS', 'IN'), ('C0', 'PLUS', 'OUT'), ('C0', 'MINUS', 'VSS')]:
        with evidence.call('add_pin', {**target, 'at_inst': inst, 'at_term': term, 'name': name}) as data:
            assert data['pin'] == name
    with evidence.call('check_schematic', target) as data:
        assert data['checked'] and data['saved'] and data['errors'] == 0
        readback = client.schematic.read(lib, target['cell'])
        assert {inst['name'] for inst in readback['instances']} >= {'R0', 'C0'}
        (root / 'mcp-individual-oa.json').write_text(json.dumps(readback, indent=2, default=str))
        _assert_rc_connectivity(client, lib, target['cell'])


def test_batched_schematic_handler(surface):
    client, lib, root, evidence = surface
    operations = [
        {'op': 'inst', 'master_lib': 'analogLib', 'master_cell': 'res', 'name': 'R0', 'x': 0, 'y': 0},
        {'op': 'inst', 'master_lib': 'analogLib', 'master_cell': 'cap', 'name': 'C0', 'x': 3, 'y': 0},
        {'op': 'wire', 'from_inst': 'R0', 'from_term': 'MINUS', 'to_inst': 'C0', 'to_term': 'PLUS'},
        {'op': 'pin', 'at_inst': 'R0', 'at_term': 'PLUS', 'name': 'IN'},
        {'op': 'pin', 'at_inst': 'C0', 'at_term': 'PLUS', 'name': 'OUT'},
        {'op': 'pin', 'at_inst': 'C0', 'at_term': 'MINUS', 'name': 'VSS'},
        {'op': 'label', 'inst': 'R0', 'term': 'PLUS', 'net': 'IN'},
    ]
    with evidence.call('build_schematic', {'lib': lib, 'cell': 'batch_rc', 'ops': operations}) as data:
        assert data == {'inst': 2, 'wire': 1, 'pin': 3, 'label': 1}
        _assert_rc_connectivity(client, lib, 'batch_rc')
        (root / 'mcp-batch-oa.json').write_text(json.dumps(
            client.schematic.read(lib, 'batch_rc'), indent=2, default=str))


def _assert_rc_connectivity(client, lib, cell):
    result = client.execute_skill(
        f'let((cv r c rp rn cp cn answer) cv=dbOpenCellViewByType("{lib}" "{cell}" "schematic" "" "r") '
        'r=dbFindAnyInstByName(cv "R0") c=dbFindAnyInstByName(cv "C0") '
        'rp=car(setof(term r~>instTerms term~>name=="PLUS")) '
        'rn=car(setof(term r~>instTerms term~>name=="MINUS")) '
        'cp=car(setof(term c~>instTerms term~>name=="PLUS")) '
        'cn=car(setof(term c~>instTerms term~>name=="MINUS")) '
        'answer=(rp~>net~>name=="IN" && rn~>net==cp~>net && cp~>net~>name=="OUT" '
        '&& cn~>net~>name=="VSS") dbClose(cv) answer)', retry_connect=False)
    assert result.ok and result.output.strip() == 't', result


def test_screenshot_uses_owned_gui(surface):
    client, _, root, evidence = surface
    if not os.environ.get('DISPLAY'):
        evidence.skip('screenshot', 'DISPLAY is absent; use the live runner --gui option')
    available = client.execute_skill("if(getd('hiWindowSaveImage) t nil)", retry_connect=False)
    assert available.ok, available
    if available.output.strip() != 't':
        evidence.skip('screenshot', 'hiWindowSaveImage is not available in this Virtuoso context')
    with evidence.call('screenshot', {'target': 'ciw', 'out': str(root / 'mcp-shots')}) as data:
        path = Path(data['file'])
        assert path.is_relative_to(root)
        assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        assert data['bytes'] == path.stat().st_size


def test_read_waveform_real_rc_transient(surface):
    from virtuoso_fusion import SpectreSimulator
    from virtuoso_bridge.spectre.psf import result_file
    _, _, root, evidence = surface
    deck = root / 'mcp-rc.scs'
    deck.write_text('''simulator lang=spectre
global 0
V0 (vin 0) vsource type=pulse val0=0 val1=1 delay=1n rise=10p fall=10p width=4n period=8n
R0 (vin out) resistor r=1k
C0 (out 0) capacitor c=1p
tr1 tran stop=8n maxstep=20p
save vin out
''')
    result = SpectreSimulator.local(timeout=60, work_dir=root / 'mcp-simulation',
                                    license_queue_timeout=0).run_simulation(deck)
    (root / 'mcp-simulation-result.json').write_text(result.model_dump_json(indent=2))
    assert result.ok, result
    raw = result_file(Path(result.metadata['output_dir']), 'tr1.tran.tran')
    with evidence.call('read_waveform', {'path': str(raw), 'signals': ['out'], 'at': [0, 4e-9]}) as data:
        stats = data['stats']['out']
        assert stats['numeric'] > 100
        assert stats['min'] == pytest.approx(0, abs=1e-6)
        assert 0.95 < stats['max'] < 1.001
        assert data['samples'][0]['out'] == pytest.approx(0, abs=1e-6)
        assert 0.90 < data['samples'][1]['out'] < 1.0
        assert data['chart']['status'] not in {'unavailable', 'omitted: image exceeds 500 KB'}
