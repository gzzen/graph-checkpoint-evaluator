"""Command-line interface: `gce <command>`."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from .graph import Graph, load_graphs
from .io import dump_json, load_doc
from .schemas import SCHEMA_FILES, detect_kind, schema_errors


def safe_name(run_unit_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", run_unit_id).strip("_") or "unit"


def parse_options(pairs: list[str] | None) -> dict:
    options = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--option expects key=value, got {pair!r}")
        try:
            options[key] = json.loads(value)
        except json.JSONDecodeError:
            options[key] = value
    return options


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


def cmd_adapt(args: argparse.Namespace) -> int:
    from .adapters.base import load_adapter

    adapter = load_adapter(args.adapter, **parse_options(args.option))
    out = Path(args.out)
    written, invalid = 0, 0
    for ref in adapter.discover(Path(args.run_root)):
        doc = adapter.to_trace(ref)
        errors = schema_errors(doc, "trace")
        if errors:
            invalid += 1
            print(f"INVALID {ref.id}: {errors[0]}", file=sys.stderr)
            if not args.keep_invalid:
                continue
        dump_json(doc, out / f"{safe_name(ref.id)}.trace.json")
        written += 1
    print(f"wrote {written} traces to {out} ({invalid} invalid)")
    return 1 if invalid else 0


def cmd_adapter_check(args: argparse.Namespace) -> int:
    from .adapter_check import check_adapter
    from .adapters.base import load_adapter

    adapter = load_adapter(args.adapter, **parse_options(args.option))
    graphs = load_graphs(args.graphs) if args.graphs else None
    report = check_adapter(adapter, args.run_root, graphs=graphs, sample=args.sample, seed=args.seed)
    print(report.render())
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gce", description="graph-checkpoint-evaluator")
    parser.add_argument("--version", action="version", version=f"gce {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate graphs, traces, stage logs or evaluations")
    p.add_argument("files", nargs="+")
    p.add_argument("--kind", choices=sorted(SCHEMA_FILES), help="document kind (default: detect)")
    p.set_defaults(func=cmd_validate)

    def adapter_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--adapter", required=True, help="registry name, module:Class, or path/to/adapter.py[:Class]")
        p.add_argument("--run-root", required=True, help="run directory to read (never modified)")
        p.add_argument("--option", action="append", metavar="KEY=VALUE", help="adapter option (JSON values allowed)")

    p = sub.add_parser("adapt", help="convert a run directory into trace.v1 files")
    adapter_args(p)
    p.add_argument("--out", required=True, help="output directory for <run_unit_id>.trace.json")
    p.add_argument("--keep-invalid", action="store_true", help="also write traces that fail schema validation")
    p.set_defaults(func=cmd_adapt)

    p = sub.add_parser("adapter-check", help="conformance check of an adapter on a run directory")
    adapter_args(p)
    p.add_argument("--graphs", nargs="*", help="graph files/directories to check task ids against")
    p.add_argument("--sample", type=int, default=5, help="number of run units to convert (default 5)")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_adapter_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
