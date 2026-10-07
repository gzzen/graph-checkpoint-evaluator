"""Turn a capsule notebook into a runnable, instrumented script.

A re-run spec (`reruns/<sid>.yaml`) controls the conversion:

    language: R | python
    drop_cells: [5]                    # template examples, installs, plotting-only cells
    patches:                           # literal replacements; each must apply exactly once
      - {cell: 13, find: "...", replace: "...", why: "..."}
    insert_after:                      # instrumentation placed after a cell
      20: 'gce_checkpoint("n_disp_lt_1e5", sum(mcols(dds)$dispGeneEst < 1e-5, na.rm=TRUE), cell=20)'
    instrument: |                      # instrumentation appended at the end
      gce_checkpoint("n_deg_97", nrow(sig_1vs97), cell=23)
    anchors:                           # values the notebook printed; the re-run must reproduce them
      - {name: n_deg_97, expected: {type: integer, value: 2091}, source: "cell 24 Venn diagram"}
    timeout: 1800

Instrumentation calls `gce_checkpoint(name, value, cell)`, which prints a `GCE_CHECKPOINT {json}` line.
"""
from __future__ import annotations

import re
from pathlib import Path

from .notebooks import Cell

SKIP_LINE = re.compile(
    r"^\s*(install\.packages\(|BiocManager::install\(|!pip|%pip|!conda|%load_ext|%%R|%%capture|import condacolab|condacolab\.)"
)

R_PRELUDE = """\
.libPaths(c({r_library!r}, .libPaths()))
suppressPackageStartupMessages(library(jsonlite))
gce_checkpoint <- function(name, value, cell = NA) {{
  cat("\\nGCE_CHECKPOINT", jsonlite::toJSON(list(name = name, value = value, cell = cell),
      auto_unbox = TRUE, digits = NA, na = "null", null = "null"), "\\n")
}}
pdf(NULL)
setwd({data_dir!r})
options(width = 200)
"""

PY_PRELUDE = """\
import json as _gce_json, os as _gce_os
import matplotlib as _gce_mpl
_gce_mpl.use("Agg")
def _gce_default(obj):
    for attr in ("item", "tolist"):
        if hasattr(obj, attr):
            return getattr(obj, attr)()
    return str(obj)
def gce_checkpoint(name, value, cell=None):
    print("\\nGCE_CHECKPOINT " + _gce_json.dumps({{"name": name, "value": value, "cell": cell}}, default=_gce_default), flush=True)
try:
    display
except NameError:
    display = print
_gce_os.chdir({data_dir!r})
"""


class PatchError(ValueError):
    pass


def _strip(source: str) -> str:
    lines = [l for l in source.splitlines() if not SKIP_LINE.match(l)]
    text = "\n".join(lines)
    # R idiom: if (!requireNamespace("x", quietly = TRUE)) { install... } becomes an empty block
    return re.sub(r"if \(!require(?:Namespace)?\([^)]*\)[^{\n]*\{\s*\}", "", text)


def build_script(cells: list[Cell], spec: dict, data_dir: Path, r_library: Path) -> tuple[str, list[str]]:
    """Return (script text, log of applied patches)."""
    language = spec["language"]
    drop = set(spec.get("drop_cells", []))
    patches = spec.get("patches", [])
    insert_after = {int(k): v for k, v in (spec.get("insert_after") or {}).items()}
    applied: list[str] = []

    sources = {c.index: c.source for c in cells if c.kind == "code"}
    for patch in patches:
        src = sources.get(patch["cell"], "")
        if src.count(patch["find"]) != 1:
            raise PatchError(f"patch for cell {patch['cell']} must match exactly once: {patch['find'][:60]!r}")
        sources[patch["cell"]] = src.replace(patch["find"], patch["replace"])
        applied.append(f"cell {patch['cell']}: {patch.get('why', 'patched')}")

    prelude = (R_PRELUDE if language == "R" else PY_PRELUDE).format(r_library=str(r_library), data_dir=str(data_dir))
    parts = [prelude]
    for index in sorted(sources):
        if index in drop:
            continue
        body = _strip(sources[index])
        if language == "python" and "%%bash" in body:
            applied.append(f"cell {index}: dropped (bash cell)")
            continue
        marker = f'cat("\\n##### CELL {index}\\n")' if language == "R" else f'print("\\n##### CELL {index}")'
        parts.append(f"{marker}\n{body}")
        if index in insert_after:
            parts.append(insert_after[index])
    if spec.get("instrument"):
        parts.append(spec["instrument"])
    return "\n\n".join(parts) + "\n", applied
