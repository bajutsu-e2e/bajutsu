"""Tests for the AI crawl guide (bajutsu/crawl_guide.py, BE-0038 --guide ai).

No LLM: the proposer is behind a protocol, exercised here with a scripted fake — the same way
record tests its authoring agent. We check the guide unions the proposer's actions with the
deterministic baseline (proposer wins), the tool-call parsing, and guide selection.
"""

from __future__ import annotations

from conftest import FakeBackend, FakeBlock, ShotDriver, el

from bajutsu import crawl
from bajutsu.agents.ai_config import AiConfig
from bajutsu.ai.base import TextPart
from bajutsu.crawl import tabs as crawl_tabs
from bajutsu.crawl.guide import (
    ClaudeActionProposer,
    Proposal,
    _actions_from,
    _proposal_from,
    ai_guide,
    make_guide,
)
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence.redaction import Redactor
from bajutsu.scenario import Redact


class _FakeProposer:
    def __init__(self, actions: list[crawl.Action], thought: str = "") -> None:
        self._actions = actions
        self._thought = thought
        self.seen_candidates: list[crawl.Action] | None = None  # what the inspector fed us
        self.seen_dismissed: tuple[str, ...] = ()  # OS prompt dismissed to reach the screen

    def propose(
        self,
        elements: list[dict],
        screenshot: bytes | None,
        candidates: list[crawl.Action],
        dismissed: tuple[str, ...],
    ) -> Proposal:
        self.seen_candidates = candidates
        self.seen_dismissed = dismissed
        return Proposal(actions=list(self._actions), thought=self._thought)


def _ctx(dismissed: tuple[str, ...] = ()) -> crawl.GuideContext:
    return crawl.GuideContext(dismissed=dismissed)


def test_ai_guide_feeds_the_deterministic_candidates_to_the_proposer() -> None:
    """The pipeline inspects deterministically first, then hands those operations to the AI so it
    can reason about and combine them."""
    elements = [el(identifier="a", traits=["button"]), el(identifier="f", traits=["textField"])]
    proposer = _FakeProposer([])
    ai_guide(proposer)(FakeDriver(screen=elements), elements, _ctx())
    keys = {a.key for a in (proposer.seen_candidates or [])}
    assert "a" in keys and "f" in keys  # the deterministic tap + type were fed to the AI


def test_ai_guide_feeds_a_dismissed_os_prompt_to_the_proposer() -> None:
    """A just-dismissed OS prompt is passed to the AI so it factors it into the next strategy."""
    elements = [el(identifier="a", traits=["button"])]
    proposer = _FakeProposer([])
    ai_guide(proposer)(FakeDriver(screen=elements), elements, _ctx(("Allow",)))
    assert proposer.seen_dismissed == ("Allow",)


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
    actions = ai_guide(proposer)(FakeDriver(screen=elements), elements, _ctx())

    # The proposer's typed value wins over the deterministic placeholder (deduped by kind+key).
    typed = [a for a in actions if a.kind == "type" and a.target == "f.user"]
    assert len(typed) == 1 and typed[0].value == "real@example.com"
    # The id-less, AI-only tap is present (the deterministic guide skips id-less controls).
    assert any(a.kind == "tap" and a.label == "Skip" for a in actions)
    # The disabled control is still never a candidate.
    assert not any(a.target == "f.submit" for a in actions)


def test_ai_guide_falls_back_to_deterministic_when_proposer_is_empty() -> None:
    elements = [el(identifier="a", traits=["button"]), el(identifier="b", traits=["button"])]
    actions = ai_guide(_FakeProposer([]))(FakeDriver(screen=elements), elements, _ctx())
    assert {a.target for a in actions} == {"a", "b"}  # the deterministic baseline still drives


class _FakeTabLocator:
    """A scripted tab locator: returns its preset tabs and records that it was asked."""

    def __init__(self, tabs: list[crawl_tabs.TabTarget]) -> None:
        self._tabs = tabs
        self.calls = 0

    def locate(self, screenshot_png: bytes) -> list[crawl_tabs.TabTarget]:
        self.calls += 1
        return list(self._tabs)


def test_ai_guide_uses_vision_tabs_only_for_an_unaddressable_tab_bar() -> None:
    """When the accessibility tree surfaces the tab bar as one opaque container (a `group` labelled "Tab Bar", no
    per-tab ids), the vision locator supplies a coordinate tap per tab, ordered first so the crawl
    switches tabs before drilling in. When the tabs are already addressable by id — or there is no
    tab bar — the locator is skipped entirely."""
    bar = [el(label="Tab Bar", traits=["group"]), el(identifier="home.start", traits=["button"])]
    locator = _FakeTabLocator(
        [crawl_tabs.TabTarget(0.2, 0.95, "Home"), crawl_tabs.TabTarget(0.8, 0.95, "Me")]
    )
    actions = ai_guide(_FakeProposer([]), tab_locator=locator)(ShotDriver(screen=bar), bar, _ctx())
    assert locator.calls == 1
    taps = [a for a in actions if a.kind == "tap_point"]
    assert [a.label for a in taps] == ["Home", "Me"]  # both tabs, left to right
    assert actions[0].kind == "tap_point"  # tabs first
    assert taps[0].point == (0.2, 0.95)

    # Tabs already addressable by id -> the deterministic taps cover them, no vision.
    addr = [el(identifier="tab.home", traits=["tab"]), el(identifier="tab.me", traits=["tab"])]
    locator2 = _FakeTabLocator([crawl_tabs.TabTarget(0.5, 0.95, "X")])
    actions2 = ai_guide(_FakeProposer([]), tab_locator=locator2)(
        ShotDriver(screen=addr), addr, _ctx()
    )
    assert locator2.calls == 0
    assert not any(a.kind == "tap_point" for a in actions2)

    # No tab bar at all -> vision never fires on an ordinary screen.
    plain = [el(identifier="only", traits=["button"])]
    locator3 = _FakeTabLocator([crawl_tabs.TabTarget(0.5, 0.95, "X")])
    ai_guide(_FakeProposer([]), tab_locator=locator3)(ShotDriver(screen=plain), plain, _ctx())
    assert locator3.calls == 0


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
    ai_guide(proposer, report=log.append)(FakeDriver(screen=elements), elements, _ctx())
    assert any("A login form" in line for line in log)  # the thought is shown
    assert any("login.user" in line and "a@b.com" in line for line in log)  # the chosen input


def test_actions_from_parses_a_compound_fill() -> None:
    payload = {
        "actions": [
            {
                "action": "fill",
                "fields": [{"id": "email", "value": "a@b.com"}, {"id": "pw", "value": "P1!"}],
            }
        ]
    }
    acts = _actions_from(payload, cap=10)
    assert len(acts) == 1 and acts[0].kind == "fill"
    assert acts[0].fields == (("email", "a@b.com"), ("pw", "P1!"))


def test_proposal_from_parses_thought_and_actions() -> None:
    payload = {"thought": "looks like a form", "actions": [{"action": "tap", "id": "x"}]}
    proposal = _proposal_from(payload, cap=10)
    assert proposal.thought == "looks like a form"
    assert [a.target for a in proposal.actions] == ["x"]
    assert _proposal_from({"actions": []}, cap=10).thought == ""  # missing thought -> empty


def test_make_guide_builds_the_ai_guide() -> None:
    # Crawl is AI-driven; the guide is always the SDK-backed AI guide, built lazily (no credential
    # needed at construction). The resolved `ai` config picks the provider (BE-0163).
    assert callable(make_guide())  # default provider
    assert callable(make_guide(ai=AiConfig(provider="ant")))  # ant provider, lazy backend


# --- BE-0097: the crawl guide's AI inputs are redacted and the provider config is threaded ---


def _propose_backend() -> FakeBackend:
    """A fake backend that returns a minimal propose_actions tool call."""
    return FakeBackend(
        FakeBlock("propose_actions", {"thought": "ok", "actions": [{"action": "tap", "id": "a"}]})
    )


def _text_of(backend: FakeBackend) -> str:
    return next(p.text for p in backend.requests[0].messages[0].content if isinstance(p, TextPart))


def test_claude_proposer_threads_ai_config_to_backend_and_model() -> None:
    """BE-0097: a non-default AiConfig is threaded to the backend and resolve_model, so the crawl
    guide talks to the user's configured provider, not a hardcoded default."""
    ai = AiConfig(provider="bedrock", model="us.anthropic.claude-opus-4-8-v1")
    backend = _propose_backend()
    proposer = ClaudeActionProposer(backend=backend, ai=ai)
    proposer.propose([el(identifier="a", traits=["button"])], None, [], ())
    assert backend.requests[0].model == "us.anthropic.claude-opus-4-8-v1"


def test_claude_proposer_redacts_elements_before_send() -> None:
    """BE-0097: a secret in an element's value/label is masked before it reaches the model."""
    backend = _propose_backend()
    redactor = Redactor(Redact(labels=["カード番号"]), values=["sk-secret-token"])
    elements = [
        el(identifier="card", label="カード番号", value="4111-1111-1111-1111"),
        el(identifier="tok", label="token: sk-secret-token", value="sk-secret-token"),
    ]
    ClaudeActionProposer(backend=backend, redactor=redactor).propose(elements, None, [], ())
    text = _text_of(backend)
    assert "sk-secret-token" not in text
    assert "[REDACTED]" in text


def test_claude_proposer_no_redactor_leaves_text_unmasked() -> None:
    backend = _propose_backend()
    elements = [el(identifier="a", label="plain-label")]
    ClaudeActionProposer(backend=backend).propose(elements, None, [], ())
    text = _text_of(backend)
    assert "plain-label" in text


def test_output_language_is_folded_into_the_crawl_system_prompt() -> None:
    # BE-0188: the guide's streamed reasoning comes out in the chosen language; `auto` (default)
    # leaves the prompt unchanged.
    default = _propose_backend()
    ClaudeActionProposer(backend=default).propose([el(identifier="a")], None, [], ())
    assert "日本語" not in default.requests[0].system

    ja = _propose_backend()
    ClaudeActionProposer(backend=ja, ai=AiConfig(language="ja")).propose(
        [el(identifier="a")], None, [], ()
    )
    assert "日本語" in ja.requests[0].system
