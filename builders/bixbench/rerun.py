"""Re-run an instrumented capsule notebook and record its checkpoint values as evidence."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import yaml

from gce.values import matches

from .config import HERE, Config
from .extract import build_script
from .notebooks import load_cells
from .questions import by_capsule, load_questions

EVIDENCE_DIR = HERE / "evidence"
SPEC_DIR = HERE / "reruns"


def parse_checkpoints(log: str) -> dict[str, dict]:
    found = {}
    for line in log.splitlines():
        if line.startswith("GCE_CHECKPOINT"):
            payload = json.loads(line[len("GCE_CHECKPOINT"):].strip())
            found[payload["name"]] = {"value": payload["value"], "cell": payload.get("cell")}
    return found


def environment_info(cfg: Config, language: str) -> dict:
    if language == "R":
        code = ('.libPaths(c("%s", .libPaths())); cat(R.version.string, "\\n"); '
                'for (p in c("DESeq2","apeglm","clusterProfiler","tidyverse","rstatix","multcomp")) '
                'if (requireNamespace(p, quietly=TRUE)) cat(p, as.character(packageVersion(p)), "\\n")') % cfg.r_library
        out = subprocess.run([cfg.rscript, "-e", code], capture_output=True, text=True).stdout
        lines = [l.strip() for l in out.strip().splitlines()]
        return {"r": lines[0] if lines else None, "packages": dict(l.split(" ", 1) for l in lines[1:] if " " in l)}
    out = subprocess.run([str(cfg.python), "-c", "import sys,importlib.metadata as m;print(sys.version.split()[0]);"
                          "[print(p, m.version(p)) for p in ('pandas','numpy','scipy','statsmodels','pydeseq2','gseapy') if __import__('importlib.util').util.find_spec(p.replace('-','_'))]"],
                         capture_output=True, text=True).stdout
    lines = [l.strip() for l in out.strip().splitlines()]
    return {"python": lines[0] if lines else None, "packages": dict(l.split(" ", 1) for l in lines[1:] if " " in l)}


def rerun(sid: str, cfg: Config) -> dict:
    spec = yaml.safe_load((SPEC_DIR / f"{sid}.yaml").read_text(encoding="utf-8"))
    rows = by_capsule(load_questions(cfg.questions))[sid]
    capsule = rows[0]["capsule_uuid"]
    cells = load_cells(cfg.notebook_dir(capsule))
    script, applied = build_script(cells, spec, cfg.data_dir(capsule), cfg.r_library)

    work = cfg.work / sid
    work.mkdir(parents=True, exist_ok=True)
    suffix = ".R" if spec["language"] == "R" else ".py"
    script_path = work / f"script{suffix}"
    script_path.write_text(script, encoding="utf-8")
    cmd = [cfg.rscript, str(script_path)] if spec["language"] == "R" else [str(cfg.python), str(script_path)]
    env = {**os.environ, "R_LIBS_USER": str(cfg.r_library), "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg"}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=spec.get("timeout", 1800), env=env, cwd=work)
    log = proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
    (work / "log.txt").write_text(log, encoding="utf-8")

    checkpoints = parse_checkpoints(proc.stdout)
    anchors = []
    for anchor in spec.get("anchors", []):
        got = checkpoints.get(anchor["name"], {}).get("value")
        anchors.append({**anchor, "observed": got, "ok": got is not None and matches(anchor["expected"], got)})
    evidence = {
        "sid": sid,
        "capsule_uuid": capsule,
        "date": dt.date.today().isoformat(),
        "returncode": proc.returncode,
        "environment": environment_info(cfg, spec["language"]),
        "patches": applied,
        "checkpoints": checkpoints,
        "anchors": anchors,
        "anchors_ok": all(a["ok"] for a in anchors),
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / f"{sid}.json").write_text(json.dumps(evidence, indent=1, default=str) + "\n", encoding="utf-8")
    return evidence
