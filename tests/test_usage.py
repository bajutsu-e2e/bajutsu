"""Tests for token-usage accounting (bajutsu.usage)."""

from __future__ import annotations

from conftest import FAKE_USAGE_PER_CALL, FakeBackend, FakeBlock, FakeUsage

from bajutsu import usage
from bajutsu.agent import Observation
from bajutsu.claude_agent import ClaudeAgent
from bajutsu.drivers import base
from bajutsu.usage import TokenUsage


def _obs() -> Observation:
    el: base.Element = {
        "identifier": "a",
        "label": "A",
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }
    return Observation(goal="g", screen=[el], history=[])


def test_total_sums_all_four_input_buckets() -> None:
    u = TokenUsage(input_tokens=100, output_tokens=30, cache_write_tokens=20, cache_read_tokens=14)
    assert u.total_tokens == 164


def test_subtraction_is_the_per_feature_delta() -> None:
    before = TokenUsage(input_tokens=100, output_tokens=30, calls=2)
    after = TokenUsage(input_tokens=180, output_tokens=55, calls=5)
    spent = after - before
    assert spent.input_tokens == 80 and spent.output_tokens == 25 and spent.calls == 3


def test_of_reads_a_dict_usage_like_the_claude_code_envelope() -> None:
    # The Claude Code adapter (BE-0176) reports usage as a dict, not an SDK object — `of` must read
    # its fields (attribute-only access would silently count it as zero tokens).
    one = usage.of(
        {
            "input_tokens": 100,
            "output_tokens": 30,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 14,
        }
    )
    assert one.total_tokens == 164 and one.calls == 1


def test_of_none_is_an_empty_snapshot() -> None:
    assert usage.of(None) == TokenUsage()


def test_record_counts_a_dict_usage_into_the_tracker() -> None:
    before = usage.snapshot()
    usage.record({"input_tokens": 7, "output_tokens": 3})
    spent = usage.snapshot() - before
    assert spent.input_tokens == 7 and spent.output_tokens == 3 and spent.calls == 1


def test_render_is_a_one_line_human_summary() -> None:
    u = TokenUsage(input_tokens=900, output_tokens=300, cache_write_tokens=20, cache_read_tokens=14)
    line = u.render()
    assert line == "AI usage: 1,234 tokens over 0 calls (900 in, 300 out; cache 20 write, 14 read)"


def test_render_omits_cache_when_none_and_singularizes_one_call() -> None:
    line = TokenUsage(input_tokens=10, output_tokens=5, calls=1).render()
    assert line == "AI usage: 15 tokens over 1 call (10 in, 5 out)"


def test_record_is_best_effort_with_none_and_missing_fields() -> None:
    before = usage.snapshot()
    usage.record(None)  # a mocked client / SDK that omitted usage: ignored, no call counted

    class _Partial:
        input_tokens = 7  # only one field present; the rest count as zero

    usage.record(_Partial())
    spent = usage.snapshot() - before
    assert spent.calls == 1
    assert spent.input_tokens == 7 and spent.output_tokens == 0


def test_record_ignores_non_numeric_and_negative_counts() -> None:
    before = usage.snapshot()
    usage.record(FakeUsage(input_tokens=-5, output_tokens=0))  # type: ignore[arg-type]
    spent = usage.snapshot() - before
    assert spent.calls == 1 and spent.total_tokens == 3  # only the default cache_read=3 survives


def test_claude_agent_records_each_call_into_the_tracker() -> None:
    before = usage.snapshot()
    agent = ClaudeAgent(backend=FakeBackend(FakeBlock("tap", {"id": "a"})))
    agent.next_action(_obs())
    spent = usage.snapshot() - before
    assert spent.calls == 1 and spent.total_tokens == FAKE_USAGE_PER_CALL
