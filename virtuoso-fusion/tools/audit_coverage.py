"""Audit the real generic compiler without executing any Cadence function."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from virtuoso_fusion.generic import GenericSkill
from virtuoso_fusion.registry import ApiRegistry
from virtuoso_fusion.values import validate_name


def audit_registry(registry: ApiRegistry) -> dict:
    generated = []
    generic = GenericSkill(lambda code, **options: generated.append(code), registry,
                           allow_write=True, auto_signature=False)
    rejected_names = []
    rejected_policy = []
    eligible = []
    keyword_candidates = []
    keyword_unavailable = []
    explicit_write = []
    object_parameters = []
    contexts = Counter()
    evidence = Counter()
    for name, record in registry.raw.items():
        contexts[tuple(record.get('contexts') or [])] += 1
        evidence.update(record.get('evidence') or [])
        try:
            validate_name(name)
        except ValueError:
            rejected_names.append(name)
            continue
        try:
            generic.call(name)
        except PermissionError:
            rejected_policy.append(name)
            continue
        eligible.append(name)
        if not generic.policy.check(name, allow_write=False)[0]:
            explicit_write.append(name)
        if generic._positional_params(name):
            keyword_candidates.append(name)
        else:
            keyword_unavailable.append(name)
        if any(param.get('type') == 'object' for param in registry.params(name)):
            object_parameters.append(name)
    assert len(generated) == len(eligible)
    return {
        'schema_version': 1,
        'method': 'Actual validate_name + GenericSkill.call(name) with auto_signature=False; executor records text only. Empty argument calls test compilation, not valid function arity or runtime execution.',
        'source': {'registry_path': str(registry.path),
                   'registry_sha256': hashlib.sha256(registry.path.read_bytes()).hexdigest(),
                   'code_sha256': {name: hashlib.sha256((Path(__file__).parents[1] / 'src/virtuoso_fusion' / name).read_bytes()).hexdigest()
                                   for name in ('values.py', 'generic.py', 'registry.py', 'policy.py')}},
        'inventory': {**registry.coverage(),
                      'documented': sum(bool(row.get('documented')) for row in registry.raw.values()),
                      'documented_without_leading_underscore': sum(bool(row.get('documented')) and not name.startswith('_') for name, row in registry.raw.items()),
                      'context_groups': [{'contexts': list(key), 'count': count} for key, count in contexts.items()],
                      'historical_evidence_labels': dict(evidence)},
        'compiler': {
            'name_rejected': {'count': len(rejected_names), 'names': rejected_names},
            'policy_rejected_even_with_allow_write': {'count': len(rejected_policy), 'names': rejected_policy},
            'compile_eligible': {'count': len(eligible), 'names': eligible},
            'requires_explicit_allow_write': {'count': len(explicit_write), 'names': explicit_write},
            'keyword_binding_candidates': {'count': len(keyword_candidates), 'names': keyword_candidates},
            'no_keyword_binding_candidates': {'count': len(keyword_unavailable), 'names': keyword_unavailable},
            'eligible_with_explicit_object_parameters': {'count': len(object_parameters), 'names': object_parameters},
        },
        'limits': [
            'Not a live callability or engineering validation percentage.',
            'public means only no leading underscore; documented is a separate historical source flag.',
            'Name or policy rejection only excludes the generic entry point; engineered facades or explicitly authored raw SKILL may still support the operation.',
            'Symbol emits a quoted symbol, not a dereferenced SKILL variable or database object. Strings are data. There is no Python OA-handle wrapper.',
            'Keyword candidates concern Python positional-name binding; SKILL ?keyword arguments, expressions, and special forms are not thereby represented.',
            'Context and CALLABLE labels are historical inventory hints, not evidence of execution in the current session.',
            'getd presence proves a current function binding only; valid inputs, GUI state, licenses and PDK compatibility remain separate.',
        ],
    }


def summarize_runtime(static: dict, inventory_paths: dict[str, Path]) -> dict:
    compiler = static['compiler']
    eligible = set(compiler['compile_eligible']['names'])
    rejected = set(compiler['name_rejected']['names'])
    policy = set(compiler['policy_rejected_even_with_allow_write']['names'])
    names = eligible | rejected | policy
    contexts = {}
    sources = {}
    versions = {}
    native_contexts = {}
    for source_name, path in inventory_paths.items():
        inventory = json.loads(path.read_text())
        if inventory['registry_sha256'] != static['source']['registry_sha256']:
            raise ValueError(f'{path}: registry hash differs from static audit')
        sources[source_name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        versions[source_name] = inventory['version']
        snapshots = inventory.get('contexts', {source_name: inventory})
        for context, snapshot in snapshots.items():
            observations = snapshot['observations']
            if set(observations) != names or dict(Counter(observations.values())) != {
                key: value for key, value in snapshot['counts'].items() if value
            } or not set(observations.values()) <= {'BOUND', 'UNBOUND', 'QUERY_ERROR'}:
                raise ValueError(f'{path}: incomplete or inconsistent inventory in {context}')
            contexts[context] = observations
        for context, snapshot in inventory.get('native', {}).items():
            native_names = set(snapshot['names'])
            observed_bound = {name for name, state in inventory['contexts'][context]['observations'].items() if state == 'BOUND'}
            if len(native_names) != snapshot['count'] or len(snapshot['names']) != snapshot['count'] or native_names & names != observed_bound:
                raise ValueError(f'{path}: native enumeration differs from snapshot inventory in {context}')
            native_contexts[context] = native_names
    union = {name for observations in contexts.values() for name, state in observations.items() if state == 'BOUND'}
    unbound = {name for name in names if all(observations[name] == 'UNBOUND' for observations in contexts.values())}
    native_union = set().union(*native_contexts.values()) if native_contexts else set()
    result = {
        'schema_version': 1,
        'meaning': 'getd binding inventory intersected with static text compilation; not function execution or engineering coverage',
        'registry_sha256': static['source']['registry_sha256'],
        'historical_registry_count': len(names),
        'sources': sources,
        'versions': versions,
        'contexts': {context: {
            'counts': {state: sum(value == state for value in observations.values()) for state in ('BOUND', 'UNBOUND', 'QUERY_ERROR')},
            'bound_and_generic_compile_eligible': sum(state == 'BOUND' and name in eligible for name, state in observations.items()),
            'bound_but_generic_name_rejected': sum(state == 'BOUND' and name in rejected for name, state in observations.items()),
            'bound_but_generic_policy_rejected': sum(state == 'BOUND' and name in policy for name, state in observations.items()),
        } for context, observations in contexts.items()},
        'union': {
            'bound': len(union),
            'bound_and_generic_compile_eligible': len(union & eligible),
            'unbound_in_every_context': sorted(unbound),
            'unbound_in_every_context_and_generic_compile_eligible': sorted(unbound & eligible),
            'unbound_in_every_context_and_generic_name_rejected': sorted(unbound & rejected),
            'bound_but_generic_name_rejected': sorted(union & rejected),
        },
        'limitations': [
            'Snapshots describe loaded context stages; later GUI contexts may retain earlier loaded modules.',
            'Current IC23.1 bindings do not establish compatibility with another Cadence version, installation or PDK.',
            'No inventoried function was executed by getd inspection; arguments, object handles, licenses and output correctness remain unverified.',
        ],
    }

    if native_contexts:
        result['native_universe'] = {
            'scope': 'All getd-bound names enumerated from oblist in this session at its observed loaded stages; not all Cadence versions/products/licenses and not executed-function coverage',
            'contexts': {context: {
                'bound': len(bound), 'present_in_registry': len(bound & names),
                'absent_from_registry': len(bound - names),
                'registry_name_recall': len(bound & names) / len(bound) if bound else None,
            } for context, bound in native_contexts.items()},
            'union': {
                'bound': len(native_union),
                'present_in_registry': len(native_union & names),
                'absent_from_registry': len(native_union - names),
                'registry_name_recall': len(native_union & names) / len(native_union),
                'missing_name_fraction': len(native_union - names) / len(native_union),
                'registered_and_generic_compile_eligible': len(native_union & eligible),
                'registered_generic_compile_fraction': len(native_union & eligible) / len(native_union),
                'new_names': sorted(native_union - names),
                'registry_names_absent_from_native_union': sorted(names - native_union),
            },
            'limitation': 'Names absent from the registry have not been added to production data; absence does not imply GenericSkill rejects a valid unregistered name, but signatures, semantics and execution remain unverified.',
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--headless-inventory', type=Path)
    parser.add_argument('--gui-inventory', type=Path)
    parser.add_argument('--runtime-output', type=Path)
    parser.add_argument('--native-inventory', type=Path)
    args = parser.parse_args()
    if any((args.headless_inventory, args.gui_inventory, args.runtime_output, args.native_inventory)) and not all((args.headless_inventory, args.gui_inventory, args.runtime_output)):
        parser.error('runtime audit requires both inventories and --runtime-output')
    report = audit_registry(ApiRegistry(args.registry))
    if args.runtime_output:
        inventory_paths = {'headless': args.headless_inventory, 'gui': args.gui_inventory}
        if args.native_inventory:
            inventory_paths['native'] = args.native_inventory
        runtime = summarize_runtime(report, inventory_paths)
        args.runtime_output.parent.mkdir(parents=True, exist_ok=True)
        args.runtime_output.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'inventory': report['inventory'],
                      'compiler_counts': {key: value['count'] for key, value in report['compiler'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
