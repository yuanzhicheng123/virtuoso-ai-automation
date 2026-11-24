"""Opt-in four-port OA → GDS → Calibre RC → Spectre acceptance fixture.

This intentionally long-wire fixture exercises parasitics, not chip DRC signoff.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('VF_LIVE_CONNECTED'), reason='explicit connected circuit acceptance required')

CRITERIA = {
    'revision': 4,
    'revision_reason': 'The voltage-clamped drain current includes capacitive feedthrough and is not a low-pass transfer; revision 1 remains FAIL. Revision 2 uses a 1kohm drain load and measures output voltage.',
    'scope': 'one four-port N65 MOS with 100um gate/drain M1 wires; not a chip signoff',
    'lvs': 'CORRECT, four named ports D G S B, one N65 nch with L=60nm W=600nm',
    'pex': 'complete three-phase extraction, positive nonzero resistors and capacitors',
    'dc': 'both drain currents 0 < I < 10mA; relative pre/post difference < 20%',
    'ac': '1kohm drain load; 1kHz voltage magnitude pre/post difference < 20%; 100GHz post/pre voltage magnitude < 0.99',
    'reference': 'Calibre VSS is explicitly global and driven to zero; PSF VSS must equal zero within 1pV',
    'warnings': 'Both native-options decks allow one SPECTRE-17101 checklimitdest=psf deprecation; post additionally allows four SFE-2453 VSS global/formal collisions; no other coded warnings; all retained',
    'drc': 'all original foundry checks run and violations reported, never waived for this fixture',
}


def test_connected_fixture():
    from virtuoso_fusion import FusionClient, PhysicalVerification
    from virtuoso_bridge.virtuoso.layout import layout_create_param_inst, layout_create_rect
    root = Path(os.environ['VF_LIVE_ROOT'])
    (root/'acceptance-criteria.json').write_text(json.dumps(CRITERIA, indent=2))
    (root/'test-source.py').write_text(Path(__file__).read_text())
    with FusionClient(port=int(os.environ['VF_LIVE_PORT']), timeout=60, log_to_ciw=False,
                      daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip(),
                      evidence_dir=root/'calls') as client:
        assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
        lib = 'vf_conn_' + root.name.replace('-', '_')
        client.library.create(lib, str(root/lib), technology_library='tsmcN65')
        create_native_schematic(client, lib, root)
        with client.layout.create(lib, 'connected_nch') as layout:
            layout.add(layout_create_param_inst('tsmcN65', 'nch', 'layout', 'M0', 0, 0, 'R0'))
            layout.add('dbReplaceProp(car(cv~>instances) "l" "string" "60n")')
            layout.add('dbReplaceProp(car(cv~>instances) "w" "string" "600n")')
            layout.add('dbReplaceProp(car(cv~>instances) "wf" "string" "600n")')
            layout.add(layout_create_param_inst('tsmcN65', 'M1_PO', 'via', 'GCONTACT', 0.03, 1.2, 'R0'))
            layout.add(layout_create_param_inst('tsmcN65', 'M1_PODc', 'layout', 'BCONTACT', -3, -2, 'R0'))
            # Coordinates are derived from the saved N65 master readback (probe2/probe4).
            for layer, box in [('PO', (0, .6, .06, 1.2)),
                               ('M1', (.115, .24, 100.16, .36)),
                               ('M1', (-10.1, .24, -.055, .36)),
                               ('M1', (-.03, 1.115, .09, 101.2))]:
                layout.add(layout_create_rect(layer, 'drawing', *box))
            for name, x, y in [('D',100.1,.3), ('G',.03,101.14), ('S',-10.04,.3), ('B',-3,-2)]:
                layout.add(f'''let((net term pin) net=dbCreateNet(cv "{name}") term=dbCreateTerm(net "{name}" "inputOutput")
pin=dbCreateRect(cv list("M1" "pin") list(list({x-.04} {y-.04}) list({x+.04} {y+.04})))
dbCreatePin(net pin "{name}") dbCreateLabel(cv list("M1" "pin") list({x} {y}) "{name}" "centerCenter" "R0" "roman" 0.08))''')
        code = f'''let((cv master port) cv=dbOpenCellViewByType("{lib}" "connected_nch" "layout" "" "r") port=outfile("{root}/oa-readback.txt")
fprintf(port "TOP_TERMINALS %L\\n" cv~>terminals~>name)
foreach(inst cv~>instances master=inst~>master fprintf(port "CELL %L ORIGIN %L\\n" master~>cellName inst~>xy)
fprintf(port "MASTER %L\\n" master~>bBox)
foreach(term master~>terminals foreach(pin term~>pins foreach(fig pin~>figs fprintf(port "PIN %L %L %L\\n" term~>name fig~>lpp fig~>bBox))))
foreach(shape master~>shapes fprintf(port "SHAPE %L %L %L\\n" shape~>objType shape~>lpp shape~>bBox))
foreach(p inst~>prop fprintf(port "PROP %L %L\\n" p~>name p~>value)))
close(port) dbClose(cv) t)'''
        result = client.execute_skill(code)
        assert result.ok, result
        readback = (root/'oa-readback.txt').read_text()
        assert 'TOP_TERMINALS ("D" "G" "S" "B")' in readback
        assert all(f'CELL "{c}"' in readback for c in ['nch','M1_PO','M1_PODc'])
        exported = client.oa.export_gds(lib, 'connected_nch', 'layout', root/'connected_nch.gds',
            layer_map=Path('/eda/1P9M/TSMC065_1P9M/tsmcN65/tsmcN65.layermap'), timeout=120)
        (root/'gds-export.json').write_text(json.dumps(exported, indent=2, default=str))
        assert exported['ok'], exported
    source = root/'expected.cdl'
    source.write_text('* Independent intent: one 600nm/60nm MOS, four explicit ports\n.SUBCKT connected_nch D G S B\nM0 D G S B nch W=600n L=60n\n.ENDS connected_nch\n')
    base = Path('/eda/1P9M/TSMC065_1P9M/Calibre')
    api = PhysicalVerification()
    common = dict(work_dir=root/'physical', layout=root/'connected_nch.gds', primary='connected_nch', timeout=300)
    report = {'criteria': CRITERIA, 'root': str(root), 'library':lib}
    for kind, deck in [('drc','drc/calibre.drc'),('lvs','lvs/calibre.lvs'),('pex','rcx/calibre.rcx')]:
        options = {} if kind=='drc' else {'source':source}
        report[kind] = api.run(kind, deck=base/deck, **common, **options)
        (root/'connected-report.json').write_text(json.dumps(report, indent=2, default=str))
    assert report['drc']['execution_ok']
    assert report['lvs']['ok'], report['lvs']
    assert report['pex']['ok'], report['pex']
    assert report['pex']['parsed']['resistors'] > 0, report['pex']['parsed']
    assert report['pex']['parsed']['capacitors'] > 0, report['pex']['parsed']
    report['rc_values'] = inspect_rc_values(report['pex']['parsed']['files'])
    netlist = Path(report['pex']['netlist'])
    main = netlist.read_text()
    header = re.search(r'^\.subckt\s+connected_nch\s+([^\n]+)', main, re.M|re.I)
    assert header and set(header.group(1).split())=={'D','G','S','B'}, main
    pxi = netlist.with_name(netlist.name+'.CONNECTED_NCH.pxi').read_text().replace('\n+',' ')
    reference_instances = [line for line in pxi.splitlines() if line.lower().startswith('x_pm_connected_nch')]
    assert len(reference_instances) == 4
    assert all(line.split()[-2]=='VSS' for line in reference_instances)
    report['reference_connections'] = reference_instances
    report['postlayout'] = compare_simulations(root, netlist, header.group(1).split())
    report['hashes'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [root/'native-netlist/input.scs', root/'test-source.py',root/'acceptance-criteria.json',root/'oa-readback.txt',source,root/'connected_nch.gds',netlist]}
    (root/'connected-report.json').write_text(json.dumps(report, indent=2, default=str))
    assert report['postlayout']['passed'], report['postlayout']


def compare_simulations(root, netlist, ports):
    from virtuoso_fusion import SpectreSimulator
    from virtuoso_bridge.spectre.psf import read_psf_ascii, vector, frequency_hz
    model = Path('/eda/1P9M/TSMC065_1P9M/models/spectre/crn65gplus_2d5_lk_v1d0.scs')
    native = root/'native-netlist/input.scs'
    native_content = native.read_text()
    model_lines = [line for line in native_content.splitlines() if line.startswith('include ')]
    assert model_lines
    options = re.search(r'^simulatorOptions options.*?(?=^modelParameter)', native_content, re.M|re.S).group(0)
    header = 'simulator lang=spectre\nglobal 0 VSS\n'+'\n'.join(model_lines)+'\n'+options
    bench = '''VVSS (VSS 0) vsource dc=0
BREF (vss_probe 0) bsource v=V(VSS)+1
VD (vdd 0) vsource dc=1.2
RD (vdd D) resistor r=1k
VG (G 0) vsource dc=0.6 mag=1
VS (S 0) vsource dc=0
VB (B 0) vsource dc=0
save VD:p VG:p D VSS vss_probe
op1 dc
ac1 ac start=1k stop=100G dec=5
'''
    decks = {'pre': native_content+'\nglobal VSS\n'+bench,
             'post': header+'simulator lang=spice\n.include "'+str(netlist)+'"\nsimulator lang=spectre\nXD ('+' '.join(ports)+') CONNECTED_NCH\n'+bench}
    output = {'model_sections': model_lines, 'native_netlist_sha256':hashlib.sha256(native.read_bytes()).hexdigest(), 'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest(), 'runs':{}}
    for name, deck in decks.items():
        path = root/(name+'.scs')
        path.write_text(deck)
        result = SpectreSimulator.local(timeout=120,work_dir=root/name,license_queue_timeout=0).run_simulation(path)
        entry = {'result':result.model_dump(mode='json'), 'deck_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        output['runs'][name] = entry
        (root/'simulation-report.json').write_text(json.dumps(output, indent=2))
        assert result.ok, result
        warning_codes = re.findall(r'WARNING \(([^)]+)\)', '\n'.join(result.warnings))
        if name == 'pre':
            assert warning_codes == ['SPECTRE-17101'], result.warnings
            entry['warning_scope'] = 'Unmodified native simulatorOptions checklimitdest=psf emits a future-deprecation warning.'
        else:
            assert warning_codes == ['SFE-2453']*4+['SPECTRE-17101'], result.warnings
            entry['warning_scope'] = 'Four VSS global/formal-terminal name collisions; Spectre explicitly retains each formal terminal.'
        raw = Path(result.metadata['output_dir'])
        dc = read_psf_ascii(raw/'op1.dc')
        ac = read_psf_ascii(raw/'ac1.ac')
        reference_voltage = float(dc['vss_probe']) - 1.0
        assert abs(reference_voltage) < 1e-12, dc
        entry['vss_reference_voltage'] = reference_voltage
        current = -float(dc['VD:p'])
        response = vector(ac,'D')
        entry.update(current=current, frequencies=frequency_hz(ac), response=[[v.real,v.imag] for v in response])
    pre, post = output['runs']['pre'], output['runs']['post']
    assert pre['frequencies'] == post['frequencies']
    assert pre['frequencies'][0] == 1000.0 and pre['frequencies'][-1] == 1e11
    assert len(pre['frequencies']) == 41
    dc_difference = abs(pre['current']-post['current'])/pre['current']
    low_difference = abs(abs(complex(*pre['response'][0]))-abs(complex(*post['response'][0])))/abs(complex(*pre['response'][0]))
    high_ratio = abs(complex(*post['response'][-1]))/abs(complex(*pre['response'][-1]))
    output.update(dc_relative_difference=dc_difference, low_frequency_relative_difference=low_difference,
                  high_frequency_post_pre_ratio=high_ratio,
                  passed=all(0<r['current']<.01 for r in (pre,post)) and dc_difference<.2 and low_difference<.2 and high_ratio<.99)
    (root/'simulation-report.json').write_text(json.dumps(output, indent=2))
    return output


def inspect_rc_values(files):
    """Check real extracted element values; component counts alone are insufficient."""
    values = {'r':[], 'c':[]}
    multipliers = {'':1.0, 'f':1e-15, 'p':1e-12, 'n':1e-9, 'u':1e-6, 'm':1e-3, 'k':1e3, 'meg':1e6}
    for filename in files:
        for line in Path(filename).read_text().splitlines():
            fields = line.split()
            if fields and fields[0][0].lower() in values:
                assert len(fields) >= 4, line
                match = re.fullmatch(r'([+\-]?[\d.]+(?:[eE][+\-]?\d+)?)([a-zA-Z]*)', fields[3])
                assert match, line
                value = float(match[1])*multipliers[match[2].lower()]
                assert value > 0, line
                values[fields[0][0].lower()].append(value)
    assert values['r'] and values['c']
    return {kind: {'count':len(data), 'min':min(data), 'max':max(data), 'sum':sum(data)}
            for kind,data in values.items()}


def create_native_schematic(client, lib, root):
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_create_pin as pin,
        schematic_label_instance_term as label,
    )
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params
    with client.schematic.create(lib, 'connected_nch') as sch:
        sch.add(inst('tsmcN65','nch','symbol','M0',0,0,'R0'))
        for index, name in enumerate(('D','G','S','B')):
            sch.add(label('M0',name,name))
            sch.add(pin(name,2.5,index*.5,'R0',direction='inputOutput'))
    assert client.open_window(lib, 'connected_nch').ok
    params = set_instance_params(client,'M0',l='60n',w='600n',param_filters=None)
    (root/'schematic-cdf.json').write_text(json.dumps(params,indent=2))
    check = client.schematic.check_and_save(lib,'connected_nch')
    assert check.status == 'saved' and check.check_error_count == 0, check
    readback = client.schematic.read(lib,'connected_nch',include_param_evidence=True)
    (root/'schematic-readback.json').write_text(json.dumps(readback,indent=2,default=str))
    exported = client.schematic.export_netlist(lib,'connected_nch',root/'native-netlist',run_dir=root/'native-export-run',timeout=120)
    (root/'native-export.json').write_text(json.dumps(exported,indent=2,default=str))
    content = (root/'native-netlist/input.scs').read_text()
    flat = content.replace('\\\n',' ')
    match = re.search(r'^M0 \(D G S B\) nch ([^\n]+)',flat,re.M)
    assert match, content
    assert re.search(r'\bl=60n\b',match[1]), content
    assert re.search(r'\bw=600n\b',match[1]), content
