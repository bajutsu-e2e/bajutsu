"""The `/usage` AI usage/cost dashboard endpoint (BE-0195).

Renders the self-contained dashboard from the attributed usage ledger `bajutsu.usage_ledger` writes.
Read-only and deterministic — no Mac, no LLM. The ledger is written through the real `JsonlLedger`
(not a stub) and read back through the operation, so the test exercises the true round trip.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _get, _serve

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.usage import TokenUsage
from bajutsu.usage_ledger import JsonlLedger, UsageEvent


def _event(
    *,
    provider: str = "api-key",
    model: str = "claude-sonnet-4-6",
    scenario: str = "login",
    cost: float | None = 0.01,
) -> UsageEvent:
    return UsageEvent(
        ts="2026-07-08T00:00:00+00:00",
        command="run",
        provider=provider,
        model=model,
        scenario=scenario,
        step=None,
        usage=TokenUsage(input_tokens=100, output_tokens=20, calls=1),
        cost=cost,
    )


def _state_with_ledger(tmp_path: Path, ledger: Path) -> srv.ServeState:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(f"defaults:\n  ai:\n    usageLedger: {ledger}\n", encoding="utf-8")
    return srv.ServeState(runs_dir=tmp_path / "runs", config=cfg)


def test_usage_dashboard_reports_recorded_events(tmp_path: Path) -> None:
    ledger_path = tmp_path / "usage.jsonl"
    sink = JsonlLedger(ledger_path)
    sink.append(_event(model="claude-opus-4", scenario="checkout", cost=0.05))
    sink.append(_event(model="claude-opus-4", scenario="checkout", cost=0.03))
    state = _state_with_ledger(tmp_path, ledger_path)

    html, code = ops.usage_html(state)

    assert code == 200
    assert "claude-opus-4" in html
    assert "checkout" in html
    assert "$0.0800" in html  # 0.05 + 0.03 summed and rendered


def test_usage_dashboard_marks_unpriced_calls(tmp_path: Path) -> None:
    ledger_path = tmp_path / "usage.jsonl"
    JsonlLedger(ledger_path).append(_event(provider="ant", cost=None))
    state = _state_with_ledger(tmp_path, ledger_path)

    html, code = ops.usage_html(state)

    assert code == 200
    assert "ant" in html
    assert "unpriced" in html  # tokens shown, dollar figure left absent — no fabricated $0.00


def test_usage_dashboard_empty_state_when_no_ledger(tmp_path: Path) -> None:
    # The ledger file was never created (AI paths never ran) — a clear empty state, not an error.
    state = _state_with_ledger(tmp_path, tmp_path / "absent.jsonl")

    html, code = ops.usage_html(state)

    assert code == 200
    assert "No AI usage recorded yet" in html


def test_usage_route_serves_html_over_http(tmp_path: Path) -> None:
    ledger_path = tmp_path / "usage.jsonl"
    JsonlLedger(ledger_path).append(_event(model="claude-opus-4"))
    state = _state_with_ledger(tmp_path, ledger_path)
    server, port = _serve(state)
    try:
        status, body, content_type = _get(port, "/usage")
        assert status == 200
        assert "text/html" in content_type
        assert b"AI usage" in body and b"claude-opus-4" in body
    finally:
        server.shutdown()
        server.server_close()


def test_usage_dashboard_empty_when_persistence_disabled(tmp_path: Path) -> None:
    # An explicit empty `usageLedger` disables recording; the dashboard reads nothing and says so.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text('defaults:\n  ai:\n    usageLedger: ""\n', encoding="utf-8")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=cfg)

    html, code = ops.usage_html(state)

    assert code == 200
    assert "No AI usage recorded yet" in html
