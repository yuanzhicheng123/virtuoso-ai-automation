"""Exercise the real PSF parser through the public MCP stdio entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def call_waveform(path, **arguments):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': 'read_waveform', 'arguments': {'path': str(path), **arguments}}}
    process = subprocess.run(
        [sys.executable, '-m', 'virtuoso_fusion.mcp'], input=json.dumps(request)+'\n',
        text=True, capture_output=True, timeout=20,
        env=dict(os.environ, VB_SAFE_ROOTS=str(path.parent),
                 PYTHONPATH=str(Path(__file__).parents[1]/'src')))
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)['result']


def psf(axis='time'):
    return f'''HEADER
"PSFversion" "1.00"
TYPE
"sweep" FLOAT DOUBLE
"V" FLOAT DOUBLE
SWEEP
"{axis}" "sweep"
TRACE
"out" "V"
VALUE
"{axis}" 0
"out" 1
"{axis}" 2
"out" 3
END
'''


@pytest.mark.parametrize('contents', ['', 'this is not PSF'])
def test_invalid_psf_retains_parser_failure(tmp_path, contents):
    path = tmp_path/'invalid.psf'
    path.write_text(contents)
    result = call_waveform(path)
    assert result.get('isError') is True
    assert result['structuredContent']['status'] in ('error', 'failure')
    if not contents:
        assert 'empty' in ' '.join(result['structuredContent']['errors'])


def test_empty_directory_is_not_success(tmp_path):
    result = call_waveform(tmp_path)
    assert result.get('isError') is True


def test_at_requires_time_axis(tmp_path):
    path = tmp_path/'ac.psf'
    path.write_text(psf('freq'))
    result = call_waveform(path, signals=['out'], at=[0.2, 1.8])
    assert result.get('isError') is True
    assert 'time' in result['content'][0]['text'].lower()
    assert 'samples' not in result.get('structuredContent', {})


def test_transient_nearest_samples_and_stats(tmp_path):
    path = tmp_path/'tran.tran'
    path.write_text(psf())
    result = call_waveform(path, signals=['out'], at=[0.2, 1.8])
    assert not result.get('isError')
    data = result['structuredContent']
    assert data['samples'] == [{'t': 0.0, 'out': 1.0}, {'t': 2.0, 'out': 3.0}]
    assert data['stats']['out']['min'] == 1
    assert data['stats']['out']['max'] == 3


def test_frequency_data_without_time_sampling_still_readable(tmp_path):
    path = tmp_path/'ac.psf'
    path.write_text(psf('freq'))
    result = call_waveform(path, signals=['out'])
    assert not result.get('isError')
    assert result['structuredContent']['stats']['out']['n'] == 2


def test_transient_directory_and_missing_signal(tmp_path):
    path = tmp_path/'tran.tran'
    path.write_text(psf())
    result = call_waveform(tmp_path, signals=['out'], at=[1.8])
    assert not result.get('isError')
    assert result['structuredContent']['samples'] == [{'t': 2.0, 'out': 3.0}]
    missing = call_waveform(path, signals=['absent'])
    assert missing.get('isError') is True
