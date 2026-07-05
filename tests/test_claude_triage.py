"""Tests for ClaudeTriageAgent with an injected fake AI backend (no real provider, BE-0104)."""

from __future__ import annotations

from typing import Any

from conftest import FakeBackend, FakeBlock

from bajutsu.ai.base import AnyTool, ImagePart, TextPart
from bajutsu.claude_triage import ClaudeTriageAgent, _render
from bajutsu.drivers import base
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact
from bajutsu.triage import FailedStep, TriageContext


def _el(identifier: str, label: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _ctx(**over: Any) -> TriageContext:
    base_ctx = {
        "scenario": "s",
        "failure": "step0 tap: 一致なし",
        "failed_step": FailedStep(0, "tap", "一致なし: home.titel"),
        "failed_expectations": [],
        "elements": [_el("home.title", "Home")],
        "scenario_yaml": "- name: s\n  steps:\n    - tap: { id: home.titel }\n",
        "target_id": "home.titel",
        "evidence": ["deviceLog"],
    }
    base_ctx.update(over)
    return TriageContext(**base_ctx)


def _text_of(backend: FakeBackend) -> str:
    return next(p.text for p in backend.requests[0].messages[0].content if isinstance(p, TextPart))


def _diagnose(
    category: str = "selector", summary: str = "id renamed", suggestions: list[str] | None = None
) -> FakeBlock:
    return FakeBlock(
        "diagnose",
        {
            "category": category,
            "summary": summary,
            "suggestions": suggestions if suggestions is not None else ["did you mean home.title?"],
        },
    )


def test_diagnose_maps_to_triage() -> None:
    agent = ClaudeTriageAgent(backend=FakeBackend(_diagnose()))
    result = agent.triage(_ctx())
    assert result.category == "selector"
    assert result.summary == "id renamed"
    assert result.suggestions == ["did you mean home.title?"]


def test_secret_in_context_is_masked_before_send() -> None:
    # BE-0047: the failure text, the element tree, and the scenario YAML are redacted before they
    # reach the model — a literal secret in any of them must be [REDACTED] in the sent payload.
    backend = FakeBackend(_diagnose())
    redactor = Redactor(Redact(), values=["sk-secret-token"])
    ctx = _ctx(
        failure="auth failed with token sk-secret-token",
        # `action` comes from the manifest and can embed typed text — it must be redacted too.
        failed_step=FailedStep(0, "type into=auth.password text=sk-secret-token", "一致なし"),
        elements=[
            {
                "identifier": "tok",
                "label": "Authorization: sk-secret-token",
                "traits": ["staticText"],
                "value": "sk-secret-token",
                "frame": (0.0, 0.0, 1.0, 1.0),
            }
        ],
        scenario_yaml="- name: s\n  steps:\n    - type: { into: { id: f }, text: sk-secret-token }\n",
    )
    ClaudeTriageAgent(backend=backend, redactor=redactor).triage(ctx)
    text = _text_of(backend)
    assert "sk-secret-token" not in text
    assert "[REDACTED]" in text


def test_unknown_category_is_clamped() -> None:
    agent = ClaudeTriageAgent(backend=FakeBackend(_diagnose(category="bogus")))
    assert agent.triage(_ctx()).category == "unknown"


def test_no_tool_call_falls_back_to_unknown() -> None:
    agent = ClaudeTriageAgent(backend=FakeBackend())  # response with no tool_use blocks
    result = agent.triage(_ctx())
    assert result.category == "unknown"
    assert result.suggestions == []


def test_request_uses_forced_tool_choice() -> None:
    backend = FakeBackend(_diagnose())
    ClaudeTriageAgent(backend=backend, model="claude-opus-4-8").triage(_ctx())
    request = backend.requests[0]
    assert request.model == "claude-opus-4-8"
    assert isinstance(request.tool_choice, AnyTool)
    assert [t.name for t in request.tools] == ["diagnose"]
    assert request.tools[0].input_schema["properties"]["category"]["enum"] == [
        "selector",
        "timing",
        "assertion",
        "unknown",
    ]


def test_render_carries_the_failure_context() -> None:
    text = _render(_ctx())
    assert "Scenario: s" in text
    assert "Failed step: [0] tap — 一致なし: home.titel" in text
    assert "Target id of the failed step: home.titel" in text
    assert "id=home.title" in text  # the real screen
    assert "Scenario definition (YAML):" in text
    assert "Evidence captured: deviceLog" in text
    assert text.rstrip().endswith("Call the `diagnose` tool exactly once.")


def test_render_handles_empty_elements_and_expectations() -> None:
    text = _render(
        _ctx(
            elements=[],
            failed_step=None,
            target_id=None,
            failed_expectations=["value equals='2': id='counter'"],
        )
    )
    assert "(no element tree captured)" in text
    assert "Failed expectations:" in text
    assert "  - value equals='2': id='counter'" in text


def test_screenshot_sent_as_image_part() -> None:
    backend = FakeBackend(_diagnose())
    png = b"\x89PNG\r\n\x1a\n fake-bytes"
    ClaudeTriageAgent(backend=backend).triage(_ctx(screenshot=png))
    content = backend.requests[0].messages[0].content
    image = next(c for c in content if isinstance(c, ImagePart))
    assert image.data == png
    text = next(c.text for c in content if isinstance(c, TextPart))
    assert "attached above" in text


def test_no_screenshot_is_text_only() -> None:
    backend = FakeBackend(_diagnose())
    ClaudeTriageAgent(backend=backend).triage(_ctx(screenshot=None))
    content = backend.requests[0].messages[0].content
    assert [type(c) for c in content] == [TextPart]
    assert isinstance(content[0], TextPart) and "attached above" not in content[0].text


def _diagnose_with_fix(fix: dict[str, Any]) -> FakeBlock:
    return FakeBlock(
        "diagnose",
        {
            "category": "selector",
            "summary": "renamed",
            "suggestions": ["did you mean nav.settings?"],
            "fix": fix,
        },
    )


def test_fix_is_parsed_into_triage() -> None:
    block = _diagnose_with_fix(
        {"kind": "renameId", "find": "nav.setting", "replace": "nav.settings"}
    )
    result = ClaudeTriageAgent(backend=FakeBackend(block)).triage(_ctx())
    assert result.fix is not None and result.fix.kind == "renameId"
    assert (result.fix.find, result.fix.replace) == ("nav.setting", "nav.settings")
    assert result.fix.summary == "rename id `nav.setting` -> `nav.settings`"


def test_fragment_fix_kinds_are_parsed() -> None:
    add = _diagnose_with_fix(
        {"kind": "addIndex", "find": "{ id: row.cell }", "replace": "{ id: row.cell, index: 0 }"}
    )
    res = ClaudeTriageAgent(backend=FakeBackend(add)).triage(_ctx())
    assert res.fix is not None and res.fix.kind == "addIndex"
    assert (
        res.fix.summary
        == "disambiguate selector `{ id: row.cell }` -> `{ id: row.cell, index: 0 }`"
    )

    bump = _diagnose_with_fix(
        {"kind": "raiseTimeout", "find": "timeout: 5", "replace": "timeout: 15"}
    )
    res2 = ClaudeTriageAgent(backend=FakeBackend(bump)).triage(_ctx())
    assert res2.fix is not None and res2.fix.kind == "raiseTimeout"


def test_no_fix_when_omitted() -> None:
    assert ClaudeTriageAgent(backend=FakeBackend(_diagnose())).triage(_ctx()).fix is None


def test_invalid_fix_is_rejected() -> None:
    bad_fixes = [
        {"kind": "renameId", "find": "a", "replace": "a"},  # a no-op
        {"kind": "moveStep", "find": "a", "replace": "b"},  # unsupported kind
        {"kind": "renameId", "find": "", "replace": "b"},  # empty find
        {"kind": "addIndex", "replace": "b"},  # missing find
    ]
    for bad in bad_fixes:
        result = ClaudeTriageAgent(backend=FakeBackend(_diagnose_with_fix(bad))).triage(_ctx())
        assert result.fix is None, bad


def test_tool_schema_exposes_optional_fix() -> None:
    backend = FakeBackend(_diagnose())
    ClaudeTriageAgent(backend=backend).triage(_ctx())
    schema = backend.requests[0].tools[0].input_schema
    assert schema["properties"]["fix"]["properties"]["kind"]["enum"] == [
        "renameId",
        "addIndex",
        "raiseTimeout",
    ]
    assert "fix" not in schema["required"]  # advisory-by-default; fix is opt-in for the model
