"""Deterministic probes: decide a checkpoint's state from a trace without any model calls.

Evidence, strongest first:
1. raw tool output (steps[].output) containing the expected value, either a high-specificity value
   or one with name/context support nearby;
2. stage-log outputs whose key names the quantity (pass or fail);
3. stage-log outputs whose value matches, without a name (high-specificity values only);
4. a number labelled by one of the quantity's names in raw output ("n_obs: 194", "U = 20261"):
   fail evidence only, low confidence. `context` patterns only disambiguate found values; they never
   locate the agent's value.

A pass that rests only on the stage log must be corroborated by raw output when the trace has raw
steps; otherwise the node is `unsupported`.

Raw output is read as *results* only: file reads (input contents), file writes (code), numbers that
sit next to file names in listings (sizes, counts in `ls -l` / archive listings), and tool names merely
mentioned in inspection commands (`pip list | grep mafft`) are not evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..graph import Node, Quantity
from ..trace import Step, Trace
from ..values import NUMERIC_TYPES, _decimals, find_in_text, infer_spec, is_low_specificity, matches, scan_numbers

CONTEXT_WINDOW = 160
SNIPPET = 120
RESULT_KINDS = {"exec", "tool_call", "message"}
LABELLED = re.compile(r"\s*(?:\([^)\n]{0,20}\))?\s*[:=]\s*([-−+]?[\d.,]+(?:[eE][-+]?\d+)?%?)")
LS_LONG = re.compile(r"^\s*[dlcbps-][rwxsStT-]{9}[@+.]?\s")
FILE_TOKEN = re.compile(r"(?:\S*/\S+|\S+\.[A-Za-z][A-Za-z0-9]{0,9})$")
INSPECTION = re.compile(
    r"^\s*(?:(?:source|\.)\s+\S+[^;&|]*[;&|]+\s*)*"
    r"(?:grep|egrep|rg|which|whereis|type|command\s+-v|pip3?\s+(?:list|show|freeze)|conda\s+list|apropos|man|ls|find|unzip\s+-l|tar\s+-t|echo)\b"
)


def result_steps(trace: Trace) -> list[Step]:
    return [s for s in trace.steps if s.kind in RESULT_KINDS and s.output]


def _in_listing(text: str, start: int, end: int) -> bool:
    """A number that is a file size/count in a directory or archive listing."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start: line_end if line_end != -1 else len(text)]
    if LS_LONG.match(line):
        return True
    before = text[line_start:start].rstrip(" \t:=")
    after = text[end: line_end if line_end != -1 else len(text)].lstrip(" \t:")
    return bool(FILE_TOKEN.search(before)) or bool(re.match(r"(?:\S*/\S+|\S+\.[A-Za-z][A-Za-z0-9]{0,9})(?:\s|$)", after))


def _imprecise(spec: dict, raw: str) -> bool:
    """A printed value with fewer decimals than a range's bounds (e.g. '1.5' for 1.50-1.54) is weak evidence."""
    if spec["type"] != "range":
        return False
    mant = re.split(r"[eE]|[x×*]\s*10", raw.replace(",", "").strip().rstrip("%"))[0]
    shown = len(mant.split(".")[1]) if "." in mant else 0
    bound = max(_decimals(float(spec["min"])), _decimals(float(spec["max"])))
    return "e" not in raw.lower() and shown < bound


@dataclass
class Evidence:
    source: str
    ref: int | str
    snippet: str

    def to_dict(self) -> dict:
        return {"source": self.source, "ref": self.ref, "snippet": self.snippet}


@dataclass
class QuantityResult:
    name: str
    state: str = "absent"  # pass | fail | absent | unsupported
    observed: Any = None
    confidence: str = "high"
    self_reported: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    named_observations: list[tuple[int, Any, Any]] = field(default_factory=list)  # (entry position, entry tasks, value)
    note: str = ""


def norm_key(key: str) -> str:
    return re.sub(r"[\W_]+", " ", str(key).lower()).strip()


def flatten_outputs(outputs: dict, prefix: str = "") -> Iterator[tuple[str, Any]]:
    for key, value in outputs.items():
        name = f"{prefix} {key}".strip()
        if isinstance(value, dict):
            yield from flatten_outputs(value, name)
        else:
            yield name, value


def _snippet(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - SNIPPET // 2), min(len(text), end + SNIPPET // 2)
    return " ".join(text[lo:hi].split())


def _alias_patterns(q: Quantity) -> list[re.Pattern]:
    pats = []
    for alias in [q.name, *q.aliases]:
        words = [w for w in re.split(r"[\W_]+", alias) if w]
        if words:
            pats.append(re.compile(r"(?<![A-Za-z0-9])" + r"[\s_\-]*".join(map(re.escape, words)) + r"(?![A-Za-z0-9])", re.IGNORECASE))
    return pats


def _context_patterns(q: Quantity) -> list[re.Pattern]:
    return [re.compile(c, re.IGNORECASE) for c in q.context]


def _line_context(text: str, start: int, end: int) -> str:
    """The line holding a hit, plus the previous non-empty line when the hit's line has no words
    (value rows of printed tables and R vectors carry their labels on the line above)."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start: line_end if line_end != -1 else len(text)]
    if not re.search(r"[A-Za-z]{2,}", re.sub(r"\[\d+\]", "", line)):
        before = text[:line_start].rstrip("\n")
        prev = before[before.rfind("\n") + 1:]
        line = prev + "\n" + line
    return line[-CONTEXT_WINDOW * 2:]


def _near(patterns: list[re.Pattern], text: str, start: int, end: int) -> bool:
    context = _line_context(text, start, end)
    return any(p.search(context) for p in patterns)


def _corroborated(value: Any, trace: Trace) -> Evidence | None:
    """Find a self-reported scalar value in the raw tool output."""
    if isinstance(value, (dict, list)) or value is None:
        return None
    spec = infer_spec(str(value))
    if spec["type"] == "number":  # accept the value printed with more or fewer digits
        spec = {"type": "number", "value": spec["value"], "rel_tol": 5e-3}
    for step in result_steps(trace):
        hits = [h for h in find_in_text(spec, step.output) if not _in_listing(step.output, h.start, h.end)]
        if hits:
            return Evidence("step", step.index, _snippet(step.output, hits[0].start, hits[0].end))
    return None


def probe_quantity(q: Quantity, trace: Trace, task_ids: list[str], require_context: bool = False) -> QuantityResult:
    """`require_context`: the expected value is shared with another checkpoint of the graph, so a bare
    occurrence cannot tell them apart; accept it only with name/context support."""
    res = QuantityResult(q.name)
    spec = q.expected
    low = require_context or ((q.specificity == "low") if q.specificity else is_low_specificity(spec))
    names = {norm_key(n) for n in [q.name, *q.aliases]}
    aliases, contexts = _alias_patterns(q), _context_patterns(q)

    raw_pass: list[tuple[Any, Evidence]] = []
    raw_fail: list[tuple[Any, Evidence]] = []
    if trace.capabilities.get("raw_steps"):
        for step in result_steps(trace):
            text = step.output
            for hit in find_in_text(spec, text):
                if _in_listing(text, hit.start, hit.end):
                    continue
                needs_context = low or _imprecise(spec, hit.raw)
                if not needs_context or _near(contexts + aliases, text, hit.start, hit.end):
                    raw_pass.append((hit.value, Evidence("step", step.index, _snippet(text, hit.start, hit.end))))
            if spec["type"] in NUMERIC_TYPES:
                # the agent's own value for this quantity: one of its names directly labelling a number
                for pat in aliases:
                    for m in pat.finditer(text):
                        label = LABELLED.match(text, m.end())
                        if not label:
                            continue
                        nums = scan_numbers(label.group(1))
                        if nums and not matches(spec, nums[0].raw):
                            raw_fail.append((nums[0].value, Evidence("step", step.index, _snippet(text, m.start(), label.end()))))

    named_pass, named_fail, loose_pass = [], [], []
    for entry in trace.stage_log:
        if not entry.serves(task_ids):
            continue
        for key, value in flatten_outputs(entry.outputs):
            ev = Evidence("stage_log", entry.position, f"{entry.action} | {key} = {value}"[:SNIPPET * 2])
            if norm_key(key) in names:
                res.named_observations.append((entry.position, entry.tasks, value))
                (named_pass if matches(spec, value) else named_fail).append((value, ev))
            elif matches(spec, value) and (not low or any(p.search(f"{key} {entry.action}") for p in contexts + aliases)):
                loose_pass.append((value, ev))

    if raw_pass:
        res.state, res.evidence = "pass", [ev for _, ev in raw_pass[:3]] + [ev for _, ev in named_pass[:1]]
        res.observed = named_pass[0][0] if named_pass else raw_pass[0][0]
        if named_fail and not named_pass:
            res.confidence, res.note = "low", f"stage log reports {named_fail[0][0]!r} but raw output shows the expected value"
        elif low:
            res.confidence = "low"
    elif named_pass or loose_pass:
        value, ev = (named_pass or loose_pass)[0]
        res.observed, res.evidence = value, [ev]
        if not named_pass or low:
            res.confidence = "low"
        if trace.capabilities.get("raw_steps"):
            support = _corroborated(value, trace)
            if support:
                res.state = "pass"
                res.evidence.append(support)
            else:
                res.state, res.note = "unsupported", "stage-log value not found in any tool output"
        else:
            res.state, res.self_reported = "pass", True
    elif named_fail:
        res.state = "fail"
        res.observed, res.evidence = named_fail[0][0], [ev for _, ev in named_fail[:3]]
    elif raw_fail:
        res.state, res.confidence = "fail", "low"
        res.observed, res.evidence = raw_fail[0][0], [ev for _, ev in raw_fail[:3]]
        res.note = "value labelled with the quantity's name in raw output"
    return res


def probe_patterns(node: Node, trace: Trace, task_ids: list[str]) -> Evidence | None:
    """Evidence a semantic node by regexes over agent code/commands and stage-log actions."""
    patterns = [re.compile(p, re.IGNORECASE) for p in node.probe.get("patterns", [])]
    if not patterns:
        return None
    for step in trace.steps:
        if step.kind == "file_read" or (step.kind == "exec" and INSPECTION.match(step.input or "")):
            continue  # reading inputs or looking tools up is not performing the step
        texts = [step.input or ""] + ([step.output or ""] if step.kind == "message" else [])
        for text in texts:
            for pat in patterns:
                m = pat.search(text)
                if m:
                    return Evidence("pattern", step.index, _snippet(text, m.start(), m.end()))
    for entry in trace.stage_log:
        if entry.serves(task_ids):
            text = f"{entry.action} {entry.params}"
            for pat in patterns:
                m = pat.search(text)
                if m:
                    return Evidence("pattern", f"stage_log:{entry.position}", _snippet(text, m.start(), m.end()))
    return None
