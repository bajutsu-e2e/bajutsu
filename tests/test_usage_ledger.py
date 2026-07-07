"""Tests for the attributed, persistent AI usage/cost ledger (BE-0196)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bajutsu import usage, usage_ledger
from bajutsu.usage import TokenUsage
from bajutsu.usage_ledger import (
    LEDGER_SCHEMA_VERSION,
    JsonlLedger,
    Pricing,
    UsageEvent,
    attributed,
    compute_cost,
    current_attribution,
    pricing_table_from_config,
    read_events,
)


def test_event_round_trips_through_a_versioned_record() -> None:
    event = UsageEvent(
        ts="2026-07-08T00:00:00+00:00",
        command="crawl",
        provider="api-key",
        model="claude-sonnet-4-6",
        scenario="login",
        step="tap-submit",
        usage=TokenUsage(input_tokens=100, output_tokens=30, cache_read_tokens=14, calls=1),
        cost=0.0012,
    )
    record = event.to_record()
    assert record["v"] == LEDGER_SCHEMA_VERSION
    assert UsageEvent.from_record(record) == event


def test_from_record_tolerates_missing_optional_fields() -> None:
    # A forward-compatible reader: an older/partial line still yields an event, missing counts zero.
    event = UsageEvent.from_record({"v": 1, "ts": "2026-07-08T00:00:00+00:00"})
    assert event.command is None and event.cost is None
    assert event.usage == TokenUsage()


def test_pricing_costs_every_token_bucket_in_dollars() -> None:
    pricing = Pricing(
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
        cache_write_usd_per_mtok=3.75,
        cache_read_usd_per_mtok=0.3,
    )
    # 1M input @ $3 + 1M output @ $15 + 1M cache-write @ $3.75 + 1M cache-read @ $0.30.
    u = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert pricing.cost(u) == 3.0 + 15.0 + 3.75 + 0.3


def test_compute_cost_prices_a_known_api_key_model() -> None:
    table = {("api-key", "sonnet"): Pricing(3.0, 15.0, 3.75, 0.3)}
    # The model id carries a version suffix; the family key matches by substring.
    cost = compute_cost(
        table, "api-key", "claude-sonnet-4-6-20250101", TokenUsage(input_tokens=1_000_000)
    )
    assert cost == 3.0


def test_compute_cost_is_null_for_a_subscription_provider() -> None:
    # `ant` / `claude-code` have no per-token price: tokens are still counted, cost stays null.
    table = {("api-key", "sonnet"): Pricing(3.0, 15.0, 3.75, 0.3)}
    assert (
        compute_cost(table, "claude-code", "claude-sonnet-4-6", TokenUsage(input_tokens=999))
        is None
    )


def test_compute_cost_is_null_for_an_unpriced_model() -> None:
    assert compute_cost({}, "api-key", "some-unknown-model", TokenUsage(input_tokens=999)) is None


def test_compute_cost_prefers_an_exact_key_over_a_family_match() -> None:
    # An exact (provider, model) entry wins over a looser family-substring entry for the same model.
    table = {
        ("api-key", "sonnet"): Pricing(3.0, 0.0, 0.0, 0.0),
        ("api-key", "claude-sonnet-4-6"): Pricing(9.0, 0.0, 0.0, 0.0),
    }
    cost = compute_cost(table, "api-key", "claude-sonnet-4-6", TokenUsage(input_tokens=1_000_000))
    assert cost == 9.0


def test_compute_cost_is_null_when_model_is_unknown() -> None:
    # A None model (never resolved) has no price — tokens still record, cost stays null.
    table = {("api-key", "sonnet"): Pricing(3.0, 15.0, 3.75, 0.3)}
    assert compute_cost(table, "api-key", None, TokenUsage(input_tokens=1)) is None


def test_attribution_scope_nests_and_restores() -> None:
    assert current_attribution().command is None
    with attributed(command="crawl", scenario="login"):
        assert current_attribution().command == "crawl"
        with attributed(step="tap-submit"):
            inner = current_attribution()
            # The inner scope refines the outer: command/scenario carry through, step is added.
            assert (
                inner.command == "crawl"
                and inner.scenario == "login"
                and inner.step == "tap-submit"
            )
        assert current_attribution().step is None
    assert current_attribution().command is None


def test_jsonl_ledger_appends_and_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "usage.jsonl"
    ledger = JsonlLedger(path)
    first = UsageEvent(
        ts="2026-07-08T00:00:00+00:00",
        command="record",
        provider="api-key",
        model="claude-sonnet-4-6",
        scenario=None,
        step=None,
        usage=TokenUsage(input_tokens=10, output_tokens=5, calls=1),
        cost=0.0001,
    )
    second = UsageEvent(
        ts="2026-07-08T00:01:00+00:00",
        command="crawl",
        provider="claude-code",
        model="claude-sonnet-4-6",
        scenario="home",
        step="scroll",
        usage=TokenUsage(input_tokens=20, output_tokens=8, calls=1),
        cost=None,
    )
    ledger.append(first)
    ledger.append(second)
    assert read_events(path) == [first, second]


def test_read_events_is_empty_when_the_ledger_is_absent(tmp_path: Path) -> None:
    assert read_events(tmp_path / "never-written.jsonl") == []


def test_read_events_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    path.write_text(
        '{"v": 1, "ts": "2026-07-08T00:00:00+00:00", "command": "run"}\n\n',
        encoding="utf-8",
    )
    events = read_events(path)
    assert len(events) == 1 and events[0].command == "run"


def test_pricing_table_from_config_overlays_config_on_defaults() -> None:
    # A config entry overrides the shipped default for that (provider, model) key; other defaults stay.
    table = pricing_table_from_config(
        {"api-key/sonnet": {"input": 99.0, "output": 1.0, "cacheWrite": 0.0, "cacheRead": 0.0}}
    )
    assert table[("api-key", "sonnet")] == Pricing(99.0, 1.0, 0.0, 0.0)
    assert ("api-key", "opus") in table  # a shipped default survives the overlay


def test_pricing_table_from_config_is_defaults_when_config_is_none() -> None:
    assert pricing_table_from_config(None) == usage_ledger.default_pricing_table()


def test_pricing_table_skips_malformed_keys_so_none_become_a_catch_all() -> None:
    # A key with no slash or an empty model would family-match every model ("" in anything is true);
    # such keys are dropped so they can never silently reprice an unrelated model.
    table = pricing_table_from_config(
        {
            "no-slash": {"input": 1.0, "output": 1.0},
            "api-key/": {"input": 1.0, "output": 1.0},
            "/sonnet": {"input": 1.0, "output": 1.0},
        }
    )
    # No empty-model / empty-provider entry crept in; an unknown model stays unpriced (null cost).
    assert not any(not provider or not model for provider, model in table)
    assert compute_cost(table, "api-key", "brand-new-model", TokenUsage(input_tokens=1_000)) is None


def test_record_emits_a_ledger_event_when_configured(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    usage_ledger.configure(
        JsonlLedger(path), {("api-key", "sonnet"): Pricing(3.0, 15.0, 3.75, 0.3)}
    )
    try:
        with attributed(command="record", scenario="login"):
            usage.record(
                {"input_tokens": 1_000_000, "output_tokens": 0},
                provider="api-key",
                model="claude-sonnet-4-6",
            )
        events = read_events(path)
        assert len(events) == 1
        event = events[0]
        assert event.command == "record" and event.scenario == "login"
        assert event.provider == "api-key" and event.model == "claude-sonnet-4-6"
        assert event.usage.input_tokens == 1_000_000
        assert event.cost == 3.0
        assert event.ts  # a UTC timestamp was stamped
    finally:
        usage_ledger.reset()


def test_record_is_a_noop_ledger_when_unconfigured() -> None:
    # Unit 5: the in-memory total keeps working and nothing is persisted when no ledger is set up.
    usage_ledger.reset()
    before = usage.snapshot()
    usage.record({"input_tokens": 7, "output_tokens": 3}, provider="api-key", model="m")
    spent = usage.snapshot() - before
    assert spent.input_tokens == 7 and spent.calls == 1


def test_record_ledger_emission_never_raises(tmp_path: Path) -> None:
    # Best-effort (reporting only): a broken sink must not break the AI path.
    class _Broken(JsonlLedger):
        def append(self, event: UsageEvent) -> None:
            raise OSError("disk full")

    usage_ledger.configure(_Broken(tmp_path / "x.jsonl"), {})
    try:
        usage.record({"input_tokens": 1}, provider="api-key", model="m")  # must not raise
    finally:
        usage_ledger.reset()


def test_attribution_entered_inside_a_worker_thread_is_recorded(tmp_path: Path) -> None:
    # Why `run` binds attribution at the alert guard (which fires in a ThreadPoolExecutor worker)
    # rather than on the main thread: a scope entered *inside* the worker is what `emit` reads there.
    path = tmp_path / "usage.jsonl"
    usage_ledger.configure(JsonlLedger(path), {})

    def work() -> None:
        with attributed(command="run", scenario="checkout"):
            usage.record({"input_tokens": 3}, provider="api-key", model="m")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(work).result()
        event = read_events(path)[0]
        assert event.command == "run" and event.scenario == "checkout"
    finally:
        usage_ledger.reset()


def test_configure_from_ai_config_uses_the_configured_path(tmp_path: Path) -> None:
    from bajutsu.config import AiConfig

    path = tmp_path / "custom.jsonl"
    usage_ledger.configure_from_ai_config(AiConfig(usage_ledger=str(path)))
    try:
        with attributed(command="run"):
            usage.record({"input_tokens": 5}, provider="api-key", model="claude-sonnet-4-6")
        assert read_events(path)[0].command == "run"
    finally:
        usage_ledger.reset()


def test_configure_from_ai_config_disables_on_empty_path() -> None:
    from bajutsu.config import AiConfig

    usage_ledger.configure_from_ai_config(AiConfig(usage_ledger=""))
    try:
        # An explicit empty path opts out: no sink, so recording only touches the in-memory total.
        assert usage_ledger._ACTIVE_LEDGER is None
    finally:
        usage_ledger.reset()


def test_configure_from_ai_config_overlays_config_pricing(tmp_path: Path) -> None:
    from bajutsu.config import AiConfig

    path = tmp_path / "usage.jsonl"
    ai = AiConfig(
        usage_ledger=str(path),
        pricing={
            "api-key/sonnet": {"input": 99.0, "output": 0.0, "cacheWrite": 0.0, "cacheRead": 0.0}
        },
    )
    usage_ledger.configure_from_ai_config(ai)
    try:
        with attributed(command="record"):
            usage.record(
                {"input_tokens": 1_000_000}, provider="api-key", model="claude-sonnet-4-6-2025"
            )
        assert read_events(path)[0].cost == 99.0  # the config rate, not the shipped default of 3.0
    finally:
        usage_ledger.reset()
