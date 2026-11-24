"""Independent fusion commands; upstream keeps its own complete CLI."""
import argparse
import contextlib
import json
import sys

from .registry import ApiRegistry


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'upstream':
        from virtuoso_bridge.cli import main as upstream_main
        return upstream_main(argv[1:])
    parser = argparse.ArgumentParser(prog='virtuoso-fusion')
    commands = parser.add_subparsers(dest='command', required=True)
    query = commands.add_parser('registry', help='Search the local SKILL registry')
    query.add_argument('query')
    query.add_argument('--limit', type=int, default=20)
    commands.add_parser('coverage', help='Report signature coverage, not functional acceptance')
    commands.add_parser('upstream', help='Forward remaining arguments to virtuoso-bridge')
    call = commands.add_parser('call', help='Execute a registered SKILL function')
    call.add_argument('name')
    call.add_argument('--args', type=_json_array, default=[])
    call.add_argument('--kwargs', type=_json_object, default={})
    call.add_argument('--timeout', type=float, default=60.0)
    call.add_argument('--allow-write', action='store_true')
    call.add_argument('--evidence-dir')
    physical = commands.add_parser('physical', help='Run Calibre; execution success is separate from design acceptance')
    physical.add_argument('kind', choices=('drc', 'lvs', 'pex', 'rcx'))
    physical.add_argument('--deck', required=True)
    physical.add_argument('--layout', required=True)
    physical.add_argument('--primary', required=True)
    physical.add_argument('--work-dir', required=True)
    physical.add_argument('--source')
    physical.add_argument('--source-primary')
    physical.add_argument('--calibre', default='calibre')
    physical.add_argument('--timeout', type=float, default=240)
    physical.add_argument('--netlist', default='net.dist')
    discover = commands.add_parser('discover', help='Report tool/deck presence without checking out licenses')
    discover.add_argument('--deck', action='append', default=[])
    args = parser.parse_args(argv)
    if args.command == 'physical' and args.kind != 'drc' and not args.source:
        parser.error('--source is required for LVS/PEX')
    try:
        if args.command == 'physical':
            from .physical import PhysicalVerification
            payload = PhysicalVerification(calibre=args.calibre).run(
                args.kind, deck=args.deck, work_dir=args.work_dir, layout=args.layout,
                primary=args.primary, source=args.source, source_primary=args.source_primary,
                timeout=args.timeout, netlist=args.netlist)
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if payload['ok'] else (2 if payload['execution_ok'] else 1)
        if args.command == 'discover':
            from .environment import discover_tools
            payload = discover_tools(decks=args.deck)
        elif args.command == 'coverage':
            payload = ApiRegistry().coverage()
        elif args.command == 'registry':
            payload = ApiRegistry().search(args.query, limit=args.limit)
        else:
            from .client import FusionClient
            with contextlib.redirect_stdout(sys.stderr):
                client = FusionClient.from_env(allow_write=args.allow_write, evidence_dir=args.evidence_dir)
                try:
                    result = client.call(args.name, *args.args, timeout=args.timeout, **args.kwargs)
                finally:
                    client.close()
            print(result.model_dump_json())
            return 0 if result.ok else 1
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        result = getattr(exc, 'result', None)
        if result is not None and hasattr(result, 'model_dump_json'):
            print(result.model_dump_json())
        else:
            print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False))
        return 1


def _json_array(value):
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not isinstance(parsed, list):
        raise argparse.ArgumentTypeError('--args must be a JSON array')
    return parsed


def _json_object(value):
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError('--kwargs must be a JSON object')
    return parsed
