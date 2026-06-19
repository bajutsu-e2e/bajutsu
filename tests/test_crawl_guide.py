"""Tests for the AI crawl guide (bajutsu/crawl_guide.py, BE-0038 --guide ai).

No LLM: the proposer is behind a protocol, exercised here with a scripted fake — the same way
record tests its authoring agent. We check the guide unions the proposer's actions with the
deterministic baseline (proposer wins), the tool-call parsing, and guide selection.
"""

from __future__ import annotations

import pytest
from conftest import el

from bajutsu import crawl
from bajutsu.crawl_guide import Proposal, _actions_from, _proposal_from, ai_guide, make_guide
from bajutsu.drivers.fake import FakeDriver


class _FakeProposer:
    def __init__(self, actions: list[crawl.Action], thought: str = "") -> None:
        self._actions = actions
        self._thought = thought

    def propose(self, elements: list[dict], screenshot: bytes | None) -> Proposal:
        return Proposal(actions=list(self._actions), thought=self._thought)


def test_ai_guide_unions_proposer_with_deterministic_and_dedups() -> None:
    elements = [
        el(identifier="f.user", traits=["textField"]),  # deterministic would type a placeholder
        el(identifier="f.submit", traits=["button", "notEnabled"]),  # disabled -> nobody taps it
        el(label="Skip", traits=["button"]),  # id-less -> only the AI proposes it
    ]
    proposer = _FakeProposer(
        [
            crawl.Action("type", target="f.user", value="real@example.com"),  # realistic value
            crawl.Action("tap", label="Skip"),
        ]
    )
    actions = ai_guide(proposer)(FakeDriver(screen=elements), elements)

    # The proposer's typed value wins over the deterministic placeholder (deduped by kind+key).
    typed = [a for a in actions if a.kind == "type" and a.target == "f.user"]
    assert len(typed) == 1 and typed[0].value == "real@example.com"
    # The id-less, AI-only tap is present (the deterministic guide skips id-less controls).
    assert any(a.kind == "tap" and a.label == "Skip" for a in actions)
    # The disabled control is still never a candidate.
    assert not any(a.target == "f.submit" for a in actions)


def test_ai_guide_falls_back_to_deterministic_when_proposer_is_empty() -> None:
    elements = [el(identifier="a", traits=["button"]), el(identifier="b", traits=["button"])]
    actions = ai_guide(_FakeProposer([]))(FakeDriver(screen=elements), elements)
    assert {a.target for a in actions} == {"a", "b"}  # the deterministic baseline still drives


def test_actions_from_parses_skips_malformed_and_caps() -> None:
    payload = {
        "actions": [
            {"action": "tap", "id": "a"},
            {"action": "type", "id": "b", "value": "x"},
            {"action": "tap", "label": "L", "index": 1},
            {"action": "tap"},  # no selector -> skipped (can't replay)
            {"bad": 1},  # malformed -> skipped
        ]
    }
    acts = _actions_from(payload, cap=10)
    assert [(a.kind, a.target or a.label) for a in acts] == [
        ("tap", "a"),
        ("type", "b"),
        ("tap", "L"),
    ]
    assert acts[1].value == "x" and acts[2].index == 1
    assert len(_actions_from(payload, cap=1)) == 1  # capped


def test_ai_guide_narrates_the_models_thought_and_choices() -> None:
    """The AI's reasoning + chosen operations are surfaced live through `report` (the crawl log /
    web UI) so a watcher can see what the model is thinking."""
    elements = [el(identifier="login.go", traits=["button", "notEnabled"])]
    proposer = _FakeProposer(
        [crawl.Action("type", target="login.user", value="a@b.com")],
        thought="A login form; I'll fill the email to enable Sign In.",
    )
    log: list[str] = []
    ai_guide(proposer, report=log.append)(FakeDriver(screen=elements), elements)
    assert any("A login form" in line for line in log)  # the thought is shown
    assert any("login.user" in line and "a@b.com" in line for line in log)  # the chosen input


def test_proposal_from_parses_thought_and_actions() -> None:
    payload = {"thought": "looks like a form", "actions": [{"action": "tap", "id": "x"}]}
    proposal = _proposal_from(payload, cap=10)
    assert proposal.thought == "looks like a form"
    assert [a.target for a in proposal.actions] == ["x"]
    assert _proposal_from({"actions": []}, cap=10).thought == ""  # missing thought -> empty


def test_make_guide_selects_off_ai_or_errors() -> None:
    assert make_guide("off") is None and make_guide("") is None  # deterministic default
    assert callable(make_guide("ai"))  # built without needing an API key (lazy client)
    with pytest.raises(ValueError, match="unknown crawl guide"):
        make_guide("nonsense")
