from dataclasses import dataclass
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from virtuoso_bridge import ExecutionStatus, VirtuosoResult


def oa_class():
    assert importlib.util.find_spec('virtuoso_fusion.oa') is not None, 'OA facade is missing'
    return importlib.import_module('virtuoso_fusion.oa').FusionOAOps


def test_copy_uses_existing_client_and_preserves_failure():
    expected = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['missing source'], metadata={'recovery_handle': 'a'})
    calls = []
    client = SimpleNamespace(execute_skill=lambda code, **kwargs: calls.append(code) or expected)
    actual = oa_class()(client).copy_geometry('src"lib', 'src', 'dst', 'copy')
    assert actual is expected
    assert '"src\\"lib"' in calls[0]
    assert 'unwindProtect(' in calls[0]
    # IC23.1 requires one cleanup expression after the protected body.
    assert 'progn(\n      when(dst dbClose(dst))\n      when(src dbClose(src))\n    )' in calls[0]
    assert 'src~>instances' in calls[0]
    assert 'dbSave(dst)' in calls[0]
    assert 'done=t' not in calls[0]


def test_copy_rejects_same_cellview_before_execution():
    client = SimpleNamespace(execute_skill=lambda *a, **kw: pytest.fail('must not execute'))
    with pytest.raises(ValueError, match='different'):
        oa_class()(client).copy_geometry('lib', 'cell', 'lib', 'cell')


@dataclass
class ExportOutcome:
    ok: bool = True
    errors: tuple = ()
    warnings: tuple = ('map warning',)
    remote_run_dir: str = '/evidence/run'


def test_named_map_reaches_existing_xstream_and_keeps_metadata(tmp_path):
    seen = []
    def export(library, cell, output_path, **kwargs):
        seen.append((library, cell, output_path, kwargs, Path(kwargs['stream_map']).read_text()))
        return ExportOutcome()
    ops = oa_class()(SimpleNamespace(layout=SimpleNamespace(export_gds=export)))
    ops.register_layer_map('metal', lambda: ('metal.map', 'M1 drawing 31 0\n'))
    result = ops.export_gds('lib', 'cell', 'layout', tmp_path/'out.gds', layer_map='metal')
    assert seen[0][4] == 'M1 drawing 31 0\n'
    assert seen[0][3]['view'] == 'layout'
    assert result['ok'] and result['execution_ok']
    assert result['metadata']['remote_run_dir'] == '/evidence/run'
    assert result['warnings'] == ['map warning']
    assert result['layer_map_name'] == 'metal'
    assert Path(result['stream_map']).read_text() == seen[0][4]


def test_map_registry_is_per_instance(tmp_path):
    cls = oa_class()
    first = cls(SimpleNamespace())
    first.register_layer_map('metal', lambda: ('metal.map', 'M1 drawing 31 0\n'))
    with pytest.raises(FileNotFoundError):
        cls(SimpleNamespace()).export_gds('lib', 'cell', 'layout', tmp_path/'out.gds', layer_map='metal')


def test_xstream_requires_map_and_rejects_unused_environment(tmp_path):
    ops = oa_class()(SimpleNamespace())
    with pytest.raises(ValueError, match='layer_map'):
        ops.export_gds('lib', 'cell', 'layout', tmp_path/'out.gds')
    with pytest.raises(ValueError, match='env'):
        ops.export_gds('lib', 'cell', 'layout', tmp_path/'out.gds', env={'OA_HOME': '/oa'})


@pytest.fixture
def export_program(tmp_path):
    program = tmp_path/'oa2strm'
    program.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path(os.environ['ARGS_FILE']).write_text(json.dumps(args))
if not os.environ.get('NO_GDS'):
    pathlib.Path(args[args.index('-gds')+1]).write_bytes(b'GDS-test-fixture')
print(os.environ.get('TOOL_MESSAGE', ''))
sys.exit(int(os.environ.get('EXIT_CODE', '0')))
''')
    program.chmod(0o755)
    return program


@pytest.mark.parametrize('extra,expected', [({}, True), ({'EXIT_CODE':'2'}, False), ({'TOOL_MESSAGE':'ERROR: failed'}, False), ({'NO_GDS':'1'}, False)])
def test_standalone_exit_and_artifact_are_both_required(tmp_path, export_program, extra, expected):
    args_file = tmp_path/'args.json'
    mapping = tmp_path/'map'
    mapping.write_text('M1 drawing 31 0\n')
    libdef = tmp_path/'lib.defs'
    libdef.write_text('')
    result = oa_class()(SimpleNamespace()).export_gds('lib', 'cell', 'layout', tmp_path/'out.gds', libdef,
        oa2strm=export_program, layer_map=mapping, env={'ARGS_FILE':str(args_file), **extra})
    assert result['ok'] is expected
    args = json.loads(args_file.read_text())
    assert args[args.index('-layerMap')+1] == str(mapping)
    assert args[args.index('-libDefFile')+1] == str(libdef)
    assert result['execution_ok'] is (extra.get('EXIT_CODE', '0') == '0')


def test_standalone_refuses_stale_existing_output(tmp_path, export_program):
    output = tmp_path/'existing.gds'
    output.write_bytes(b'existing')
    libdef = tmp_path/'lib.defs'
    libdef.touch()
    with pytest.raises(FileExistsError):
        oa_class()(SimpleNamespace()).export_gds('lib', 'cell', 'layout', output, libdef, oa2strm=export_program)
    assert output.read_bytes() == b'existing'


@pytest.mark.parametrize('message,field,ok', [
    # Recorded in evidence/physical-api-020-final/oa2strm-map-export.json.
    ('2026/11/23 23:02:52 WARNING This OS does not appear to be a Cadence supported Linux configuration.', 'warnings', True),
    ('2026/11/23 23:02:52 ERROR Export failed.', 'errors', False),
])
def test_standalone_keeps_timestamped_diagnostic_lines(tmp_path, export_program, message, field, ok):
    libdef = tmp_path/'lib.defs'
    libdef.touch()
    result = oa_class()(SimpleNamespace()).export_gds(
        'lib', 'cell', 'layout', tmp_path/'out.gds', libdef, oa2strm=export_program,
        env={'ARGS_FILE': str(tmp_path/'args.json'),
             'TOOL_MESSAGE': message + '\nMessages: 0 errors, 0 warnings'})
    assert result[field] == [message]
    assert result['ok'] is ok
    assert result['execution_ok'] is True
