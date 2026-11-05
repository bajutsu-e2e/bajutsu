"""Webhook notifications for run results (BE-0099)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from bajutsu.common.config import NotifyEndpoint
from bajutsu.common.orchestrator import RunResult
from bajutsu.run.notify import (
    FailureSummary,
    RunNotification,
    _deliver,
    _find_prior_verdict,
    _render_slack,
    _render_slack_start,
    _should_fire,
    build_summary,
    emit,
    emit_start,
)


def _res(name: str, ok: bool, failure: str | None = None, duration: float = 1.0) -> RunResult:
    return RunResult(scenario=name, ok=ok, steps=[], failure=failure, duration_s=duration)


def _endpoint(
    on: list[str] | None = None, targets: list[str] | None = None, url: str = "https://hook"
) -> NotifyEndpoint:
    return NotifyEndpoint(format="slack", url=url, on=on or ["failure"], targets=targets or [])


# --- build_summary ---


def test_build_summary_all_pass() -> None:
    results = [_res("login", True), _res("checkout", True)]
    s = build_summary(
        results, run_id="20260630-120000", source_name="smoke.yaml", backend="xcuitest"
    )
    assert s.ok is True
    assert s.total == 2
    assert s.passed == 2
    assert s.failed == 0
    assert s.failures == []
    assert s.failures_remaining == 0


def test_build_summary_with_failures() -> None:
    results = [
        _res("login", True),
        _res("checkout", False, "value mismatch"),
        _res("pay", False, "timeout"),
    ]
    s = build_summary(results, run_id="r1", source_name="smoke.yaml", backend="xcuitest")
    assert s.ok is False
    assert s.total == 3
    assert s.passed == 1
    assert s.failed == 2
    assert len(s.failures) == 2
    assert s.failures[0]["scenario"] == "checkout"
    assert s.failures[0]["failure"] == "value mismatch"
    assert s.failures_remaining == 0


def test_build_summary_failure_cap() -> None:
    results = [_res(f"s{i}", False, f"err{i}") for i in range(8)]
    s = build_summary(results, run_id="r1", source_name="s.yaml", backend="fake", max_failures=3)
    assert len(s.failures) == 3
    assert s.failures_remaining == 5


def test_build_summary_report_url() -> None:
    results = [_res("s", True)]
    s = build_summary(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        report_url="https://host/runs/r1",
    )
    assert s.report_url == "https://host/runs/r1"


def test_build_summary_duration() -> None:
    results = [_res("a", True, duration=2.5), _res("b", True, duration=3.5)]
    s = build_summary(results, run_id="r1", source_name="s.yaml", backend="xcuitest")
    assert s.duration_s == 6.0


# --- _should_fire ---


def test_should_fire_failure_on_fail() -> None:
    ep = _endpoint(on=["failure"])
    s = RunNotification(
        run_id="r1",
        ok=False,
        total=1,
        passed=0,
        failed=1,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=None) is True


def test_should_fire_failure_on_pass() -> None:
    ep = _endpoint(on=["failure"])
    s = RunNotification(
        run_id="r1",
        ok=True,
        total=1,
        passed=1,
        failed=0,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=None) is False


def test_should_fire_always() -> None:
    ep = _endpoint(on=["always"])
    s = RunNotification(
        run_id="r1",
        ok=True,
        total=1,
        passed=1,
        failed=0,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=None) is True


def test_should_fire_change_no_prior() -> None:
    ep = _endpoint(on=["change"])
    s = RunNotification(
        run_id="r1",
        ok=True,
        total=1,
        passed=1,
        failed=0,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=None) is True


def test_should_fire_change_same_verdict() -> None:
    ep = _endpoint(on=["change"])
    s = RunNotification(
        run_id="r1",
        ok=True,
        total=1,
        passed=1,
        failed=0,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=True) is False


def test_should_fire_change_flipped() -> None:
    ep = _endpoint(on=["change"])
    s = RunNotification(
        run_id="r1",
        ok=False,
        total=1,
        passed=0,
        failed=1,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=True) is True


def test_should_fire_recovery_flipped() -> None:
    ep = _endpoint(on=["recovery"])
    s = RunNotification(
        run_id="r1",
        ok=True,
        total=1,
        passed=1,
        failed=0,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=False) is True


def test_should_fire_multiple_events() -> None:
    ep = _endpoint(on=["failure", "change"])
    s = RunNotification(
        run_id="r1",
        ok=False,
        total=2,
        passed=1,
        failed=1,
        source_name="s",
        backend="xcuitest",
        duration_s=1.0,
        failures=[FailureSummary(scenario="checkout", failure="err", duration_s=1.0)],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    assert _should_fire(ep, s, prior_ok=None) is True


# --- _find_prior_verdict ---


def test_find_prior_verdict_found(tmp_path: Path) -> None:
    prev = tmp_path / "20260629-120000"
    prev.mkdir()
    (prev / "manifest.json").write_text(
        json.dumps({"ok": True, "sourceName": "smoke.yaml"}), encoding="utf-8"
    )
    assert _find_prior_verdict(tmp_path, "smoke.yaml", "20260630-120000") is True


def test_find_prior_verdict_none(tmp_path: Path) -> None:
    assert _find_prior_verdict(tmp_path, "smoke.yaml", "20260630-120000") is None


def test_find_prior_verdict_skips_current(tmp_path: Path) -> None:
    curr = tmp_path / "20260630-120000"
    curr.mkdir()
    (curr / "manifest.json").write_text(
        json.dumps({"ok": False, "sourceName": "smoke.yaml"}), encoding="utf-8"
    )
    assert _find_prior_verdict(tmp_path, "smoke.yaml", "20260630-120000") is None


def test_find_prior_verdict_most_recent(tmp_path: Path) -> None:
    for rid, verdict in [("20260628-100000", True), ("20260629-100000", False)]:
        d = tmp_path / rid
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"ok": verdict, "sourceName": "s.yaml"}), encoding="utf-8"
        )
    assert _find_prior_verdict(tmp_path, "s.yaml", "20260630-120000") is False


# --- _render_slack ---


def test_render_slack_pass() -> None:
    s = RunNotification(
        run_id="20260630-120000",
        ok=True,
        total=5,
        passed=5,
        failed=0,
        source_name="smoke.yaml",
        backend="xcuitest",
        duration_s=12.3,
        failures=[],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    payload = _render_slack(s)
    assert "text" in payload
    assert "PASS" in payload["text"]
    assert "5" in payload["text"]
    assert "blocks" in payload


def test_render_slack_fail_with_url() -> None:
    s = RunNotification(
        run_id="r1",
        ok=False,
        total=3,
        passed=1,
        failed=2,
        source_name="smoke.yaml",
        backend="xcuitest",
        duration_s=8.0,
        failures=[
            FailureSummary(scenario="checkout", failure="value mismatch", duration_s=2.0),
            FailureSummary(scenario="pay", failure="timeout", duration_s=3.0),
        ],
        failures_remaining=0,
        report_url="https://host/runs/r1",
        engine="",
    )
    payload = _render_slack(s)
    assert "FAIL" in payload["text"]
    text = json.dumps(payload)
    assert "checkout" in text
    assert "https://host/runs/r1" in text


def test_render_slack_fail_no_url() -> None:
    s = RunNotification(
        run_id="r1",
        ok=False,
        total=1,
        passed=0,
        failed=1,
        source_name="s.yaml",
        backend="xcuitest",
        duration_s=1.0,
        failures=[FailureSummary(scenario="s", failure="err", duration_s=1.0)],
        failures_remaining=0,
        report_url=None,
        engine="",
    )
    payload = _render_slack(s)
    text = json.dumps(payload)
    assert "report" not in text.lower()


# --- _render_slack_start ---


def test_render_slack_start() -> None:
    payload = _render_slack_start(
        run_id="20260630-120000",
        source_name="smoke.yaml",
        target="sample",
        scenario_count=5,
    )
    assert "text" in payload
    text = json.dumps(payload)
    assert "smoke.yaml" in text
    assert "5" in text


# --- _deliver ---


def test_deliver_success(monkeypatch: Any) -> None:
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda self: self
    resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: resp)
    assert _deliver("https://hook", {"text": "hi"}, masked="https://hook/***") is True


def test_deliver_retry_on_error(monkeypatch: Any) -> None:
    call_count = 0

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.HTTPError("https://hook", 500, "ISE", {}, None)  # type: ignore[arg-type]
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("bajutsu.run.notify._functions._RETRY_DELAY", 0.0)
    assert _deliver("https://hook", {"text": "hi"}, masked="https://hook/***") is True
    assert call_count == 2


def test_deliver_never_raises(monkeypatch: Any) -> None:
    def explode(req: Any, timeout: Any = None) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    monkeypatch.setattr("bajutsu.run.notify._functions._RETRY_DELAY", 0.0)
    assert _deliver("https://hook", {"text": "hi"}, masked="https://hook/***") is False


# --- emit (integration) ---


def test_emit_fires_on_failure(monkeypatch: Any) -> None:
    captured: list[bytes] = []

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        captured.append(req.data)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("login", True), _res("checkout", False, "mismatch")]
    fired = emit(
        results,
        run_id="r1",
        source_name="smoke.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is True
    assert len(captured) == 1
    payload = json.loads(captured[0])
    assert "FAIL" in payload["text"]


def test_emit_noop_on_pass(monkeypatch: Any) -> None:
    called = False

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("login", True)]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is False
    assert called is False


def test_emit_url_interpolation(monkeypatch: Any) -> None:
    captured_urls: list[str] = []

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        captured_urls.append(req.full_url)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("s", False, "err")]
    emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"], url="${secrets.HOOK}")],
        bindings={"secrets.HOOK": "https://resolved.hook/post"},
        runs_dir=Path("/nonexistent"),
    )
    assert captured_urls == ["https://resolved.hook/post"]


def test_emit_start_fires(monkeypatch: Any) -> None:
    captured: list[bytes] = []

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        captured.append(req.data)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    fired = emit_start(
        run_id="r1",
        source_name="smoke.yaml",
        target="sample",
        scenario_count=5,
        endpoints=[_endpoint(on=["start"])],
        bindings={},
    )
    assert fired is True
    assert len(captured) == 1


def test_emit_targets_filter_excludes_unrelated_failures(monkeypatch: Any) -> None:
    called = False

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("login", True), _res("checkout", False, "err")]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"], targets=["login"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is False
    assert called is False


def test_emit_targets_filter_fires_when_targeted_fails(monkeypatch: Any) -> None:
    captured: list[bytes] = []

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        captured.append(req.data)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("login", True), _res("checkout", False, "err")]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"], targets=["checkout"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is True
    assert len(captured) == 1


def test_emit_targets_filter_skips_when_no_match(monkeypatch: Any) -> None:
    called = False

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("login", True), _res("checkout", False, "err")]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["always"], targets=["payment"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is False
    assert called is False


def test_emit_start_skips_non_start_endpoints(monkeypatch: Any) -> None:
    called = False

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    fired = emit_start(
        run_id="r1",
        source_name="s.yaml",
        target="sample",
        scenario_count=3,
        endpoints=[_endpoint(on=["failure"])],
        bindings={},
    )
    assert fired is False
    assert called is False


# --- Review-driven edge case tests ---


def test_deliver_exhausts_all_retries(monkeypatch: Any) -> None:
    call_count = 0

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError("https://hook", 500, "ISE", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("bajutsu.run.notify._functions._RETRY_DELAY", 0.0)
    assert _deliver("https://hook", {"text": "hi"}, masked="https://hook/***") is False
    assert call_count == 3  # initial + 2 retries


def test_emit_delivery_failure_returns_false(monkeypatch: Any) -> None:
    def explode(req: Any, timeout: Any = None) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    monkeypatch.setattr("bajutsu.run.notify._functions._RETRY_DELAY", 0.0)
    results = [_res("s", False, "err")]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is False


def test_find_prior_verdict_corrupt_manifest(tmp_path: Path) -> None:
    corrupt = tmp_path / "20260629-120000"
    corrupt.mkdir()
    (corrupt / "manifest.json").write_text("not valid json", encoding="utf-8")
    valid = tmp_path / "20260628-120000"
    valid.mkdir()
    (valid / "manifest.json").write_text(
        json.dumps({"ok": True, "sourceName": "s.yaml"}), encoding="utf-8"
    )
    assert _find_prior_verdict(tmp_path, "s.yaml", "20260630-120000") is True


def test_find_prior_verdict_different_source(tmp_path: Path) -> None:
    other = tmp_path / "20260629-120000"
    other.mkdir()
    (other / "manifest.json").write_text(
        json.dumps({"ok": False, "sourceName": "other.yaml"}), encoding="utf-8"
    )
    assert _find_prior_verdict(tmp_path, "s.yaml", "20260630-120000") is None


def test_emit_unresolved_url_skips(monkeypatch: Any) -> None:
    called = False

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("s", False, "err")]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["failure"], url="${secrets.MISSING}")],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is False
    assert called is False


def test_emit_mixed_start_and_failure(monkeypatch: Any) -> None:
    captured: list[bytes] = []

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        captured.append(req.data)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    results = [_res("s", False, "err")]
    fired = emit(
        results,
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[_endpoint(on=["start", "failure"])],
        bindings={},
        runs_dir=Path("/nonexistent"),
    )
    assert fired is True


# --- CodeQL clear-text-logging regression (alerts #71-73) ---


def test_emit_failure_logs_never_contain_resolved_secret(monkeypatch: Any, caplog: Any) -> None:
    """The failure/retry log label must come from the endpoint's un-interpolated URL
    template, never from the secret-resolved URL — else the webhook secret (or the host
    it embeds, per the documented ``url: "${secrets.SLACK_WEBHOOK_URL}"`` shape) ends up
    in clear text in the logs.
    """

    def explode(req: Any, timeout: Any = None) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    monkeypatch.setattr("bajutsu.run.notify._functions._RETRY_DELAY", 0.0)

    secret_url = "https://hooks.slack.com/services/T00/B00/SECRETVALUE"
    results = [_res("s", False, "err")]
    with caplog.at_level(logging.WARNING, logger="bajutsu.run.notify"):
        fired = emit(
            results,
            run_id="r1",
            source_name="s.yaml",
            backend="xcuitest",
            endpoints=[_endpoint(on=["failure"], url="${secrets.HOOK}")],
            bindings={"secrets.HOOK": secret_url},
            runs_dir=Path("/nonexistent"),
        )

    assert fired is False
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRETVALUE" not in log_text
    assert "hooks.slack.com" not in log_text
    assert "${secrets.HOOK}" in log_text


def test_emit_unexpected_error_log_names_endpoint_once(monkeypatch: Any, caplog: Any) -> None:
    """``emit``'s catch-all handler labels the endpoint via ``_mask_endpoint``, whose
    fallback branch already prefixes the word "endpoint" — so the template must not repeat
    it, and must still name the secret token rather than its resolved value.
    """

    def explode(summary: Any) -> Any:
        raise RuntimeError("renderer blew up")

    monkeypatch.setattr("bajutsu.run.notify._functions._render_slack", explode)

    secret_url = "https://hooks.slack.com/services/T00/B00/SECRETVALUE"
    results = [_res("s", False, "err")]
    with caplog.at_level(logging.WARNING, logger="bajutsu.run.notify"):
        fired = emit(
            results,
            run_id="r1",
            source_name="s.yaml",
            backend="xcuitest",
            endpoints=[_endpoint(on=["failure"], url="${secrets.HOOK}")],
            bindings={"secrets.HOOK": secret_url},
            runs_dir=Path("/nonexistent"),
        )

    assert fired is False
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "endpoint endpoint" not in log_text
    assert "SECRETVALUE" not in log_text
    assert "hooks.slack.com" not in log_text
    assert "endpoint '${secrets.HOOK}'" in log_text


def test_emit_start_failure_logs_never_contain_resolved_secret(
    monkeypatch: Any, caplog: Any
) -> None:
    """Same regression as above (BE-0099 'start' event path)."""

    def explode(req: Any, timeout: Any = None) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    monkeypatch.setattr("bajutsu.run.notify._functions._RETRY_DELAY", 0.0)

    secret_url = "https://hooks.slack.com/services/T00/B00/SECRETVALUE"
    with caplog.at_level(logging.WARNING, logger="bajutsu.run.notify"):
        fired = emit_start(
            run_id="r1",
            source_name="smoke.yaml",
            target="sample",
            scenario_count=5,
            endpoints=[_endpoint(on=["start"], url="${secrets.HOOK}")],
            bindings={"secrets.HOOK": secret_url},
        )

    assert fired is False
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRETVALUE" not in log_text
    assert "hooks.slack.com" not in log_text
    assert "${secrets.HOOK}" in log_text


def test_emit_targets_filter_counts_only_its_own_not_applicable_records(monkeypatch: Any) -> None:
    # BE-0452: an endpoint scoped by `targets:` reports the not-applicable records of its own
    # scenarios, as the unscoped endpoint reports all of them.
    captured: list[bytes] = []

    def fake_urlopen(req: Any, timeout: Any = None) -> Any:
        captured.append(req.data)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    records = [
        {"scenario": "checkout", "reason": "no device"},
        {"scenario": "login", "reason": "no device"},
        {"scenario": "login", "reason": "no device"},
    ]
    emit(
        [_res("login", False, "err"), _res("checkout", False, "err")],
        run_id="r1",
        source_name="s.yaml",
        backend="xcuitest",
        endpoints=[
            _endpoint(on=["failure"], url="https://all"),
            _endpoint(on=["failure"], targets=["checkout"], url="https://checkout"),
        ],
        bindings={},
        runs_dir=Path("/nonexistent"),
        not_applicable=records,
    )
    assert len(captured) == 2
    assert b"3 not applicable" in captured[0]
    assert b"1 not applicable" in captured[1]
