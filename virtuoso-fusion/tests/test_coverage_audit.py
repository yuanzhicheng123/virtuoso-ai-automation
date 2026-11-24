import importlib.util
from collections import Counter
import json
import tempfile
from pathlib import Path
import unittest

from virtuoso_fusion import ApiRegistry

spec = importlib.util.spec_from_file_location('coverage_audit', Path(__file__).parents[1] / 'tools/audit_coverage.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class CoverageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ApiRegistry()
        cls.report = audit.audit_registry(cls.registry)

    def test_every_packaged_name_has_exactly_one_compiler_outcome(self):
        groups = [self.report['compiler'][key]['names'] for key in (
            'name_rejected', 'policy_rejected_even_with_allow_write', 'compile_eligible')]
        self.assertEqual(sum(map(len, groups)), len(self.registry.raw))
        self.assertEqual(set().union(*map(set, groups)), set(self.registry.raw))

    def test_real_policy_and_signature_boundaries_are_retained(self):
        compiler = self.report['compiler']
        self.assertIn('dbDeleteObject', compiler['policy_rejected_even_with_allow_write']['names'])
        self.assertIn('dbOpenCellViewByType', compiler['requires_explicit_allow_write']['names'])
        self.assertIn('dbOpenCellViewByType', compiler['no_keyword_binding_candidates']['names'])
        self.assertIn('plus', compiler['keyword_binding_candidates']['names'])
        self.assertGreater(compiler['name_rejected']['count'], 0)
        self.assertLess(self.report['inventory']['documented'], self.report['inventory']['public'])

    def test_runtime_summary_keeps_binding_and_compilation_separate(self):
        observations = dict.fromkeys(self.registry.raw, 'UNBOUND')
        for name in ('plus', 'cons*', 'dbDeleteObject'):
            observations[name] = 'BOUND'
        inventory = {
            'registry_sha256': self.report['source']['registry_sha256'],
            'version': 'test snapshot',
            'observations': observations,
            'counts': dict(Counter(observations.values())),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inventory.json'
            path.write_text(json.dumps(inventory))
            summary = audit.summarize_runtime(self.report, {'headless': path})
            context = summary['contexts']['headless']
            self.assertEqual(context['bound_and_generic_compile_eligible'], 1)
            self.assertEqual(context['bound_but_generic_name_rejected'], 1)
            self.assertEqual(context['bound_but_generic_policy_rejected'], 1)
            self.assertEqual(summary['union']['bound_but_generic_name_rejected'], ['cons*'])
            self.assertNotIn('cons*', summary['union']['unbound_in_every_context'])
            inventory['contexts'] = {'base': {'observations': observations, 'counts': inventory['counts']}}
            inventory['native'] = {'base': {'count': 4, 'names': ['plus', 'cons*', 'dbDeleteObject', 'newSessionFunction']}}
            path.write_text(json.dumps(inventory))
            native = audit.summarize_runtime(self.report, {'native': path})['native_universe']['union']
            self.assertEqual(native['bound'], 4)
            self.assertEqual(native['present_in_registry'], 3)
            self.assertEqual(native['registry_name_recall'], 0.75)
            self.assertEqual(native['new_names'], ['newSessionFunction'])
            self.assertEqual(native['registered_and_generic_compile_eligible'], 1)
            inventory['registry_sha256'] = 'different-registry'
            path.write_text(json.dumps(inventory))
            with self.assertRaisesRegex(ValueError, 'registry hash differs'):
                audit.summarize_runtime(self.report, {'headless': path})
