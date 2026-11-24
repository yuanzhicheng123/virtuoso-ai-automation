"""Additional public-domain checks on a disposable installed-package session."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('VF_LIVE_WORKBENCH'), reason='explicit whole-workbench acceptance required')


@pytest.fixture(scope='module')
def workbench():
    from virtuoso_fusion import FusionClient
    root = Path(os.environ['VF_LIVE_ROOT'])
    with FusionClient(port=int(os.environ['VF_LIVE_PORT']), timeout=60, log_to_ciw=False,
                      daemon_token=Path(os.environ['VF_LIVE_TOKEN_FILE']).read_text().strip(),
                      evidence_dir=root/'calls') as client:
        assert client.handshake_virtuoso_pid() == int(os.environ['VF_LIVE_PID'])
        lib = 'vf_workbench_' + root.name.replace('-', '_')
        client.library.create(lib, str(root/lib), technology_library='tsmcN65')
        yield client, lib, root


def test_symbol_ports_labels_and_order(workbench):
    from virtuoso_bridge.virtuoso.symbol import (
        symbol_create_rect, symbol_create_pin, symbol_create_instance_label,
        symbol_create_logical_label, symbol_create_selection_box, symbol_set_term_order)
    client, lib, root = workbench
    with client.symbol.create(lib, 'two_ports') as symbol:
        symbol.add(symbol_create_rect('device','drawing',-.5,-.5,.5,.5))
        symbol.add(symbol_create_pin('IN',-1,0,direction='input',label_x=-.45,label_y=0))
        symbol.add(symbol_create_pin('OUT',1,0,direction='output',label_x=.45,label_y=0))
        symbol.add(symbol_create_instance_label(0,.75))
        symbol.add(symbol_create_logical_label(0,-.75))
        symbol.add(symbol_create_selection_box(-1,-.5,1,.5))
        symbol.add(symbol_set_term_order(['IN','OUT']))
    data = client.symbol.read_ports(lib, 'two_ports')
    (root/'symbol-roundtrip.json').write_text(json.dumps(data,indent=2))
    assert data['termOrder'] == ['IN','OUT']
    assert {x['name'] for x in data['terms']} == {'IN','OUT'}
    labels = {x['text']:x for x in data['labels']}
    assert labels['IN']['layerName'] == 'pin' and labels['IN']['labelType'] == 'normalLabel'
    assert labels['[@instanceName]']['labelType'] == 'NLPLabel'
    assert data['selectionBoxes'] == [[[-1.,-.5],[1.,.5]]]


def test_library_technology_categories_and_scratch_rename(workbench):
    client, lib, root = workbench
    assert client.library.get_technology_library(lib) == 'tsmcN65'
    # Only this test-created empty library is renamed and deleted.
    scratch = lib+'_scratch'
    client.library.create(scratch,str(root/scratch),technology_library='tsmcN65')
    renamed = client.library.rename(scratch,scratch+'_renamed')
    assert renamed.name == scratch+'_renamed' and renamed.technology_library == 'tsmcN65'
    client.library.delete(renamed.name)
    assert scratch not in client.library.list() and renamed.name not in client.library.list()
    with client.schematic.create(lib,'category_member') as editor:
        editor.add('t')
    client.library.create_category(lib,'cat_a')
    client.library.add_cell_to_category(lib,'cat_a','category_member')
    assert client.library.list_category_cells(lib,'cat_a') == ['category_member']
    client.library.rename_category(lib,'cat_a','cat_b')
    assert 'cat_b' in client.library.list_categories(lib)
    client.library.remove_cell_from_category(lib,'cat_b','category_member')
    assert not client.library.list_category_cells(lib,'cat_b')
    client.library.delete_category(lib,'cat_b')
    assert 'cat_b' not in client.library.list_categories(lib)
    (root/'library-acceptance.json').write_text(json.dumps({'technology':'tsmcN65','scratch_rename_delete':True,'category_roundtrip':True}))


def test_file_transfer_and_mcp_real_call(workbench):
    from virtuoso_fusion import mcp, FusionClient
    client, lib, root = workbench
    payload = bytes(range(256))*800
    source, remote, returned = root/'upload.bin',root/'transferred.bin',root/'download.bin'
    source.write_bytes(payload)
    assert client.upload_file(source,remote).ok
    assert client.download_file(remote,returned).ok
    assert returned.read_bytes() == payload
    mcp.server._CLIENT['obj'] = client
    try:
        answer = mcp.server.dispatch_tool('call_registered',{'name':'plus','args':[1,2,3]}, {})
        assert not answer.get('isError') and answer['structuredContent']['output'].strip() == '6'
        failure = mcp.server.dispatch_tool('call_registered',{'name':'quotient','args':[1,0]}, {})
        assert failure['isError'] and failure['structuredContent']['errors']
    finally:
        mcp.server._CLIENT.pop('obj',None)
    with FusionClient(port=client.port,daemon_token='ab'*32,timeout=5,log_to_ciw=False) as stranger:
        rejected = stranger.execute_skill('1+1')
        assert not rejected.ok
    (root/'transfer-mcp.json').write_text(json.dumps({'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'mcp':answer,'failure_preserved':failure,'wrong_token_rejected':True},indent=2))


def test_dialog_inspection_and_recoverable_receipt(workbench):
    client, lib, root = workbench
    inspection = client.dialogs.inspect(pid=client.get_virtuoso_pid())
    assert inspection.status == 'clear', inspection
    client.dialogs.enable_guard(pid=client.get_virtuoso_pid(),local_gui=True,protect_inflight=True,timeout=15)
    try:
        assert client.execute_skill('vfReceiptCount=0').ok
        pending = client.execute_skill('progn(sh("sleep 5") vfReceiptCount=vfReceiptCount+1)',timeout=2)
        assert pending.metadata.get('outcome') == 'unknown', pending
        handle = pending.metadata['request_handle']
        deadline = time.monotonic()+30
        while True:
            receipt = client.requests.receipt(handle,timeout=10)
            if receipt.metadata.get('request_state') == 'completed' or time.monotonic() >= deadline:
                break
            time.sleep(.2)
        assert receipt.ok and receipt.output.strip() == '1', receipt
        assert client.execute_skill('vfReceiptCount').output.strip() == '1'
        (root/'request-recovery.json').write_text(json.dumps({'inspection':inspection.model_dump(),'pending':pending.model_dump(),'receipt':receipt.model_dump(),'execution_count':1},indent=2))
    finally:
        client.dialogs.disable_guard()


def test_spectre_pool_two_actual_jobs(workbench):
    from virtuoso_fusion import SpectreSimulator, SpectrePool
    from virtuoso_bridge.spectre.psf import read_psf_ascii, result_file, scalar
    client, lib, root = workbench
    decks = []
    for value in [1,2]:
        path = root/f'pool-{value}.scs'
        path.write_text(f'simulator lang=spectre\nglobal 0\nV0 (v 0) vsource dc={value}\nR0 (v 0) resistor r=1k\nop1 dc\n')
        decks.append(path)
    simulator = SpectreSimulator.local(work_dir=root/'pool-results',timeout=90,license_queue_timeout=0)
    with SpectrePool(simulator,max_workers=2) as pool:
        results = pool.wait_all([pool.submit(p) for p in decks])
    measured = []
    for result in results:
        assert result.ok, result
        raw = Path(result.metadata['output_dir'])
        measured.append(scalar(read_psf_ascii(result_file(raw,'op1.dc')),'v'))
    assert measured == [1.,2.], measured
    assert len({x.metadata['output_dir'] for x in results}) == 2
    (root/'pool-results.json').write_text(json.dumps({'voltages':measured,'results':[x.model_dump(mode='json') for x in results]},indent=2))
