"""Tests for the shared ClaudeBackedAgent base (BE-0246 Unit 3).

The seven Claude-backed classes reuse this base for the byte-identical backend/usage plumbing;
here a minimal subclass exercises that plumbing directly, with the real usage tracker (no mocks).
"""

from __future__ import annotations

from conftest import FAKE_USAGE_PER_CALL, FakeBackend, FakeUsage

from bajutsu import usage
from bajutsu.ai.base import MessageResponse
from bajutsu.claude_backed_agent import ClaudeBackedAgent


class _Probe(ClaudeBackedAgent):
    """Minimal concrete subclass — inherits the base plumbing unchanged."""


def _probe(**kwargs: object) -> _Probe:
    return _Probe(default_model="probe-model", **kwargs)  # type: ignore[arg-type]


def test_resolved_model_defaults_from_the_constant() -> None:
    assert _probe(backend=None, ai=None)._model == "probe-model"


def test_explicit_model_overrides_the_default() -> None:
    assert _probe(backend=None, ai=None, model="pinned")._model == "pinned"


def test_ensure_backend_returns_the_injected_backend() -> None:
    backend = FakeBackend()
    assert _probe(backend=backend, ai=None)._ensure_backend() is backend


def test_ensure_backend_constructs_one_lazily_and_caches_it() -> None:
    probe = _probe(backend=None, ai=None)
    first = probe._ensure_backend()
    assert first is probe._ensure_backend()  # built once, then reused


def test_record_usage_defaults_to_the_other_category() -> None:
    probe = _probe(backend=FakeBackend(), ai=None)
    before = usage.snapshot_by_category().get(usage.CATEGORY_OTHER, usage.TokenUsage())
    probe._record_usage(MessageResponse(content=[], usage=FakeUsage()))
    after = usage.snapshot_by_category()[usage.CATEGORY_OTHER]
    assert (after - before).total_tokens == FAKE_USAGE_PER_CALL


def test_record_usage_honors_an_explicit_category() -> None:
    probe = _probe(backend=FakeBackend(), ai=None)
    before = usage.snapshot_by_category().get(usage.CATEGORY_ACTION, usage.TokenUsage())
    probe._record_usage(MessageResponse(content=[], usage=FakeUsage()), usage.CATEGORY_ACTION)
    after = usage.snapshot_by_category()[usage.CATEGORY_ACTION]
    assert (after - before).total_tokens == FAKE_USAGE_PER_CALL
