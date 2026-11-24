#!/usr/bin/env python3
"""N65 acceptance convenience wrapper around the installed physical API."""
import argparse
import json
from pathlib import Path

from virtuoso_fusion import PhysicalVerification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--gds', type=Path, required=True)
    parser.add_argument('--cell', required=True)
    parser.add_argument('--mode', choices=('drc', 'lvs', 'pex'), required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--timeout', type=float, default=240)
    args = parser.parse_args()
    if args.mode != 'drc' and args.source is None:
        parser.error('--source is required for LVS/PEX')
    folder = 'rcx' if args.mode == 'pex' else args.mode
    deck = Path('/eda/1P9M/TSMC065_1P9M/Calibre') / folder / ('calibre.' + folder)
    result = PhysicalVerification().run(args.mode, deck=deck, work_dir=args.root,
        layout=args.gds, primary=args.cell, source=args.source, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else (2 if result['execution_ok'] else 1)


if __name__ == '__main__':
    raise SystemExit(main())
