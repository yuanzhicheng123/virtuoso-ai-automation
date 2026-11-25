import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))


class CliTests(unittest.TestCase):
    def test_offline_coverage_command(self):
        proc = subprocess.run([sys.executable, '-m', 'virtuoso_fusion', 'coverage'], env=ENV, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreater(json.loads(proc.stdout)['total'], 70000)

    def test_upstream_help_passthrough(self):
        proc = subprocess.run([sys.executable, '-m', 'virtuoso_fusion', 'upstream', '--help'], env=ENV, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('start', proc.stdout)

    def test_call_rejects_non_array_args_before_connection(self):
        proc = subprocess.run([sys.executable, '-m', 'virtuoso_fusion', 'call', 'getVersion', '--args', '{}'], env=ENV, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('JSON array', proc.stderr)


class McpTests(unittest.TestCase):
    def test_stdio_preserves_original_tools_skills_and_adds_registry(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'skills/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'registry_coverage', 'arguments': {}}},
        ]
        proc = subprocess.run([sys.executable, '-m', 'virtuoso_fusion.mcp'], input='\n'.join(json.dumps(v) for v in requests)+'\n', env=ENV, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        names = {item['name'] for item in replies[0]['result']['tools']}
        self.assertTrue({'evaluate', 'simulate', 'registry_search', 'registry_coverage', 'call_registered'} <= names)
        self.assertGreater(len(replies[1]['result']['skills']), 0)
        self.assertGreater(replies[2]['result']['structuredContent']['total'], 70000)

    def test_failed_bridge_result_retains_metadata(self):
        from virtuoso_bridge import VirtuosoResult, ExecutionStatus
        from virtuoso_fusion import mcp
        original = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['offline'], metadata={'request_id': 'original-42', 'outcome': 'unknown'})
        rendered = mcp.server._bridge_result(original)
        self.assertTrue(rendered['isError'])
        self.assertEqual(rendered['structuredContent'], original.model_dump(mode='json'))


def test_call_registered_preserves_failure_and_limits_write_opt_in(monkeypatch):
    from virtuoso_bridge import VirtuosoClient, VirtuosoResult, ExecutionStatus
    from virtuoso_fusion import FusionClient, mcp
    failure = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['busy'], metadata={'request_id': 'original', 'outcome': 'unknown'})
    calls = []
    def execute(self, code, timeout=None, *, retry_connect=True):
        calls.append((code, timeout, retry_connect))
        return failure
    monkeypatch.setattr(VirtuosoClient, 'execute_skill', execute)
    with FusionClient() as client:
        monkeypatch.setitem(mcp.server._CLIENT, 'obj', client)
        result = mcp.server.dispatch_tool('call_registered', {'name': 'dbSave', 'args': [None], 'allow_write': True, 'timeout': 2}, {})
        assert result['isError'] is True
        assert result['structuredContent']['fusion_error']['stage'] == 'execution'
        assert {key: value for key, value in result['structuredContent'].items()
                if key != 'fusion_error'} == failure.model_dump(mode='json')
        assert calls == [('dbSave(nil)', 2, False)]
        assert client.allow_write is False
        denied = mcp.server.dispatch_tool('call_registered', {'name': 'dbSave', 'args': [None]}, {})
        assert denied['isError'] is True
        assert len(calls) == 1
