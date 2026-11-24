"""Private rendezvous for the SSH endurance controller's owned run_live session."""
import importlib.metadata
import json
import os
from pathlib import Path
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('VF_ENDURANCE_ROOT'), reason='requires the owned SSH endurance controller')


def rss_kib(pid):
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    return None


def test_hold_owned_session():
    rendezvous = Path(os.environ['VF_ENDURANCE_ROOT'])
    run = Path(os.environ['VF_LIVE_ROOT'])
    identity = next((run/'private/runs').glob('*.json'))
    record = json.loads(identity.read_text())
    assert record['pid'] == int(os.environ['VF_LIVE_PID'])
    assert record['port'] == int(os.environ['VF_LIVE_PORT'])
    sockets = [row.split() for row in Path('/proc/net/tcp').read_text().splitlines()[1:]]
    listeners = [row[1] for row in sockets if row[3]=='0A' and int(row[1].split(':')[1],16)==record['port']]
    assert listeners == [f'0100007F:{record["port"]:04X}'], listeners
    ready = {'listen_ipv4':'127.0.0.1', 'run': str(run), 'port':record['port'], 'pid':record['pid'],
             'daemon_pid':record['daemon_pid'], 'token_file':os.environ['VF_LIVE_TOKEN_FILE'],
             'package_version':importlib.metadata.version('virtuoso-fusion')}
    ready_path = rendezvous/'ready.json'
    ready_path.write_text(json.dumps(ready))
    ready_path.chmod(0o600)
    started = time.monotonic()
    deadline = started + float(os.environ['VF_ENDURANCE_MAX_SECONDS'])
    samples = []
    while time.monotonic() < deadline:
        samples.append({'elapsed_s':time.monotonic()-started,
                        'virtuoso_rss_kib':rss_kib(record['pid']),
                        'daemon_rss_kib':rss_kib(record['daemon_pid'])})
        if (rendezvous/'stop.json').exists():
            break
        time.sleep(min(2.0, max(0.0, deadline-time.monotonic())))
    (rendezvous/'remote-rss.json').write_text(json.dumps(samples, indent=2))
    assert (rendezvous/'stop.json').exists(), 'controller did not finish before bounded session deadline'
