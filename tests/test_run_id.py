"""Tests for the run-id contract (BE-0200).

The run-id format is a cross-surface contract: the Web UI sorts run history lexicographically
(chronological *only because* ids are zero-padded UTC timestamps) and `report/ctrf.py` parses
the shape back into a start time. These pin that contract — mint, parse, and the
lexicographic-equals-chronological property — so a format change can't silently break either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bajutsu.run_id import RUN_ID_FORMAT, new_run_id, parse_run_id_timestamp


def test_format_constant_is_the_pinned_shape() -> None:
    # Pin the literal shape: the Web UI's lexicographic sort and ctrf's parse both depend on this
    # exact zero-padded UTC form, so any change here must be a deliberate, reviewed edit — not one
    # that slips through while the property tests below keep passing.
    assert RUN_ID_FORMAT == "%Y%m%d-%H%M%S"


def test_mint_parses_back_to_the_minting_instant() -> None:
    # Bound the parsed time between wall-clock reads taken around the mint (floored to the second,
    # since ids carry no sub-second field) rather than a fixed slack — robust under a slow CI pause.
    before = datetime.now(tz=UTC).replace(microsecond=0)
    started = parse_run_id_timestamp(new_run_id())
    after = datetime.now(tz=UTC)
    assert started is not None
    assert started.tzinfo is UTC
    assert before <= started <= after


def test_lexicographic_order_equals_chronological_order() -> None:
    # The property the Web UI relies on: sorting ids as strings orders runs by time. Spanning a
    # day/month/year rollover guards against a separator or field-width regression.
    base = datetime(2026, 12, 31, 23, 59, 58, tzinfo=UTC)
    times = [base + timedelta(seconds=i) for i in (0, 1, 2, 3, 90_000)]
    ids = [t.strftime(RUN_ID_FORMAT) for t in times]
    assert sorted(ids) == ids


def test_parse_round_trips_to_the_second() -> None:
    t = datetime(2026, 6, 13, 15, 30, 45, tzinfo=UTC)
    assert parse_run_id_timestamp(t.strftime(RUN_ID_FORMAT)) == t


def test_prefixed_and_non_timestamp_ids_do_not_parse() -> None:
    # A prefixed id (audit) is deliberately not a bare timestamp, so ctrf gets None rather than a
    # fabricated start — same for any non-timestamp id.
    assert new_run_id("audit-").startswith("audit-")
    assert parse_run_id_timestamp(new_run_id("audit-")) is None
    assert parse_run_id_timestamp("not-a-timestamp") is None
    assert parse_run_id_timestamp("") is None
