"""DeepSeek judge and .env loading; the HTTP call is stubbed, so no network or real key is used."""
import io
import json
import urllib.error

import pytest

from gce.env import load_env, parse_env
from gce.graph import Node
from gce.probes.judge import Candidate, DeepSeekJudge, load_judge, parse_verdict

ENV_KEYS = ["DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_REASONING_EFFORT", "GCE_JUDGE_CACHE", "GCE_JUDGE_TIMEOUT"]
NODE = Node("md", "model", "Fit ~ Replicate + Strain + Media", params={"design": "~ Replicate + Strain + Media"})
CANDS = [Candidate("step:3", "dds <- DESeqDataSetFromMatrix(..., design = ~ Replicate + Strain + Media)")]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_parse_env_handles_quotes_comments_and_export():
    text = '# comment\nexport DEEPSEEK_API_KEY="sk-abc"\nDEEPSEEK_MODEL=deepseek-flash  # inline\nEMPTY=\nBAD LINE\nQ=\'a # b\'\n'
    assert parse_env(text) == {"DEEPSEEK_API_KEY": "sk-abc", "DEEPSEEK_MODEL": "deepseek-flash", "EMPTY": "", "Q": "a # b"}


def test_load_env_does_not_override_exports(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=from-file\nDEEPSEEK_MODEL=from-file\n")
    monkeypatch.setenv("DEEPSEEK_MODEL", "exported")
    assert load_env(env) == env
    import os
    assert os.environ["DEEPSEEK_API_KEY"] == "from-file"
    assert os.environ["DEEPSEEK_MODEL"] == "exported"


def test_missing_key_is_a_clear_error(tmp_path):
    empty = tmp_path / "empty.env"
    empty.write_text("DEEPSEEK_MODEL=deepseek-flash\n")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        load_judge("deepseek", env_file=empty)


@pytest.mark.parametrize("text, state", [
    ('{"state": "pass", "ref": "step:3", "rationale": "design matches"}', "pass"),
    ('Here you go:\n```json\n{"state": "fail", "ref": null, "rationale": "Student t"}\n```', "fail"),
    ('Verdict {"state": "absent", "ref": "", "rationale": "not shown"} done', "absent"),
])
def test_parse_verdict_variants(text, state):
    verdict = parse_verdict(text)
    assert verdict.state == state


def test_parse_verdict_rejects_unknown_state():
    assert parse_verdict('{"state": "maybe"}') is None


def test_request_body_and_verdict(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"DEEPSEEK_API_KEY=sk-test\nGCE_JUDGE_CACHE={tmp_path / 'cache'}\n")
    judge = load_judge("deepseek:deepseek-flash", env_file=env)
    assert judge.name == "deepseek:deepseek-flash"
    sent = []
    monkeypatch.setattr(judge, "_post", lambda body: sent.append(body) or reply('{"state": "pass", "ref": "step:3", "rationale": "ok"}'))
    verdict = judge.judge(NODE, CANDS)
    assert (verdict.state, verdict.ref) == ("pass", "step:3")
    body = sent[0]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"} and body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert "json" in body["messages"][1]["content"].lower()  # JSON mode requires the word in the prompt
    # memoised in-process and cached on disk: a fresh judge reads the cache without calling the API
    assert judge.judge(NODE, CANDS) == verdict and len(sent) == 1
    fresh = DeepSeekJudge("deepseek-flash")
    monkeypatch.setattr(fresh, "_post", lambda body: pytest.fail("should use the cache"))
    assert fresh.judge(NODE, CANDS).state == "pass"


def test_thinking_effort_body(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "high")
    body = DeepSeekJudge().request_body("prompt")
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high" and "temperature" not in body


def test_falls_back_without_json_mode_on_400(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    judge = DeepSeekJudge()
    calls = []

    def post(body):
        calls.append("response_format" in body)
        if "response_format" in body:
            raise urllib.error.HTTPError("u", 400, "bad request", {}, io.BytesIO(b"unsupported"))
        return reply('{"state": "fail", "ref": "step:3", "rationale": "different design"}')

    monkeypatch.setattr(judge, "_post", post)
    assert judge.judge(NODE, CANDS).state == "fail"
    assert calls == [True, False]


def test_no_candidates_needs_no_call(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    judge = DeepSeekJudge()
    monkeypatch.setattr(judge, "_post", lambda body: pytest.fail("no call expected"))
    assert judge.judge(NODE, []).state == "absent"
