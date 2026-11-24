"""Installed Fusion physical APIs on isolated N65 OA and real Calibre."""
import json
import os
from pathlib import Path
import struct

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('VF_LIVE_PHYSICAL'), reason='explicit installed physical API acceptance required')


@pytest.fixture(scope='module')
def setup():
    from virtuoso_fusion import FusionClient
    root = Path(os.environ['VF_LIVE_ROOT'])
    with FusionClient(port=int(os.environ['VF_LIVE_PORT']), timeout=30, log_to_ciw=False,
                      daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip(),
                      evidence_dir=root/'calls') as client:
        assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
        library = 'vf_api_' + root.name.replace('-', '_')
        client.library.create(library, str(root/library), technology_library='tsmcN65')
        yield client, library, root


def test_copy_and_reopen_top_level_geometry(setup):
    from virtuoso_bridge.virtuoso.layout import layout_create_rect, layout_read_geometry, parse_layout_geometry_output
    client, lib, root = setup
    with client.layout.create(lib, 'source_geometry') as layout:
        layout.add(layout_create_rect('M1', 'drawing', 0, 0, 2, 1))
    result = client.oa.copy_geometry(lib, 'source_geometry', lib, 'copied_geometry')
    (root/'oa-copy-result.json').write_text(result.model_dump_json())
    assert result.ok, result
    read = client.execute_skill(layout_read_geometry(lib, 'copied_geometry'))
    assert read.ok
    rows = parse_layout_geometry_output(read.output)
    assert len(rows) == 1 and rows[0]['layer'] == 'M1'
    assert rows[0]['bbox'] == [(0.0, 0.0), (2.0, 1.0)]
    (root/'copied-geometry.json').write_text(json.dumps(rows, indent=2))
    bad = client.oa.copy_geometry(lib, 'absent_geometry', lib, 'failed_copy')
    assert not bad.ok
    assert client.call('plus', 1, 2).ok


@pytest.mark.parametrize('route', ['xstream', 'oa2strm'])
def test_named_layer_map_reaches_export(setup, route):
    client, lib, root = setup
    # Change only the simple M1 fixture's GDS layer to prove the registered map is consumed.
    custom_map = 'M1 drawing 231 0\n'
    client.oa.register_layer_map('fixture_map', lambda: ('fixture.map', custom_map))
    options = {}
    if route == 'oa2strm':
        oa_home = Path('/eda/cds/IC2310/share/oa')
        platform = next(p for p in (oa_home/'bin').iterdir() if p.is_dir() and (p/'opt').is_dir())
        shim = root/'oa-home'
        (shim/'bin').mkdir(parents=True)
        for entry in (oa_home/'bin').iterdir():
            (shim/'bin'/entry.name).symlink_to(entry)
        if not (shim/'bin/unknown_64').exists():
            (shim/'bin/unknown_64').symlink_to(platform)
        for name in ('data','lib','license'):
            if (oa_home/name).is_dir():
                (shim/name).symlink_to(oa_home/name)
        options = {'oa2strm':'/eda/cds/IC2310/tools/bin/oa2strm',
                   'libdef':root/'runtime/cds.lib',
                   'env':{'OA_HOME':str(shim),'OA_MODE':'opt','OA_BIT':'64'}}
    result = client.oa.export_gds(lib, 'copied_geometry', 'layout', root/(route+'.gds'),
                                layer_map='fixture_map', timeout=120, **options)
    (root/(route+'-map-export.json')).write_text(json.dumps(result, default=str, indent=2))
    assert result['ok'], result
    raw = (root/(route+'.gds')).read_bytes()
    offset, layers = 0, set()
    while offset < len(raw):
        size, kind, _ = struct.unpack('>HBB', raw[offset:offset+4])
        assert size >= 4 and size % 2 == 0
        if kind == 13:
            layers.add(struct.unpack('>h', raw[offset+4:offset+size])[0])
        offset += size
        if kind == 4:
            break
    assert layers == {231}, layers
    (root/(route+'-map-proof.json')).write_text(json.dumps({'layers': sorted(layers), 'expected_layer':231}))


def test_real_calibre_api_reports_design_status_and_pex(setup):
    from virtuoso_fusion import PhysicalVerification, discover_tools
    from virtuoso_fusion.physical import parse_drc
    from virtuoso_bridge.virtuoso.layout import layout_create_param_inst
    client, lib, root = setup
    with client.layout.create(lib, 'api_nch') as layout:
        layout.add(layout_create_param_inst('tsmcN65', 'nch', 'layout', 'M0', 0, 0, 'R0'))
        layout.add('dbReplaceProp(car(cv~>instances) "l" "string" "60n")')
        layout.add('dbReplaceProp(car(cv~>instances) "w" "string" "600n")')
        layout.add('dbReplaceProp(car(cv~>instances) "wf" "string" "600n")')
    exported = client.oa.export_gds(lib, 'api_nch', 'layout', root/'api_nch.gds',
        layer_map=Path('/eda/1P9M/TSMC065_1P9M/tsmcN65/tsmcN65.layermap'), timeout=120)
    assert exported['ok'], exported
    source = root/'api_nch.cdl'
    source.write_text('* Independent expected geometry: 600nm/60nm\n.SUBCKT api_nch\nM0 D G S B nch W=600n L=60n\n.ENDS api_nch\n')
    base = Path('/eda/1P9M/TSMC065_1P9M/Calibre')
    api = PhysicalVerification()
    api.register_parser('drc', lambda p: {**parse_drc(p), 'custom_parser_called': True})
    common = dict(work_dir=root/'physical', layout=root/'api_nch.gds', primary='api_nch', timeout=240)
    drc = api.run('drc', deck=base/'drc/calibre.drc', **common)
    lvs = api.run('lvs', deck=base/'lvs/calibre.lvs', source=source, **common)
    pex = api.run('rcx', deck=base/'rcx/calibre.rcx', source=source, netlist='fusion-extracted.sp', **common)
    report = {'scope':'single floating MOS; not full-chip or post-layout signoff',
              'drc':drc,'lvs':lvs,'pex':pex, 'discovery':discover_tools(decks=[base/'drc/calibre.drc'])}
    (root/'physical-api-results.json').write_text(json.dumps(report, default=str, indent=2))
    assert drc['execution_ok'] and drc['checks_passed'] is False and drc['ok'] is False
    assert drc['parsed']['custom_parser_called'] is True and drc['parsed']['results'] > 0
    assert lvs['execution_ok'] and lvs['checks_passed'] is True and lvs['ok']
    assert pex['execution_ok'] and pex['extraction_complete'] and pex['ok']
    assert pex['checks_passed'] is None and pex['parsed']['capacitors'] > 0
    assert len(pex['phase_results']) == 3 and Path(pex['netlist']).name == 'fusion-extracted.sp'
