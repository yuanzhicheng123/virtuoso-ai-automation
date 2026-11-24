"""Read all registry function bindings; never invoke the inventoried functions."""
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('VF_RUNTIME_INVENTORY'), reason='explicit read-only inventory run required')


def test_runtime_binding_inventory():
    from virtuoso_fusion import ApiRegistry, FusionClient

    root = Path(os.environ['VF_LIVE_ROOT'])
    (root/'inventory-test-source.py').write_bytes(Path(__file__).read_bytes())
    registry = ApiRegistry()
    names = sorted(registry.raw)
    observations = {}
    started = time.time()
    with FusionClient(port=int(os.environ['VF_LIVE_PORT']), timeout=30, log_to_ciw=False,
                      daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip(),
                      evidence_dir=root / 'calls') as client:
        version = client.execute_skill('getVersion(t)', retry_connect=False)
        assert version.ok
        observations = collect_bindings(client, names, root, 'base')
        contexts = {'base': observations}
        if os.environ.get('VF_INVENTORY_VIEWS'):
            license_setup = client.execute_skill(
                'foreach(k list("VSEL_UseNextLicense" "VLSL_UseNextLicense" "VLSXL_UseNextLicense" "ADE_UseNextLicense" "Explorer_UseNextLicense") envSetVal("license" k \'string "always"))', retry_connect=False)
            assert license_setup.ok
            lib = 'vf_inventory_' + root.name.replace('-', '_')
            client.library.create(lib, str(root/lib), technology_library='tsmcN65')
            for label, view, kind in [('schematic','schematic','schematic'),('layout','layout','maskLayout')]:
                opened = client.execute_skill(f'deOpenCellView("{lib}" "context" "{view}" "{kind}" nil "w")', retry_connect=False)
                assert opened.ok and not opened.is_nil, opened
                contexts[label] = collect_bindings(client, names, root, label)
            configured = client.execute_skill(
                f'envSetVal("asimenv.startup" "projectDir" \'string "{root}/simulation")', retry_connect=False)
            assert configured.ok
            with client.schematic.create(lib, 'maestro_context') as editor:
                editor.add('t')
            session = client.maestro.open_session(lib, 'maestro_context')
            client.maestro.create_test('probe', lib=lib, cell='maestro_context', session=session)
            client.maestro.save_setup(lib, 'maestro_context', session=session)
            client.maestro.close_session(session)
            session = client.maestro.open_gui_session(lib, 'maestro_context', timeout=60)
            assert session
            contexts['maestro'] = collect_bindings(client, names, root, 'maestro')
    assert len(observations) == len(names)
    summary = {
        'scope': 'binding inventory only; getd is not execution or license/argument verification',
        'version': version.output, 'count': len(names), 'seconds': round(time.time()-started, 3),
        'registry_sha256': hashlib.sha256(registry.path.read_bytes()).hexdigest(),
        'counts': {status: list(observations.values()).count(status) for status in ('BOUND','UNBOUND','QUERY_ERROR')},
        'observations': observations,
        'native': {label:json.loads((root/('native-'+label+'.json')).read_text()) for label in contexts},
        'contexts': {label: {'counts':{status:list(values.values()).count(status) for status in ('BOUND','UNBOUND','QUERY_ERROR')}, 'observations':values} for label,values in contexts.items()},
    }
    (root / 'runtime-inventory.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))


def collect_bindings(client, names, root, label):
    observations = {}
    for start in range(0, len(names), 1000):
        selected = names[start:start + 1000]
        path = root / f'{label}-bindings-{start:05d}.tsv'
        literals = ' '.join(json.dumps(name, ensure_ascii=False) for name in selected)
        code = f'''let((p answer)
 p=outfile({json.dumps(str(path))} "w")
 foreach(name list({literals})
   answer=errset(getd(concat(name)) nil)
   fprintf(p "%s\\t%s\\n" name if(answer then if(car(answer) then "BOUND" else "UNBOUND") else "QUERY_ERROR"))
 )
 close(p)
 t)'''
        result = client.execute_skill(code, timeout=30, retry_connect=False)
        assert result.ok, result.errors
        rows = dict(line.split('\t') for line in path.read_text().splitlines())
        assert set(rows) == set(selected)
        observations.update(rows)
    assert len(observations) == len(names)
    assert 'QUERY_ERROR' not in observations.values()
    from virtuoso_bridge.virtuoso.skill_output import parse_sexpr
    native_path = root / ('native-' + label + '.txt')
    native_code = f'''let((p count answer)
 p=outfile({json.dumps(str(native_path))} "w") count=0
 foreach(symbol oblist
   answer=errset(getd(symbol) nil)
   when(answer && car(answer) fprintf(p "%L\\n" symbolToString(symbol)) count=count+1)
 )
 close(p) count)'''
    native = client.execute_skill(native_code, timeout=30, retry_connect=False)
    assert native.ok, native.errors
    native_names = [parse_sexpr(line) for line in native_path.read_text().splitlines()]
    assert len(native_names) == int(native.output.strip())
    assert len(set(native_names)) == len(native_names)
    (root/('native-'+label+'.json')).write_text(json.dumps({'count':len(native_names),'names':sorted(native_names)},ensure_ascii=False,indent=2))
    (root/('context-'+label+'.json')).write_text(json.dumps({'counts':{status:list(observations.values()).count(status) for status in ('BOUND','UNBOUND','QUERY_ERROR')},'observations':observations},indent=2))
    return observations
