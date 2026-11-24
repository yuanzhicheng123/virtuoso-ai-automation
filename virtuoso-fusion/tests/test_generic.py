import json
import math
import tempfile
import unittest
from pathlib import Path

from virtuoso_bridge.models import ExecutionStatus, VirtuosoResult
from virtuoso_fusion.generic import GenericSkill
from virtuoso_fusion.registry import ApiRegistry
from virtuoso_fusion.values import Symbol, to_skill


class GenericTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'registry.json'
        self.path.write_text(json.dumps({
            'sum': {'params': [{'name': 'n_a'}, {'name': 'n_b'}]},
            'getThing': {'params': [{'name': 'g_obj'}, {'name': 's_name', 'type': 'symbol'}]},
            'optional': {'arglist_raw': '(a @optional b c)'},
            'ambiguous': {'arglist_raw': '(a @key b)'},
            'duplicates': {'params': [{'name': 'n_a'}, {'name': 's_a'}]},
            'nonfinalRest': {'params': [{'name': ' n_a ... '}, {'name': 'n_b'}]},
        }))
        self.calls = []
        self.result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='42', warnings=['notice'], metadata={'request_id': 'x'})
        self.responses = []
        self.generic = GenericSkill(self.execute, ApiRegistry(self.path), auto_signature=False)

    def execute(self, code, **options):
        self.calls.append((code, options))
        return self.responses.pop(0) if self.responses else self.result

    def test_positional_arguments_are_never_truncated(self):
        result = self.generic.call('sum', 1, 2, 3, 4)
        self.assertIs(result, self.result)
        self.assertEqual(self.calls, [('sum(1 2 3 4)', {'retry_connect': False})])

    def test_keyword_order_and_optional_gap(self):
        self.generic.call('sum', b=2, a=1)
        self.generic.call('optional', 1, c=3)
        self.assertEqual([c[0] for c in self.calls], ['sum(1 2)', 'optional(1 nil 3)'])

    def test_ambiguous_unknown_missing_duplicate_keywords_are_rejected(self):
        for fn, args, kwargs in [('ambiguous', (), {'b': 2}), ('duplicates', (), {'a': 1}), ('nonfinalRest', (), {'b': 2}), ('sum', (), {'c': 1}), ('sum', (), {'b': 1}), ('sum', (1,), {'a': 1})]:
            with self.subTest(fn=fn, args=args, kwargs=kwargs), self.assertRaises(TypeError):
                self.generic.call(fn, *args, **kwargs)
        self.assertEqual(self.calls, [])

    def test_symbol_is_explicit_even_with_registry_type(self):
        self.generic.call('getThing', None, 'name')
        self.generic.call('getThing', None, Symbol('name'))
        self.assertEqual([c[0] for c in self.calls], ['getThing(nil "name")', "getThing(nil 'name)"])

    def test_failed_result_is_preserved(self):
        self.result = VirtuosoResult(status=ExecutionStatus.ERROR, errors=['lost'], metadata={'request_id': 'pending'})
        self.assertIs(self.generic.call('sum', 1, 2), self.result)

    def test_signature_probe_failure_stops_execution_and_retains_evidence(self):
        for status in (ExecutionStatus.ERROR, ExecutionStatus.PARTIAL, ExecutionStatus.FAILURE):
            with self.subTest(status=status):
                self.calls.clear()
                self.generic.auto_signature = True
                result = VirtuosoResult(status=status, errors=['unknown outcome'], metadata={'request_id': 'probe'})
                self.responses = [result]
                self.assertIs(self.generic.call('unknown', 1), result)
                self.assertEqual(self.calls, [("arglist('unknown)", {'retry_connect': False})])
                self.assertIsNone(self.generic.reg.get('unknown'))

    def test_live_signature_is_cached_only_in_memory(self):
        original = self.path.read_bytes()
        self.generic.auto_signature = True
        self.responses = [VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(n_a n_b)')]
        self.assertIs(self.generic.call('unknown', b=2, a=1), self.result)
        self.assertIs(self.generic.call('unknown', 3, 4), self.result)
        self.assertEqual([c[0] for c in self.calls], ["arglist('unknown)", 'unknown(1 2)', 'unknown(3 4)'])
        self.assertEqual(self.path.read_bytes(), original)

    def test_successful_nil_probe_allows_positional_call(self):
        self.generic.auto_signature = True
        self.responses = [VirtuosoResult(status=ExecutionStatus.SUCCESS, output='nil')]
        self.generic.call('unknown', 1, 2)
        self.assertEqual(self.calls[-1][0], 'unknown(1 2)')
        self.generic.call('unknown', 3, 4)
        self.assertEqual([code for code, _ in self.calls], ["arglist('unknown)", 'unknown(1 2)', 'unknown(3 4)'])

    def test_write_requires_explicit_allow_and_known_allowlist(self):
        for fn in ('dbSave', 'dbOpenCellViewByType', 'dbDeleteObject'):
            with self.subTest(fn=fn), self.assertRaises(PermissionError):
                self.generic.call(fn)
        self.generic.allow_write = True
        self.generic.call('dbSave', Symbol('cv'))
        self.assertEqual(self.calls[-1][0], "dbSave('cv)")
        with self.assertRaises(PermissionError):
            self.generic.call('dbDeleteObject', Symbol('cv'))

    def test_function_name_rejected_before_probe(self):
        self.generic.auto_signature = True
        for name in ('x) system("bad")', '', 'x y'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.generic.call(name)
        self.assertEqual(self.calls, [])

    def test_timeout_applies_to_probe_and_request_without_retry(self):
        self.generic.auto_signature = True
        self.responses = [VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(n_a)')]
        self.generic.call('unknown', 1, timeout=4.5)
        self.assertEqual(self.calls, [("arglist('unknown)", {'timeout': 4.5, 'retry_connect': False}), ('unknown(1)', {'timeout': 4.5, 'retry_connect': False})])

    def test_exception_from_probe_propagates_without_main_call(self):
        calls = []
        def disconnected(code, **options):
            calls.append(code)
            raise ConnectionError('offline')
        generic = GenericSkill(disconnected, self.generic.reg)
        with self.assertRaises(ConnectionError):
            generic.call('unknown')
        self.assertEqual(calls, ["arglist('unknown)"])

    def test_ensure_signature_preserves_result_and_skips_cached_probe(self):
        result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='(n_a)')
        self.responses = [result]
        self.assertIs(self.generic.ensure_signature('unknown'), result)
        self.assertIsNone(self.generic.ensure_signature('unknown'))
        self.assertEqual(len(self.calls), 1)

    def test_dynamic_call(self):
        self.assertIs(self.generic.sum(1, 2), self.result)


class ValueTests(unittest.TestCase):
    def test_supported_values(self):
        self.assertEqual(to_skill([True, False, None, 3, 2.5, 'a"b\\c\n', (Symbol('a'),)]), 'list(t nil nil 3 2.5 "a\\"b\\\\c\\n" list(\'a))')

    def test_unsupported_values(self):
        for value in (math.inf, -math.inf, math.nan, {}, object(), b'bytes'):
            with self.subTest(value=repr(value)), self.assertRaises((TypeError, ValueError)):
                to_skill(value)

    def test_symbol_rejects_code(self):
        for name in ('x y', "x)", 'cv~>name', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Symbol(name)


class PackagedRegistryCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ApiRegistry()

    def setUp(self):
        self.calls = []
        self.result = VirtuosoResult(status=ExecutionStatus.SUCCESS, output='t')
        self.generic = GenericSkill(self.execute, self.registry, allow_write=True)

    def execute(self, code, **options):
        self.calls.append(code)
        return self.result

    def test_optional_names_with_whitespace(self):
        self.assertIs(self.generic.call('getVersion', opt=True), self.result)
        self.assertEqual(self.calls, ['getVersion(t)'])

    def test_reserved_names_use_existing_prefix_aliases(self):
        self.generic.call('geixGetNumRows', g_self=None)
        self.generic.call('_ciMemoizeCmd', g_fn=Symbol('plus'))
        self.generic.call('mpsDPut', pt=None, g_timeout=3, timeout=5)
        self.generic.call(fn='plus', op1=1, op2=2)
        self.assertEqual(self.calls, [
            'geixGetNumRows(nil)', "_ciMemoizeCmd('plus)",
            'mpsDPut(nil 3)', 'plus(1 2)',
        ])

    def test_variadic_tail_allows_binding_fixed_prefix(self):
        self.assertIs(self.generic.call('plus', op1=1, op2=2), self.result)
        self.assertEqual(self.calls, ['plus(1 2)'])

    def test_variadic_positional_calls_preserve_all_values(self):
        self.generic.call('plus', 1, 2, 3, 4)
        self.generic.call('printf', '%L %L', 10, 20)
        self.assertEqual(self.calls, ['plus(1 2 3 4)', 'printf("%L %L" 10 20)'])

    def test_ambiguous_signature_rejects_binding_before_execution(self):
        with self.assertRaises(TypeError):
            self.generic.call('dbOpenCellViewByType', lib='L', cellName='C')
        self.assertEqual(self.calls, [])

    def test_missing_trailing_required_argument_is_rejected(self):
        with self.assertRaises(TypeError):
            self.generic.call('nth', index0=1)
        self.assertEqual(self.calls, [])
