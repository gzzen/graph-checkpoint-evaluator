"""Command-line interface: `gce <command>`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .graph import Graph
from .io import load_doc
from .schemas import SCHEMA_FILES, detect_kind, schema_errors


def cmd_validate(args: argparse.Namespace) -> int:
    failed = 0
    for name in args.files:
        path = Path(name)
        try:
            doc = load_doc(path)
        except Exception as exc:  # unreadable file is a validation failure, not a crash
            print(f"FAIL {path}: cannot load: {exc}")
            failed += 1
            continue
        kind = args.kind or detect_kind(doc)
        if kind is None:
            print(f"FAIL {path}: cannot detect document kind; pass --kind")
            failed += 1
            continue
        errors = schema_errors(doc, kind)
        if not errors and kind == "checkpoint_graph":
            errors = Graph.from_dict(doc).semantic_errors()
        if errors:
            failed += 1
            print(f"FAIL {path} ({kind})")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"OK   {path} ({kind})")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gce", description="graph-checkpoint-evaluator")
    parser.add_argument("--version", action="version", version=f"gce {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate graphs, traces, stage logs or evaluations")
    p.add_argument("files", nargs="+")
    p.add_argument("--kind", choices=sorted(SCHEMA_FILES), help="document kind (default: detect)")
    p.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
