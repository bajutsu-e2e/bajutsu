"""Tests for the AI usage/cost aggregation behind the serve dashboard (BE-0195)."""

from __future__ import annotations

from datetime import UTC, datetime

from bajutsu.analytics.ledger import UsageEvent
from bajutsu.analytics.stats import aggregate_usage, render_html
from bajutsu.analytics.usage import TokenUsage


def _event(
    *,
    ts: str = "2026-07-08T00:00:00+00:00",
    command: str | None = "run",
    provider: str | None = "api-key",
    model: str | None = "claude-sonnet-4-6",
    scenario: str | None = "login",
    input_tokens: int = 100,
    output_tokens: int = 20,
    cost: float | None = 0.001,
) -> UsageEvent:
    return UsageEvent(
        ts=ts,
        command=command,
        provider=provider,
        model=model,
        scenario=scenario,
        step=None,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, calls=1),
        cost=cost,
    )


def test_empty_ledger_aggregates_to_zero() -> None:
    s = aggregate_usage([])
    assert s.calls == 0
    assert s.total_tokens == 0
    assert s.total_cost == 0.0
    assert s.by_provider == []
    assert s.by_model == []
    assert s.by_command == []
    assert s.by_scenario == []
    assert s.by_day == []


def test_totals_sum_tokens_cost_and_calls() -> None:
    s = aggregate_usage(
        [
            _event(input_tokens=100, output_tokens=20, cost=0.01),
            _event(input_tokens=200, output_tokens=30, cost=0.02),
        ]
    )
    assert s.calls == 2
    assert s.total_tokens == 100 + 20 + 200 + 30
    assert s.total_cost == 0.03
    assert s.unpriced_calls == 0


def test_groups_by_each_dimension() -> None:
    s = aggregate_usage(
        [
            _event(provider="api-key", model="claude-opus-4", command="run", scenario="login"),
            _event(provider="api-key", model="claude-opus-4", command="run", scenario="login"),
            _event(provider="bedrock", model="claude-haiku", command="crawl", scenario="search"),
        ]
    )
    # Ranked by descending calls, so the two-call api-key/opus/run/login group leads each dimension.
    assert [(r.key, r.calls) for r in s.by_provider] == [("api-key", 2), ("bedrock", 1)]
    assert [r.key for r in s.by_model] == ["claude-opus-4", "claude-haiku"]
    assert [r.key for r in s.by_command] == ["run", "crawl"]
    assert [r.key for r in s.by_scenario] == ["login", "search"]


def test_none_dimension_renders_as_unknown_key() -> None:
    s = aggregate_usage([_event(command=None, scenario=None)])
    assert s.by_command[0].key == "(unknown)"
    assert s.by_scenario[0].key == "(unknown)"


def test_unpriced_cost_is_not_fabricated() -> None:
    # A subscription provider records tokens with cost=None; its dollar figure stays absent.
    s = aggregate_usage(
        [
            _event(provider="ant", model="claude-sonnet-4-6", cost=None, input_tokens=500),
            _event(provider="ant", model="claude-sonnet-4-6", cost=None, input_tokens=500),
        ]
    )
    assert s.total_cost == 0.0
    assert s.unpriced_calls == 2
    row = s.by_provider[0]
    assert row.key == "ant"
    assert row.priced_calls == 0
    assert row.has_price is False  # template shows "—", not "$0.00"
    assert row.tokens == 1040  # tokens are still counted


def test_mixed_priced_and_unpriced_group_sums_only_priced() -> None:
    s = aggregate_usage(
        [
            _event(provider="api-key", cost=0.05),
            _event(provider="api-key", cost=None),
        ]
    )
    row = s.by_provider[0]
    assert row.cost == 0.05
    assert row.priced_calls == 1
    assert row.unpriced_calls == 1
    assert row.has_price is True


def test_time_range_filters_by_timestamp() -> None:
    events = [
        _event(ts="2026-07-01T00:00:00+00:00"),
        _event(ts="2026-07-05T00:00:00+00:00"),
        _event(ts="2026-07-10T00:00:00+00:00"),
    ]
    s = aggregate_usage(
        events,
        since=datetime(2026, 7, 3, tzinfo=UTC),
        until=datetime(2026, 7, 8, tzinfo=UTC),
    )
    assert s.calls == 1
    assert s.by_day[0].day == "2026-07-05"


def test_time_range_excludes_events_with_unparseable_timestamp() -> None:
    # A malformed ts can't be placed on the timeline, so an active bound drops it (rather than
    # silently keeping an event that might fall outside the window).
    s = aggregate_usage(
        [_event(ts="not-a-timestamp"), _event(ts="2026-07-05T00:00:00+00:00")],
        since=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert s.calls == 1


def test_time_range_excludes_offset_naive_timestamp() -> None:
    # A ledger line without a UTC offset is offset-naive; comparing it against the aware bounds would
    # raise TypeError, so it is treated as unplaceable and dropped rather than crashing the filter.
    s = aggregate_usage(
        [_event(ts="2026-07-05T00:00:00"), _event(ts="2026-07-05T00:00:00+00:00")],
        since=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert s.calls == 1


def test_comparison_sorts_unpriced_pairs_last() -> None:
    # A priced pair whose cost sums to $0.00 still outranks a genuinely unpriced pair.
    s = aggregate_usage(
        [
            _event(provider="ant", model="sub", cost=None),
            _event(provider="api-key", model="free", cost=0.0),
        ]
    )
    assert [(r.provider, r.priced_calls) for r in s.comparison] == [("api-key", 1), ("ant", 0)]


def test_by_day_trend_is_chronological() -> None:
    s = aggregate_usage(
        [
            _event(ts="2026-07-10T09:00:00+00:00", cost=0.02),
            _event(ts="2026-07-08T09:00:00+00:00", cost=0.01),
            _event(ts="2026-07-08T18:00:00+00:00", cost=0.03),
        ]
    )
    assert [d.day for d in s.by_day] == ["2026-07-08", "2026-07-10"]
    assert s.by_day[0].calls == 2
    assert s.by_day[0].cost == 0.04


def test_comparison_reports_cost_per_call_and_per_scenario() -> None:
    s = aggregate_usage(
        [
            _event(provider="api-key", model="claude-opus-4", scenario="login", cost=0.10),
            _event(provider="api-key", model="claude-opus-4", scenario="search", cost=0.30),
        ]
    )
    row = next(r for r in s.comparison if r.model == "claude-opus-4")
    assert row.calls == 2
    assert row.scenarios == 2
    assert row.cost == 0.40
    assert row.cost_per_call == 0.20
    assert row.cost_per_scenario == 0.20


def test_comparison_leaves_cost_per_metric_absent_when_unpriced() -> None:
    s = aggregate_usage([_event(provider="ant", model="claude-sonnet-4-6", cost=None)])
    row = s.comparison[0]
    assert row.cost_per_call is None
    assert row.cost_per_scenario is None


def test_render_html_shows_empty_state_without_events() -> None:
    html = render_html(aggregate_usage([]))
    assert "usageLedger" in html  # empty state explains how recording is enabled


def test_render_html_reports_totals_and_dimensions() -> None:
    html = render_html(
        aggregate_usage([_event(provider="api-key", model="claude-opus-4", scenario="login")])
    )
    assert "claude-opus-4" in html
    assert "login" in html
