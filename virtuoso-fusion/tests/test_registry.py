import gzip
import json
import tempfile
import unittest
from pathlib import Path

from virtuoso_fusion.availability import availability
from virtuoso_fusion.registry import ApiRegistry, parse_arglist


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'registry.json.gz'
        with gzip.open(self.path, 'wt') as stream:
            json.dump({'dbRead': {'name': 'dbRead', 'zero_args': True, 'contexts': ['headless']}, 'dbGui': {'contexts': ['layout-window'], 'arglist_raw': '(cv)'}, 'empty': {}}, stream)
        self.registry = ApiRegistry(self.path)

    def test_coverage_counts_signatures_without_claiming_verification(self):
        coverage = self.registry.coverage()
        self.assertEqual(coverage['total'], 3)
        self.assertEqual(coverage['with_signature'], 2)
        self.assertEqual(coverage['coverage'], 0.6667)
        self.assertEqual(coverage['meaning'], 'signature coverage, not engineering validation')

    def test_search_returns_named_records_with_limit(self):
        self.assertEqual([r['name'] for r in self.registry.search('DB', limit=1)], ['dbRead'])
        self.assertEqual(self.registry.search('missing'), [])
        self.assertEqual(self.registry.search('', limit=0), [])
        with self.assertRaises(ValueError):
            self.registry.search('', limit=-1)

    def test_availability_preserves_context_and_marks_unverified(self):
        self.assertEqual(availability(self.registry, 'dbGui')['state'], 'NEEDS_WINDOW')
        self.assertEqual(availability(self.registry, 'dbRead')['state'], 'HEADLESS_CANDIDATE')
        self.assertFalse(availability(self.registry, 'dbRead')['verified'])
        self.assertEqual(availability(self.registry, 'empty')['state'], 'NO_SIGNATURE')
        self.assertEqual(availability(self.registry, 'absent')['state'], 'NOT_IN_REGISTRY')

    def test_parse_rejects_nested_and_keyword_signatures(self):
        for signature in ('(a @key b)', '(a @optional (b 2))', '(a | b)'):
            with self.subTest(signature=signature):
                self.assertEqual(parse_arglist(signature), [])
        self.assertEqual(parse_arglist('(n_a @optional n_b)')[1]['optional'], True)

    def test_empty_registry_coverage_is_defined(self):
        path = Path(self.temp.name) / 'empty.json'
        path.write_text('{}')
        coverage = ApiRegistry(path).coverage()
        self.assertEqual(coverage['total'], 0)
        self.assertEqual(coverage['coverage'], 0.0)
        self.assertEqual(coverage['public_coverage'], 0.0)

    def test_malformed_registry_fails_at_load(self):
        path = Path(self.temp.name) / 'bad.json'
        for raw in ([], {'fn': []}, {'fn': {'params': [{'name': 5}]}}):
            path.write_text(json.dumps(raw))
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ApiRegistry(path)
