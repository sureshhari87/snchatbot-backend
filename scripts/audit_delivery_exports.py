"""Audit supplied private JSON snapshots; never connects to a service or imports data."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from delivery_reconciliation import audit


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid audit arguments")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def load(path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Export exceeds offline audit limit")
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)


def main(argv=None):
    parser = PrivateParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        sources = [args.legacy, args.backend] + ([args.bindings] if args.bindings else [])
        if args.report.resolve() in {path.resolve() for path in sources}:
            raise ValueError("Report cannot replace an input")
        report = audit(
            load(args.legacy), load(args.backend), load(args.bindings) if args.bindings else None
        )
        rendered = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        with args.report.open("x", encoding="utf-8") as output:
            output.write(rendered)
        print(json.dumps({"audit": "completed", **report["summary"], "live_changes": False}))
        return 0
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        print(json.dumps({"audit": "stopped", "input_contents": "withheld", "live_changes": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
