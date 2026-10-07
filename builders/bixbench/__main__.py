"""BixBench reference builder. Run from the repository root: `python -m builders.bixbench <command>`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .draft import draft_yaml
from .notebooks import load_cells, notebook_language, outline
from .questions import answer_visibility, by_capsule, ideal_spec, load_questions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m builders.bixbench")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("questions", help="list a capsule's questions with ideal ValueSpecs and visibility class")
    p.add_argument("sid")
    p = sub.add_parser("outline", help="compact outline of a capsule notebook")
    p.add_argument("sid")
    p.add_argument("--width", type=int, default=300)
    p = sub.add_parser("figures", help="save a cell's figures as PNG files")
    p.add_argument("sid")
    p.add_argument("cell", type=int)
    p.add_argument("--out", default="out/figures")
    p = sub.add_parser("draft", help="print a checkpoint graph skeleton (YAML)")
    p.add_argument("sid")
    p = sub.add_parser("rerun", help="re-run the instrumented notebook described by reruns/<sid>.yaml")
    p.add_argument("sid")
    args = parser.parse_args(argv)

    cfg = load_config()
    capsules = by_capsule(load_questions(cfg.questions))
    rows = capsules[args.sid]
    if args.command == "questions":
        vis = answer_visibility()["questions"]
        for row in rows:
            print(f"{row['question_id']}  [{vis.get(row['question_id'])}]  ideal={row['ideal']!r} -> {json.dumps(ideal_spec(row))}")
            print(f"    {row['question']}")
    elif args.command == "outline":
        notebook_dir = cfg.notebook_dir(rows[0]["capsule_uuid"])
        print(f"# {args.sid} ({notebook_language(notebook_dir)})")
        print(outline(load_cells(notebook_dir), args.width))
    elif args.command == "figures":
        cell = load_cells(cfg.notebook_dir(rows[0]["capsule_uuid"]))[args.cell]
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for i, png in enumerate(cell.images):
            path = out / f"{args.sid}_cell{args.cell}_{i}.png"
            path.write_bytes(png)
            print(path)
    elif args.command == "draft":
        print(draft_yaml(args.sid, rows))
    elif args.command == "rerun":
        from .rerun import rerun

        evidence = rerun(args.sid, cfg)
        print(json.dumps({k: evidence[k] for k in ("sid", "returncode", "anchors_ok", "patches")}, indent=1))
        for a in evidence["anchors"]:
            print(f"  anchor {a['name']}: expected {a['expected']} observed {a['observed']} -> {'OK' if a['ok'] else 'MISMATCH'}")
        for name, cp in evidence["checkpoints"].items():
            print(f"  checkpoint {name} = {cp['value']} (cell {cp['cell']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
