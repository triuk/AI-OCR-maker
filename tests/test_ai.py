"""Seam 1: AI requests of steps, the provider and model settings, diagnostics and the Check AI action."""
from __future__ import annotations

import stat
import sys

import pytest

from fakes import ScriptedProvider
from ocrmaker.claude import ClaudeProvider
from ocrmaker.codex import CodexProvider
from ocrmaker.language import INVALID_OUTPUT, USAGE_LIMIT, LanguageError
from ocrmaker.step import Step
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow

SCHEMA = {"type": "object", "properties": {"order": {"type": "array"}}, "required": ["order"]}


class AskingStep(Step):
    """Asks the AI once with an image and stores the parsed answer."""

    id = "order"

    def __init__(self, *, web_search=False):
        self.web_search = web_search

    def run(self, ctx):
        image = ctx.output.path("preview.png")
        image.write_bytes(b"\x89PNG fake preview")
        answer = ctx.language.ask("Order the numbered blocks.", {"page": 0, "blocks": [1, 2]}, schema=SCHEMA,
                                  images=[image], web_search=self.web_search, purpose="example")
        ctx.output.write_json("result.json", {"order": answer.data["order"], "provider": answer.provider,
                                              "model": answer.model})


@pytest.fixture
def step_implementations():
    return {"order": AskingStep()}


@pytest.fixture
def project(workflow, source_pdf):
    return workflow.create(source_pdf)


def ai_records(workflow, project_id):
    return [r for r in workflow.diagnostics(project_id) if r["kind"] == "ai_request"]


def test_a_new_project_asks_claude_with_its_default_model(workflow, project):
    assert project["settings"]["ai_provider"] == "claude"
    assert project["settings"]["ai_model"] is None


def test_a_step_gets_the_parsed_answer_and_the_request_is_recorded(workflow, project, language_provider):
    language_provider.on("example", {"order": [2, 1]})
    workflow.run(project["id"])
    assert workflow.result(project["id"], "order").read_json("result.json") == {
        "order": [2, 1], "provider": "claude", "model": "scripted-model"}
    record, = ai_records(workflow, project["id"])
    assert record["step"] == "order" and record["purpose"] == "example"
    assert record["provider"] == "claude" and record["model"] == "scripted-model"
    assert record["instructions"] == "Order the numbered blocks."
    assert '"blocks":[1,2]' in record["prompt"]
    assert record["response"] == '{"order": [2, 1]}'
    assert record["outcome"] == "ok" and record["duration"] >= 0
    assert record["usage"] == {"input": 1200, "cached_input": 1000, "output": 80, "reasoning": None, "total": 1280,
                               "uncached_input": 200}
    assert record["images"][0]["bytes"] == len(b"\x89PNG fake preview")
    assert language_provider.requests_for("example")[0].images == [b"\x89PNG fake preview"]


@pytest.mark.parametrize("language_provider", [ScriptedProvider(usage=None)])
def test_unknown_usage_is_recorded_as_unknown_never_as_zero(workflow, project, language_provider):
    language_provider.on("example", {"order": []})
    workflow.run(project["id"])
    record, = ai_records(workflow, project["id"])
    assert record["usage"] is None


def test_the_project_choice_of_provider_and_model_reaches_the_request(workflow, project, language_provider,
                                                                      monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex-cache"))
    workflow.update_settings(project["id"], {"ai_provider": "codex", "ai_model": "gpt-6-astra"})
    language_provider.on("example", {"order": [1, 2]})
    workflow.run(project["id"])
    record, = ai_records(workflow, project["id"])
    assert (record["provider"], record["model"]) == ("codex", "gpt-6-astra")
    assert language_provider.requests[0].model == "gpt-6-astra"


def test_the_project_reasoning_effort_reaches_the_request(workflow, project, language_provider):
    assert project["settings"]["ai_effort"] == "medium"
    workflow.update_settings(project["id"], {"ai_effort": "high"})
    language_provider.on("example", {"order": [1, 2]})
    workflow.run(project["id"])
    record, = ai_records(workflow, project["id"])
    assert record["effort"] == "high"
    assert language_provider.requests[0].effort == "high"


def test_settings_refuse_an_unknown_reasoning_effort(workflow, project):
    with pytest.raises(ValueError, match="úroveň uvažování"):
        workflow.update_settings(project["id"], {"ai_effort": "ultra"})


def test_a_failed_request_fails_the_step_with_its_czech_message_and_is_recorded(workflow, project,
                                                                                 language_provider):
    language_provider.on("example", LanguageError("Limit předplatného Claude je vyčerpaný. Zkuste to později.",
                                                        code=USAGE_LIMIT, usage={"input": 5, "cached_input": 0,
                                                                                 "output": 0}))
    workflow.run(project["id"])
    step = next(s for s in workflow.get(project["id"])["steps"] if s["id"] == "order")
    assert step["state"] == "failed"
    assert step["message"] == "Limit předplatného Claude je vyčerpaný. Zkuste to později."
    record, = ai_records(workflow, project["id"])
    assert record["outcome"] == "failed" and record["error_code"] == USAGE_LIMIT
    assert record["usage"]["input"] == 5


def test_an_answer_that_is_not_json_is_an_invalid_output(workflow, project, language_provider):
    language_provider.on("example", "Sorry, I cannot see the page.")
    workflow.run(project["id"])
    record, = ai_records(workflow, project["id"])
    assert record["error_code"] == INVALID_OUTPUT
    assert record["response"] == "Sorry, I cannot see the page."


def test_settings_refuse_an_unknown_provider_or_a_model_of_another_provider(workflow, project):
    with pytest.raises(ValueError, match="Neznámý poskytovatel AI"):
        workflow.update_settings(project["id"], {"ai_provider": "gemini"})
    with pytest.raises(ValueError, match="k dispozici"):
        workflow.update_settings(project["id"], {"ai_provider": "claude", "ai_model": "gpt-6-astra"})


def test_switching_the_provider_returns_to_its_default_model(workflow, project):
    workflow.update_settings(project["id"], {"ai_model": "opus"})
    updated = workflow.update_settings(project["id"], {"ai_provider": "codex"})
    assert updated["settings"]["ai_model"] is None


def test_check_ai_sends_a_tiny_request_and_reports_who_answered(workflow, project, language_provider):
    language_provider.on("check", {"ok": True})
    result = workflow.check_ai(project["id"])
    assert result["ok"] is True
    assert (result["provider"], result["model"]) == ("claude", "scripted-model")
    assert "odpověděl" in result["message"]
    record, = ai_records(workflow, project["id"])
    assert record["purpose"] == "check" and record["outcome"] == "ok"


def test_check_ai_can_try_an_unsaved_choice(workflow, project, language_provider):
    language_provider.on("check", {"ok": True})
    result = workflow.check_ai(project["id"], {"ai_model": "haiku"})
    assert result["model"] == "haiku"
    assert workflow.get(project["id"])["settings"]["ai_model"] is None


def test_check_ai_shows_the_czech_error(workflow, project, language_provider):
    language_provider.on("check", LanguageError("Claude není přihlášený k předplatnému.", code="unauthorized"))
    result = workflow.check_ai(project["id"])
    assert result == result | {"ok": False, "code": "unauthorized", "message": "Claude není přihlášený k předplatnému."}


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_check_ai_with_a_missing_cli_says_what_to_install(provider, library, source_pdf, monkeypatch, tmp_path):
    monkeypatch.setenv("OCRMAKER_" + provider.upper(), str(tmp_path / "nowhere" / provider))
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    workflow = Workflow(ProjectStore(library), steps={}, language={"claude": ClaudeProvider(),
                                                                   "codex": CodexProvider()})
    project = workflow.create(source_pdf, settings={"ai_provider": provider})
    result = workflow.check_ai(project["id"])
    assert result["ok"] is False and result["code"] == "missing_cli"
    assert f"příkaz „{provider}“" in result["message"] and "není nainstalovaný" in result["message"]


FAKE_LIMITED_CLAUDE = r'''#!{python}
import json, sys
if sys.argv[1:3] == ["auth", "status"]:
    print('{{"loggedIn": true, "authMethod": "claude.ai"}}')
    sys.exit(0)
sys.stdin.read()
print(json.dumps({{"type": "result", "is_error": True, "result": "Claude AI usage limit reached|4102444800"}}))
sys.exit(1)
'''


def test_check_ai_reports_a_reached_usage_limit_with_its_reset_time(library, source_pdf, monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_LIMITED_CLAUDE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("OCRMAKER_CLAUDE", str(fake))
    workflow = Workflow(ProjectStore(library), steps={}, language={"claude": ClaudeProvider()})
    project = workflow.create(source_pdf)
    result = workflow.check_ai(project["id"])
    assert result["code"] == "usage_limit"
    assert result["message"].startswith("Limit předplatného Claude je vyčerpaný. Obnoví se ")
    assert "2100" in result["message"]


def test_without_any_provider_a_check_explains_ai_is_unavailable(library, source_pdf):
    workflow = Workflow(ProjectStore(library), steps={})
    project = workflow.create(source_pdf)
    result = workflow.check_ai(project["id"])
    assert result["ok"] is False and result["code"] == "unavailable_provider"
