"""Read capsule notebooks: cells, text outputs, figures, and compact outlines for graph authors."""
from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
INSTALL_NOISE = re.compile(r"(Requirement already|Collecting |Downloading |Installing |Successfully installed|━━|\.{10}|also installing|trying URL|Content type)")


@dataclass
class Cell:
    index: int
    kind: str  # code | markdown
    source: str
    text_output: str
    images: list[bytes]
    errors: list[str]


def _output_text(output: dict) -> str:
    kind = output.get("output_type")
    if kind == "stream":
        return "".join(output.get("text", ""))
    if kind in ("execute_result", "display_data"):
        data = output.get("data", {})
        if "text/plain" in data:
            return "".join(data["text/plain"])
        if "text/html" in data:  # tables rendered only as HTML
            return re.sub(r"<[^>]+>", " ", html.unescape("".join(data["text/html"])))
    if kind == "error":
        return f"{output.get('ename')}: {output.get('evalue')}"
    return ""


def notebook_path(notebook_dir: Path) -> Path:
    found = sorted(notebook_dir.glob("*.ipynb"))
    if not found:
        raise FileNotFoundError(f"no notebook in {notebook_dir}")
    return found[0]


def load_cells(notebook_dir: Path) -> list[Cell]:
    nb = json.loads(notebook_path(notebook_dir).read_text(encoding="utf-8"))
    cells = []
    for i, c in enumerate(nb["cells"]):
        outputs = c.get("outputs", []) if c["cell_type"] == "code" else []
        cells.append(Cell(
            index=i,
            kind=c["cell_type"],
            source="".join(c["source"]),
            text_output=ANSI.sub("", "".join(_output_text(o) for o in outputs)),
            images=[base64.b64decode(o["data"]["image/png"]) for o in outputs if "image/png" in o.get("data", {})],
            errors=[f"{o.get('ename')}: {o.get('evalue')}" for o in outputs if o.get("output_type") == "error"],
        ))
    return cells


def notebook_language(notebook_dir: Path) -> str:
    nb = json.loads(notebook_path(notebook_dir).read_text(encoding="utf-8"))
    lang = (nb.get("metadata", {}).get("kernelspec", {}).get("language") or nb.get("metadata", {}).get("language_info", {}).get("name") or "").lower()
    if lang:
        return "R" if lang == "r" else "python"
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    return "R" if len(re.findall(r"<-|library\(", code)) > 2 * len(re.findall(r"^\s*(?:import |from \S+ import)", code, re.M)) else "python"


def outline(cells: list[Cell], width: int = 300) -> str:
    """One line per cell: markdown headings, code (comments stripped) and filtered text output."""
    lines = []
    for c in cells:
        if c.kind == "markdown":
            lines.append(f"[{c.index}] MD: " + " ".join(c.source.split())[:width])
            continue
        code = " ⏎ ".join(l for l in c.source.splitlines() if l.strip() and not l.strip().startswith("#"))
        lines.append(f"[{c.index}] CODE: {code[:width]}")
        out = " | ".join(l for l in c.text_output.splitlines() if l.strip() and not INSTALL_NOISE.search(l))
        if out:
            lines.append(f"     -> {out[: width * 2]}")
        if c.images:
            lines.append(f"     -> [{len(c.images)} figure(s)]")
        for err in c.errors:
            lines.append(f"     !! {err[:width]}")
    return "\n".join(lines)
