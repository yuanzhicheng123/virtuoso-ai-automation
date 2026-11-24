#!/usr/bin/env python3
"""Measure a real SSH-forwarded, authenticated, independently owned Cadence session."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time


def main():
    args = arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='ssh-endurance-', dir=args.output.resolve()))
    root = Path(__file__).resolve().parents[1]
    remote = json.loads(remote_python(args, 'import json,tempfile; from pathlib import Path; '
        f'p=Path({args.remote_root!r}); p.mkdir(parents=True,exist_ok=True); '
        'print(json.dumps(tempfile.mkdtemp(prefix="ssh-endurance-",dir=p)))'))
    report = {'status':'FAIL', 'requested_duration_s':args.duration, 'interval_s':args.interval,
              'window_s':args.window, 'host':args.host, 'remote_root':remote, 'output':str(output),
              'transport':'OpenSSH local 127.0.0.1 forwarding to remote 127.0.0.1; no direct remote TCP',
              'rss_measurement_note':'local RSS includes controller timing samples; three warmup generic calls precede timed RSS baseline',
              'scope':'read-only repeated plus/getVersion; not concurrent writers, OA workloads or chip signoff',
              'local_package_version':importlib.metadata.version('virtuoso-fusion'),
              'local_python':sys.executable, 'remote_python':args.remote_python}
    runner = tunnel = None
    try:
        for source in [root/'tools/run_live.py', root/'tests/live/test_remote_endurance_hold.py']:
            subprocess.run(['scp','-q',str(source),f'{args.host}:{remote}/{source.name}'], check=True, timeout=30)
        command = ['env',f'VF_ENDURANCE_ROOT={remote}',
                   f'VF_ENDURANCE_MAX_SECONDS={args.duration+180}', args.remote_python,
                   f'{remote}/run_live.py','--root',f'{remote}/sessions','--cds-lib',args.cds_lib,
                   '--virtuoso',args.virtuoso,'--tests',f'{remote}/test_remote_endurance_hold.py',
                   '--test-timeout',str(math.ceil(args.duration+210))]
        with (output/'runner.log').open('w') as log:
            runner = subprocess.Popen(ssh_command(args.host, shlex.join(command)), stdout=log,
                                      stderr=subprocess.STDOUT, start_new_session=True)
        ready = wait_ready(args, remote, runner)
        report['remote_identity'] = {k:v for k,v in ready.items() if k!='token_file'}
        secret = remote_python(args, f'from pathlib import Path; print(Path({ready["token_file"]!r}).read_text().strip())').strip()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port = listener.getsockname()[1]
        tunnel_command = ['ssh','-N','-T','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
                          '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
                          '-L',f'127.0.0.1:{port}:127.0.0.1:{ready["port"]}',args.host]
        with (output/'tunnel.log').open('w') as log:
            tunnel = subprocess.Popen(tunnel_command, stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        report['tunnel'] = {'pid':tunnel.pid,'command':tunnel_command,'local_port':port}
        wait_forward(tunnel, port)
        from virtuoso_fusion import FusionClient
        with FusionClient(host='127.0.0.1', port=port, daemon_token=secret, timeout=args.timeout,
                          log_to_ciw=False) as client:
            del secret
            assert client.handshake_virtuoso_pid(timeout=args.timeout) == ready['pid']
            report['identity_verified_over_ssh'] = True
            version = client.execute_skill('getVersion(t)', retry_connect=False)
            assert version.ok and version.output.strip(), version.errors
            report['virtuoso_version'] = version.output
            for _ in range(3):
                warmup = client.call('plus',1,2,timeout=args.timeout)
                assert warmup.ok and warmup.output.strip()=='3'
            report['warmup_plus_calls'] = 3
            report.update(run_workload(client, args, output, version.output, tunnel))
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        try:
            remote_python(args, f'from pathlib import Path; Path({remote+"/stop.json"!r}).write_text("{{}}")')
            if runner is not None:
                runner.wait(timeout=40)
            artifacts = json.loads(remote_python(args,
                'import json; from pathlib import Path; '
                f'p=Path({remote!r}); latest=p/"sessions/latest-live.txt"; '
                'result=json.loads((Path(latest.read_text())/"result.json").read_text()) if latest.exists() else None; '
                'rss=json.loads((p/"remote-rss.json").read_text()) if (p/"remote-rss.json").exists() else []; '
                'print(json.dumps({"runner":result,"rss":rss}))'))
            report['remote_runner'] = artifacts['runner']
            report['remote_rss'] = summarize_rss(artifacts['rss'])
            (output/'remote-rss.json').write_text(json.dumps(artifacts['rss'], indent=2))
        except Exception as error:
            report['cleanup_error'] = f'{type(error).__name__}: {error}'
        stop_owned(tunnel)
        stop_owned(runner)
        report['tunnel_stopped'] = tunnel is None or tunnel.poll() is not None
        completed = report.get('remote_runner') or {}
        report['status'] = completion_status(report, completed)
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2), flush=True)
    return 0 if report['status']=='PASS' else 1


def run_workload(client, args, output, expected_version, tunnel):
    events = []
    started = time.monotonic()
    next_cycle = started
    local_before = process_rss()
    sequence = 0
    next_progress = started + 60
    with (output/'requests.jsonl').open('w', buffering=1) as stream:
        while time.monotonic()-started < args.duration:
            if tunnel.poll() is not None:
                raise RuntimeError('Owned SSH forwarding process exited')
            for kind in ('plus','getVersion'):
                began = time.monotonic()
                event = {'sequence':sequence,'kind':kind,'elapsed_s':began-started,'ok':False}
                try:
                    result = (client.call('plus',sequence,7,timeout=args.timeout) if kind=='plus' else
                              client.execute_skill('getVersion(t)',timeout=args.timeout,retry_connect=False))
                    event['ok'] = result.ok and (result.output.strip()==str(sequence+7) if kind=='plus'
                                                else result.output==expected_version)
                    event['result'] = result.model_dump(mode='json')
                    if not result.ok:
                        event['outcome'] = 'failed or unknown; no replay of this request'
                except Exception as error:
                    event['error'] = f'{type(error).__name__}: {error}'
                    event['outcome'] = 'unknown; no replay of this request'
                event['latency_ms'] = (time.monotonic()-began)*1000
                stream.write(json.dumps(event)+'\n')
                events.append({key:event[key] for key in ('kind','elapsed_s','latency_ms','ok')})
                sequence += 1
            now = time.monotonic()
            if now >= next_progress:
                print(json.dumps({'progress_elapsed_s':now-started, 'requests':len(events),
                                  'errors':sum(not e['ok'] for e in events)}), flush=True)
                next_progress = now+60
            next_cycle += args.interval
            time.sleep(max(0.0,min(next_cycle-time.monotonic(),started+args.duration-time.monotonic())))
    elapsed = time.monotonic()-started
    return {'elapsed_s':elapsed, 'at_least_30_minutes':elapsed>=1800, 'requests':len(events),'errors':sum(not e['ok'] for e in events),
            'windows':summarize_windows(events,args.window), 'local_rss_before_kib':local_before,
            'local_rss_after_kib':process_rss()}


def summarize_windows(events, width):
    groups = {}
    for event in events:
        number = int(event['elapsed_s']//width)
        groups.setdefault((number,event['kind']),[]).append(event)
    return [{'start_s':number*width,'end_s':(number+1)*width,'kind':kind,'requests':len(rows),
             'errors':sum(not r['ok'] for r in rows), **latency_stats([r['latency_ms'] for r in rows])}
            for (number,kind),rows in sorted(groups.items())]


def latency_stats(values):
    ordered = sorted(values)
    return {f'p{p}_ms':ordered[math.ceil(len(ordered)*p/100)-1] for p in (50,95,99)} | {'max_ms':max(ordered)}


def completion_status(report, remote):
    passed = (not any(k in report for k in ('error','cleanup_error')) and report.get('identity_verified_over_ssh')
              and report.get('elapsed_s',0)>=report['requested_duration_s'] and report.get('requests',0)>0
              and report.get('errors')==0 and report.get('tunnel_stopped') and remote.get('status')=='PASS'
              and all(remote.get(k) for k in ('process_stopped','daemon_stopped','xvfb_stopped')))
    return 'PASS' if passed else 'FAIL'


def summarize_rss(samples):
    if not samples:
        return {}
    return {key:{'before_kib':samples[0][key],'after_kib':samples[-1][key],
                 'max_kib':max(row[key] for row in samples if row[key] is not None)}
            for key in ('virtuoso_rss_kib','daemon_rss_kib')}


def process_rss():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    return None


def wait_ready(args, remote, runner):
    deadline = time.monotonic()+120
    while time.monotonic()<deadline:
        if runner.poll() is not None:
            raise RuntimeError('Owned remote runner exited before rendezvous; inspect runner.log')
        output = remote_python(args, f'from pathlib import Path; p=Path({remote+"/ready.json"!r}); print(p.read_text() if p.exists() else "")')
        if output.strip():
            return json.loads(output)
        time.sleep(1)
    raise TimeoutError('Owned remote session did not become ready')


def wait_forward(process, port):
    deadline = time.monotonic()+15
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError('SSH forwarding failed; inspect tunnel.log')
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise TimeoutError('SSH local forwarding did not bind')


def ssh_command(host, command):
    return ['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10',host,command]


def remote_python(args, source):
    result = subprocess.run(ssh_command(args.host,shlex.join([args.remote_python,'-c',source])),
                            capture_output=True,text=True,timeout=25)
    if result.returncode:
        raise RuntimeError(f'Remote control command failed with status {result.returncode}')
    return result.stdout


def stop_owned(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=5)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='eda-220')
    parser.add_argument('--remote-python',required=True)
    parser.add_argument('--remote-root',required=True)
    parser.add_argument('--cds-lib',required=True)
    parser.add_argument('--virtuoso',default='/eda/cds/IC2310/tools/dfII/bin/virtuoso')
    parser.add_argument('--duration',type=float,default=1800)
    parser.add_argument('--interval',type=float,default=1)
    parser.add_argument('--window',type=float,default=300)
    parser.add_argument('--timeout',type=float,default=10)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    for key in ('duration','interval','window','timeout'):
        if not math.isfinite(getattr(args,key)) or getattr(args,key)<=0:
            parser.error(f'--{key} must be finite and positive')
    return args


if __name__=='__main__':
    raise SystemExit(main())
