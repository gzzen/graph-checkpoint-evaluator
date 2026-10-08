"""Pluggable LLM judge for semantic checkpoints (off by default).

A judge receives one node and a short list of candidate evidence snippets from the trace, and
returns a verdict. Built-in: `deepseek[:model]`, configured through environment variables or a
`.env` file (see `.env.example`). Any other provider can be plugged in with
`path/to/judge.py[:Class]` exposing `judge(node, candidates) -> Verdict | None`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..env import load_env
from ..graph import Node
from ..trace import Trace

MAX_CANDIDATES = 12
MAX_SNIPPET = 1500
STATES = ("pass", "fail", "absent")


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
        if step.kind == "file_read":
            continue
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


SYSTEM = "You are a careful reviewer of data-analysis runs. You answer with a single JSON object."

PROMPT = """Check whether an AI agent's analysis performed one specific step of a reference analysis.

Reference step ({stage_type}): {description}
Parameters of the reference step: {params}

Below are excerpts from the agent's run (code it ran, outputs, and its own step log), each with a ref.
Decide:
- "pass": the excerpts show the agent performed this step as described (an equivalent method with the same effect counts);
- "fail": the agent performed this step but materially differently (different design, threshold, subset or test);
- "absent": the excerpts do not show this step.
Judge only from the excerpts.

Reply with one JSON object and nothing else:
{{"state": "pass" | "fail" | "absent", "ref": "<ref of the most decisive excerpt, or null>", "rationale": "<one sentence>"}}

{candidates}"""


def parse_verdict(text: str) -> Verdict | None:
    """Verdict from a model reply: plain JSON, fenced JSON, or the first {...} object in the text."""
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    braces = re.search(r"\{.*\}", text, re.DOTALL)
    if braces:
        candidates.append(braces.group(0))
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("state") in STATES:
            ref = data.get("ref")
            return Verdict(data["state"], None if ref in (None, "", "null") else str(ref), str(data.get("rationale", "")))
    return None


class DeepSeekJudge:
    """DeepSeek chat-completions API (OpenAI-compatible wire format), standard library only.

    Settings (environment variables, typically from `.env`; explicit exports win over the file):
      DEEPSEEK_API_KEY           required
      DEEPSEEK_MODEL             default deepseek-flash
      DEEPSEEK_BASE_URL          default https://api.deepseek.com/v1
      DEEPSEEK_REASONING_EFFORT  none (default: thinking disabled) | low | medium | high | max
      GCE_JUDGE_CACHE            optional directory caching verdicts by prompt hash
      GCE_JUDGE_TIMEOUT          seconds per request, default 120
    """

    def __init__(self, model: str | None = None, effort: str | None = None):
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set; copy .env.example to .env and fill it in, or export it")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash"
        self.effort = (effort or os.environ.get("DEEPSEEK_REASONING_EFFORT") or "none").lower()
        if self.effort not in {"none", "low", "medium", "high", "max"}:
            raise ValueError("DEEPSEEK_REASONING_EFFORT must be none, low, medium, high or max")
        self.base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
        self.timeout = float(os.environ.get("GCE_JUDGE_TIMEOUT") or 120)
        cache = os.environ.get("GCE_JUDGE_CACHE")
        self.cache_dir = Path(cache) if cache else None
        self.name = f"deepseek:{self.model}"
        self._memo: dict[str, Verdict | None] = {}

    def judge(self, node: Node, candidates: list[Candidate]) -> Verdict | None:
        if not candidates:
            return Verdict("absent", None, "no candidate excerpts")
        prompt = PROMPT.format(
            stage_type=node.stage_type,
            description=node.description,
            params=json.dumps(node.params, sort_keys=True),
            candidates="\n\n".join(f"[{c.ref}]\n{c.text}" for c in candidates),
        )
        key = hashlib.sha256(json.dumps([self.model, self.effort, SYSTEM, prompt]).encode()).hexdigest()
        if key not in self._memo:
            self._memo[key] = self._cached(key) or self._call(prompt, key)
        return self._memo[key]

    # ---- cache ----------------------------------------------------------------------------------
    def _cached(self, key: str) -> Verdict | None:
        if self.cache_dir and (self.cache_dir / f"{key}.json").is_file():
            data = json.loads((self.cache_dir / f"{key}.json").read_text(encoding="utf-8"))
            return Verdict(data["state"], data.get("ref"), data.get("rationale", ""))
        return None

    def _store(self, key: str, verdict: Verdict) -> None:
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {"state": verdict.state, "ref": verdict.ref, "rationale": verdict.rationale, "model": self.model}
            (self.cache_dir / f"{key}.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    # ---- API ------------------------------------------------------------------------------------
    def request_body(self, prompt: str, json_mode: bool = True) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "max_tokens": 1024 if self.effort == "none" else 8192,
            "thinking": {"type": "disabled" if self.effort == "none" else "enabled"},
        }
        if self.effort == "none":
            body["temperature"] = 0
        else:
            body["reasoning_effort"] = self.effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _call(self, prompt: str, key: str) -> Verdict | None:
        json_mode = True
        for attempt in range(4):
            try:
                reply = self._post(self.request_body(prompt, json_mode))
            except urllib.error.HTTPError as exc:
                if exc.code == 400 and json_mode:  # the model may not support JSON mode; the prompt still asks for JSON
                    json_mode = False
                    continue
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"DeepSeek API error {exc.code}: {exc.read()[:300]!r}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"DeepSeek API unreachable: {exc}") from exc
            choices = reply.get("choices") or []
            content = (choices[0].get("message") or {}).get("content") if choices else None
            verdict = parse_verdict(content or "")
            if verdict is not None:
                self._store(key, verdict)
            return verdict
        return None


def load_judge(spec: str | None, env_file: str | Path | None = None) -> Judge | None:
    """'none' / None -> no judge; 'deepseek[:model]'; or 'path/to/judge.py[:Class]'."""
    if not spec or spec == "none":
        return None
    if spec == "deepseek" or spec.startswith("deepseek:"):
        load_env(env_file)
        _, _, model = spec.partition(":")
        return DeepSeekJudge(model or None)
    path, _, cls_name = spec.partition(":")
    mod_spec = importlib.util.spec_from_file_location(f"gce_judge_{Path(path).stem}", Path(path).resolve())
    if mod_spec is None or mod_spec.loader is None:
        raise ImportError(f"cannot load judge {spec!r}")
    module = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(module)
    return getattr(module, cls_name or "Judge")()
