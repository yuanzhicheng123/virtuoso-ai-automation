"""Advertised limits apply to the installed JSON-RPC boundary, before handlers."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('name,args,path,constraint', [
    ('read_waveform', {'path':'/does-not-exist.psf','at':['private-input']}, '$.at[0]', 'type'),
    ('read_waveform', {'path':'/does-not-exist.psf','at':[0.0]*201}, '$.at', 'maxItems'),
    ('registry_search', {'query':'plus','limit':201}, '$.limit', 'maximum'),
    ('evaluate', {'expr':'private-input','unexpected':True}, '$', 'additionalProperties'),
    ('read_waveform', {}, '$.path', 'required'),
])
def test_invalid_arguments_fail_at_stdio_entry(name, args, path, constraint):
    message = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}}
    process = subprocess.run([sys.executable,'-m','virtuoso_fusion.mcp'],
        input=json.dumps(message)+'\n', text=True, capture_output=True, timeout=15,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]/'src')))
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)['result']
    assert result['isError']
    text = result['content'][0]['text']
    assert name in text and path in text and constraint in text
    assert 'private-input' not in text and 'does-not-exist' not in text
