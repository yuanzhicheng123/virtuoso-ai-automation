import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_snapshot_provenance_matches_packaged_bytes(self):
        package = ROOT / 'src/virtuoso_fusion'
        manifest = json.loads((package / '_data/provenance.json').read_text())
        self.assertEqual(manifest['upstream_commit'], '672b451416b1ef3cc560db4196365075e94c8d4e')
        for relative, digest in manifest['files'].items():
            with self.subTest(path=relative):
                self.assertEqual(hashlib.sha256((package / relative).read_bytes()).hexdigest(), digest)
        self.assertIn('_data/api_registry_v2.json.gz', manifest['files'])
        self.assertIn('_upstream/mcp_server.py', manifest['files'])
        self.assertIn('_upstream/skills/virtuoso/SKILL.md', manifest['files'])

    def test_snapshot_verification_needs_no_original_sources(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/prepare_resources.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
