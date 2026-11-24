#!/usr/bin/env python3
"""Real N65 analysis probes; a pass validates this fixture, not RF design masks."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile

from virtuoso_fusion import SpectreSimulator
from virtuoso_bridge.spectre.psf import read_psf_ascii, vector, frequency_hz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='rf-', dir=args.root.resolve()))
    model = args.model.resolve()
    header = ('simulator lang=spectre\nglobal 0\ninclude ' + json.dumps(str(model)) + ' section=tt\n'
              'M0 (d g 0 0) nch w=1u l=60n nf=1 m=1\n'
              'simulatorOptions options temp=27 tnom=27 reltol=1e-4\n')
    linear = header + '''P1 (g 0) port r=50 dc=0.6 num=1 mag=1
P2 (d 0) port r=50 dc=1.2 num=2
op1 dc
ac1 ac start=1k stop=100M dec=5
noise1 (d 0) noise start=1k stop=1M dec=5 iprobe=P1
sp1 sp ports=[P1 P2] start=1M stop=100M dec=5 donoise=yes
'''
    driven = header + '''VDD (vdd 0) vsource dc=1.2
RD (vdd d) resistor r=1k
VG (g 0) vsource dc=0.6 type=sine sinedc=0.6 ampl=0.02 freq=1M pacmag=1
op1 dc
'''
    decks = {
        'linear': linear,
        'periodic': driven + '''pss1 pss fund=1M harms=5 tstab=5u errpreset=moderate outputtype=all
pn1 (d 0) pnoise start=1k stop=100k dec=5 maxsideband=7 iprobe=VG refsideband=0
pac1 pac start=1k stop=100k dec=5 maxsideband=3
''',
        'hb': driven + '''hb1 hb fundfreqs=[1M] maxharms=[5] tstab=5u errpreset=moderate
''',
    }
    decks['noise_refinement'] = decks['periodic'].replace('maxsideband=7 iprobe=VG', 'maxsideband=14 iprobe=VG')
    report = {'scope': 'one driven N65 MOS fixture; no oscillator, NF specification, or system jitter signoff',
              'root': str(root), 'model': str(model), 'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(), 'runs': {}}
    for name, deck in decks.items():
        netlist = root / (name + '.scs')
        netlist.write_text(deck)
        result = SpectreSimulator.local(timeout=90, work_dir=root/name, license_queue_timeout=0).run_simulation(netlist)
        entry = {'result': result.model_dump(mode='json'), 'netlist_sha256': hashlib.sha256(netlist.read_bytes()).hexdigest(), 'raw_files': []}
        output = result.metadata.get('output_dir')
        if output and Path(output).is_dir():
            entry['raw_files'] = sorted(str(p.relative_to(Path(output))) for p in Path(output).rglob('*') if p.is_file())
        entry['validation'] = 'FAIL'
        if result.ok:
            try:
                assert not result.warnings, result.warnings
                entry['measurements'] = measure_case(name, Path(output))
                entry['validation'] = 'PASS'
            except Exception as error:
                entry['validation_error'] = str(error)
        report['runs'][name] = entry
        (root/'rf-report.json').write_text(json.dumps(report, indent=2))
    try:
        pss = report['runs']['periodic']['measurements']['drain_fundamental']
        hb = report['runs']['hb']['measurements']['drain_fundamental']
        relative = abs(complex(*pss) - complex(*hb)) / abs(complex(*pss))
        assert relative < 0.01, relative
        pn7 = report['runs']['periodic']['measurements']['noise_out']
        pn14 = report['runs']['noise_refinement']['measurements']['noise_out']
        noise_error = max(abs(a-b) / b for a,b in zip(pn7,pn14))
        assert noise_error < 0.01, noise_error
        report['cross_checks'] = {'status':'PASS','pss_hb_relative_error':relative,'pnoise_7_vs_14_relative_error':noise_error}
    except Exception as error:
        report['cross_checks'] = {'status':'FAIL','error':str(error)}
    (root/'rf-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'root': str(root), 'status': {k:v['result']['status'] for k,v in report['runs'].items()},
                      'validation': {k:v['validation'] for k,v in report['runs'].items()}, 'cross_checks': report['cross_checks']}, indent=2))
    return 0 if all(v['validation']=='PASS' for v in report['runs'].values()) and report['cross_checks']['status']=='PASS' else 1


def measure_case(name, raw):
    if name == 'linear':
        ac = read_psf_ascii(raw/'ac1.ac')
        sp = read_psf_ascii(raw/'sp1.sp')
        noise = read_psf_ascii(raw/'noise1.noise')
        af, sf, nf = frequency_hz(ac), frequency_hz(sp), frequency_hz(noise)
        assert len(af) == 26 and len(sf) == 11 and len(nf) == 16
        response = vector(ac, 'd')
        scattering = {key:vector(sp,key) for key in ('s11','s12','s21','s22')}
        output_noise = vector(noise,'out')
        assert all(v.real > 0 and v.imag == 0 for v in output_noise)
        return {'ac_points':len(af),'sp_points':len(sf),'noise_points':len(nf),
                'first_ac_d':[response[0].real,response[0].imag],
                'first_s':{k:[v[0].real,v[0].imag] for k,v in scattering.items()},
                'noise_out':[v.real for v in output_noise]}
    fdname = 'hb1.fd.pss_hb' if name == 'hb' else 'pss1.fd.pss'
    fd = read_psf_ascii(raw/fdname)
    freq = frequency_hz(fd)
    assert freq[1] == 1e6 and len(freq) == 6
    drain = vector(fd,'d')
    assert abs(drain[1]) > 1e-6
    output = {'harmonics':len(freq),'drain_fundamental':[drain[1].real,drain[1].imag]}
    tdname = 'hb1.td.pss_hb.tran' if name == 'hb' else 'pss1.td.pss'
    td = read_psf_ascii(raw/tdname)
    time = vector(td,'time')
    assert all(b.real > a.real for a,b in zip(time,time[1:]))
    if name != 'hb':
        gate = [v.real for v in vector(td,'g')]
        assert abs(max(gate)-0.62) < 2e-4 and abs(min(gate)-0.58) < 2e-4
        pn = read_psf_ascii(raw/'pn1.pnoise')
        assert len(frequency_hz(pn)) == 11
        output['noise_out'] = [v.real for v in vector(pn,'out')]
        assert all(v > 0 for v in output['noise_out'])
        pac = read_psf_ascii(raw/'pac1.0.pac')
        assert len(frequency_hz(pac)) == 11
        assert abs(vector(pac,'d')[0]) > 1e-3
        output['pac_points'] = 11
    return output


if __name__ == '__main__':
    raise SystemExit(main())
