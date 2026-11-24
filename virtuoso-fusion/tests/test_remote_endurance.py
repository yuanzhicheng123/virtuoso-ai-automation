"""Deterministic report/gate tests; transport evidence comes from actual SSH runs."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('remote_endurance', Path(__file__).parents[1]/'tools/run_remote_endurance.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_latency_nearest_rank_keeps_tail_outlier():
    assert module.latency_stats(list(range(1,101))) == {'p50_ms':50,'p95_ms':95,'p99_ms':99,'max_ms':100}


def test_window_boundaries_and_failures_are_not_dropped():
    events = [dict(elapsed_s=0,kind='plus',latency_ms=1,ok=True),
              dict(elapsed_s=59.9,kind='plus',latency_ms=10000,ok=False),
              dict(elapsed_s=60,kind='plus',latency_ms=2,ok=True),
              dict(elapsed_s=60,kind='getVersion',latency_ms=3,ok=True)]
    windows = module.summarize_windows(events,60)
    assert len(windows)==3
    first = windows[0]
    assert first['requests']==2 and first['errors']==1 and first['p99_ms']==10000
    assert {(row['start_s'],row['kind']) for row in windows[1:]} == {(60,'plus'),(60,'getVersion')}


@pytest.fixture
def passed():
    return ({'requested_duration_s':1800,'elapsed_s':1800.01,'identity_verified_over_ssh':True,
             'requests':3600,'errors':0,'tunnel_stopped':True},
            {'status':'PASS','process_stopped':True,'daemon_stopped':True,'xvfb_stopped':True})


def test_complete_gate_accepts_real_duration_and_cleanup(passed):
    assert module.completion_status(*passed)=='PASS'


@pytest.mark.parametrize('key,value',[('elapsed_s',1799.9),('errors',1),('requests',0),
                                    ('identity_verified_over_ssh',False),('tunnel_stopped',False),
                                    ('cleanup_error','remote unreachable'),('error','failed')])
def test_incomplete_or_dirty_run_cannot_pass(passed,key,value):
    report,remote = passed
    report[key]=value
    assert module.completion_status(report,remote)=='FAIL'


@pytest.mark.parametrize('key,value',[('status','INCOMPLETE'),('process_stopped',False),
                                    ('daemon_stopped',False),('xvfb_stopped',False)])
def test_remote_runner_is_required_for_acceptance(passed,key,value):
    report,remote = passed
    remote[key]=value
    assert module.completion_status(report,remote)=='FAIL'
