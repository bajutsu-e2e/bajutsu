"""Tests for token-usage accounting (bajutsu.usage)."""

from __future__ import annotations

from conftest import FAKE_USAGE_PER_CALL, FakeBackend, FakeBlock, FakeUsage

from bajutsu import usage
from bajutsu.agent_protocols import Observation
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


# --- BE-0194 §4: per-category token attribution (reporting only) ---


def test_addition_is_the_inverse_of_subtraction() -> None:
    a = TokenUsage(input_tokens=10, output_tokens=5, cache_read_tokens=3, calls=1)
    b = TokenUsage(input_tokens=7, output_tokens=2, cache_write_tokens=4, calls=1)
    total = a + b
    assert total.input_tokens == 17 and total.output_tokens == 7
    assert total.cache_write_tokens == 4 and total.cache_read_tokens == 3 and total.calls == 2
    assert total - b == a  # add then subtract round-trips


def test_record_attributes_tokens_to_the_named_category() -> None:
    before = usage.snapshot_by_category()
    usage.record({"input_tokens": 5, "output_tokens": 2}, category="plan")
    usage.record({"input_tokens": 3, "output_tokens": 1}, category="next_action")
    after = usage.snapshot_by_category()
    plan = after.get("plan", TokenUsage()) - before.get("plan", TokenUsage())
    action = after.get("next_action", TokenUsage()) - before.get("next_action", TokenUsage())
    assert plan.input_tokens == 5 and plan.calls == 1
    assert action.input_tokens == 3 and action.calls == 1


def test_categories_sum_to_the_running_total_no_double_counting() -> None:
    before_total = usage.snapshot()
    before_cat = usage.snapshot_by_category()
    usage.record({"input_tokens": 5, "output_tokens": 2}, category="plan")
    usage.record({"input_tokens": 3, "output_tokens": 1}, category="next_action")
    usage.record({"input_tokens": 9, "output_tokens": 4}, category="alert-guard")
    spent_total = usage.snapshot() - before_total
    after_cat = usage.snapshot_by_category()
    per_cat_sum = sum(
        (after_cat.get(c, TokenUsage()) - before_cat.get(c, TokenUsage())).total_tokens
        for c in after_cat
    )
    assert per_cat_sum == spent_total.total_tokens  # every token lands in exactly one category


def test_uncategorized_record_lands_in_other() -> None:
    before = usage.snapshot_by_category()
    usage.record({"input_tokens": 4, "output_tokens": 1})  # no category → the default bucket
    after = usage.snapshot_by_category()
    delta = after.get("other", TokenUsage()) - before.get("other", TokenUsage())
    assert delta.input_tokens == 4 and delta.calls == 1


def test_breakdown_lines_render_only_non_empty_categories() -> None:
    before = usage.snapshot_by_category()
    usage.record({"input_tokens": 100, "output_tokens": 30}, category="plan")
    usage.record({"input_tokens": 50, "output_tokens": 10}, category="next_action")
    after = usage.snapshot_by_category()
    lines = usage.breakdown_lines(before, after)
    joined = "\n".join(lines)
    assert "plan" in joined and "next_action" in joined
    assert "alert-guard" not in joined  # a category with no spend is not listed
    assert all(line.startswith("  ") for line in lines)  # indented under the total
