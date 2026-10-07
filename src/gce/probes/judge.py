"""Pluggable LLM judge for semantic checkpoints (off by default).

A judge receives one node and a short list of candidate evidence snippets from the trace, and
returns a verdict. Built-in: `anthropic[:model]`. Any other provider can be plugged in with
`path/to/judge.py[:Class]` exposing `judge(node, candidates) -> Verdict | None`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..graph import Node
from ..trace import Trace

DEFAULT_MODEL = "claude-opus-5-5"
MAX_CANDIDATES = 12
MAX_SNIPPET = 1500


@dataclass
class Candidate:
    ref: str  # "step:<i>" or "stage_log:<i>"
    text: str


@dataclass
class Verdict:
    state: str  # pass | fail | absent
    ref: str | None
    rationale: str


class Judge(Protocol):
    name: str

    def judge(self, node: Node, candidates: list[Candidate]) -> Verdict | None: ...


def select_candidates(node: Node, trace: Trace, task_ids: list[str], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Rank code/commands and stage-log entries by word overlap with the node description and params."""
    words = {w for w in re.findall(r"[a-z][a-z0-9_]{2,}", (node.description + " " + json.dumps(node.params)).lower())}
    scored = []
    for step in trace.steps:
        text = "\n".join(t for t in (step.input, (step.output or "")[:800]) if t)
        if text:
            overlap = len(words & set(re.findall(r"[a-z][a-z0-9_]{2,}", text.lower())))
            scored.append((overlap, f"step:{step.index}", text[:MAX_SNIPPET]))
    for entry in trace.stage_log:
        if entry.serves(task_ids):
            text = json.dumps({"action": entry.action, "params": entry.params, "outputs": entry.outputs}, default=str)
            overlap = len(words & set(re.findall(r"[a-z][a-z0-9_]{2,}", text.lower())))
            scored.append((overlap + 1, f"stage_log:{entry.position}", text[:MAX_SNIPPET]))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [Candidate(ref, text) for score, ref, text in scored[:limit] if score > 0]


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["pass", "fail", "absent"]},
        "ref": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
    },
    "required": ["state", "ref", "rationale"],
    "additionalProperties": False,
}

PROMPT = """You check whether an AI agent's analysis performed one specific step of a reference analysis.

Reference step ({stage_type}): {description}
Parameters of the reference step: {params}

Below are excerpts from the agent's run (code it ran, outputs, and its own step log), each with a ref.
Decide:
- "pass": the excerpts show the agent performed this step as described (an equivalent method with the same effect counts);
- "fail": the agent performed this step but materially differently (different design, threshold, subset or test);
- "absent": the excerpts do not show this step.
Judge only from the excerpts. Give the ref of the most decisive excerpt (or null) and a one-sentence rationale.

{candidates}"""


class AnthropicJudge:
    """Claude via the Anthropic SDK, with JSON-schema structured output and an in-memory cache."""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "low"):
        import anthropic  # optional dependency: pip install 'graph-checkpoint-evaluator[llm]'

        self.client = anthropic.Anthropic()
        self.model, self.effort = model, effort
        self.name = f"anthropic:{model}"
        self._cache: dict[str, Verdict | None] = {}

    def judge(self, node: Node, candidates: list[Candidate]) -> Verdict | None:
        if not candidates:
            return Verdict("absent", None, "no candidate excerpts")
        prompt = PROMPT.format(
            stage_type=node.stage_type,
            description=node.description,
            params=json.dumps(node.params, sort_keys=True),
            candidates="\n\n".join(f"[{c.ref}]\n{c.text}" for c in candidates),
        )
        key = hashlib.sha256(f"{self.model}\n{prompt}".encode()).hexdigest()
        if key not in self._cache:
            self._cache[key] = self._call(prompt)
        return self._cache[key]

    def _call(self, prompt: str) -> Verdict | None:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            return None
        data = json.loads(text)
        return Verdict(data["state"], data.get("ref"), data.get("rationale", ""))


def load_judge(spec: str | None) -> Judge | None:
    """'none' / None -> no judge; 'anthropic[:model]'; or 'path/to/judge.py[:Class]'."""
    if not spec or spec == "none":
        return None
    if spec == "anthropic" or spec.startswith("anthropic:"):
        _, _, model = spec.partition(":")
        return AnthropicJudge(model or DEFAULT_MODEL)
    path, _, cls_name = spec.partition(":")
    mod_spec = importlib.util.spec_from_file_location(f"gce_judge_{Path(path).stem}", Path(path).resolve())
    if mod_spec is None or mod_spec.loader is None:
        raise ImportError(f"cannot load judge {spec!r}")
    module = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(module)
    return getattr(module, cls_name or "Judge")()
