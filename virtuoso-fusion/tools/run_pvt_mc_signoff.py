#!/usr/bin/env python3
"""Small real-PDK PVT and Monte Carlo execution checks, not yield signoff."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import tempfile

from virtuoso_fusion import SpectreSimulator
from virtuoso_bridge.spectre.psf import read_psf_ascii, result_file, scalar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--mc-only', action='store_true')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='pvt-mc-', dir=args.root.resolve()))
    model = args.model.resolve()
    report = {'root':str(root), 'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),
              'scope':'one N65 MOS, PVT execution and three statistical samples; no chip masks or yield estimate', 'pvt':[], 'mc':[]}
    base = 'simulator lang=spectre\nglobal 0\ninclude ' + json.dumps(str(model))
    dut = '\nM0 (d g 0 0) nch w=1u l=60n m=1 nf=1\nVG (g 0) vsource dc=0.6\n'
    for corner, voltage, temp in ([] if args.mc_only else itertools.product(('tt','ff','ss'), (1.08,1.2,1.32), (-40,27,125))):
        name = f'{corner}_{voltage}_{temp}'
        deck = base + f' section={corner}\n' + dut + f'VD (d 0) vsource dc={voltage}\nopts options temp={temp} tnom=27\nop1 dc\n'
        entry = run_case(root, name, deck)
        entry.update(corner=corner,voltage=voltage,temp=temp)
        if entry['result']['status']=='success':
            try:
                assert not entry['result']['warnings'], entry['result']['warnings']
                data=read_psf_ascii(result_file(Path(entry['result']['metadata']['output_dir']),'op1.dc'))
                current=-scalar(data,'VD:p')
                assert math.isfinite(current) and current>0
                entry.update(validation='PASS',drain_current=current)
            except Exception as error:entry.update(validation='FAIL',error=str(error))
        report['pvt'].append(entry)
        (root/'report.json').write_text(json.dumps(report,indent=2))
    mc_deck=(base+' section=stat\n'+base.split('global 0\n')[1]+' section=mc\n'+dut+'VD (d 0) vsource dc=1.2\nopts options temp=27 tnom=27\n'
             'mc1 montecarlo numruns=3 seed=20261123 variations=process savefamilyplots=yes {\n op1 dc\n}\n')
    for name in ('mc_seed_a','mc_seed_b'):
        entry=run_case(root,name,mc_deck)
        raw=entry['result']['metadata'].get('output_dir')
        entry['dc_files']=sorted(str(f) for f in Path(raw).rglob('*.dc')) if raw else []
        entry['validation']='FAIL'
        entry['variation_mode']='process_only'
        if entry['result']['status']=='success':
            try:
                assert not entry['result']['warnings'],entry['result']['warnings']
                by_file={Path(f).name:-scalar(read_psf_ascii(Path(f)),'VD:p') for f in entry['dc_files']}
                expected=['mc1-001_op1.dc','mc1-002_op1.dc','mc1-003_op1.dc']
                assert set(by_file)==set(expected+['op1.dc']), by_file
                samples=[by_file[name] for name in expected]
                nominal=by_file['op1.dc']
                assert all(math.isfinite(v) and v>0 for v in samples+[nominal])
                assert max(samples)-min(samples)>1e-9
                entry.update(validation='PASS',sample_currents=samples,nominal_current=nominal,sample_count=3)
            except Exception as error:entry['error']=str(error)
        report['mc'].append(entry)
        (root/'report.json').write_text(json.dumps(report,indent=2))
    a,b=report['mc']
    report['seed_reproducible']=(a.get('validation')=='PASS' and b.get('validation')=='PASS'
                                 and a['sample_currents']==b['sample_currents']
                                 and a['nominal_current']==b['nominal_current'])
    (root/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'root':str(root),'pvt_pass':sum(x.get('validation')=='PASS' for x in report['pvt']),
                      'pvt_count':len(report['pvt']),'mc':[{k:v for k,v in x.items() if k!='result'} for x in report['mc']]},indent=2))
    return 0 if all(x.get('validation')=='PASS' for x in report['pvt']) and all(x['validation']=='PASS' for x in report['mc']) and report['seed_reproducible'] else 1


def run_case(root,name,deck):
    path=root/(name+'.scs');path.write_text(deck)
    result=SpectreSimulator.local(timeout=60,work_dir=root/name,license_queue_timeout=0).run_simulation(path)
    return {'name':name,'deck_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'result':result.model_dump(mode='json')}


if __name__=='__main__':
    raise SystemExit(main())
