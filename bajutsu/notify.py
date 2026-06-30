"""Webhook notifications for run results (BE-0099).

A post-verdict side effect: builds a format-neutral summary from the run data,
filters by configured events, renders to the target format (Slack Block Kit first),
and POSTs to the configured URL. Delivery failures are logged as warnings, never able
to change the verdict or exit code. No LLM, no effect on the deterministic gate.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from bajutsu import interp
from bajutsu.config import NotifyEndpoint
from bajutsu.orchestrator import RunResult

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


# ---------------------------------------------------------------------------
# Summary model (format-neutral)
# ---------------------------------------------------------------------------


class FailureSummary(TypedDict):
    scenario: str
    failure: str
    duration_s: float


@dataclass
class RunNotification:
    """The format-neutral summary projected from run results."""

    run_id: str
    ok: bool
    total: int
    passed: int
    failed: int
    source_name: str
    backend: str
    duration_s: float
    failures: list[FailureSummary]
    failures_remaining: int
    report_url: str | None
    engine: str


def build_summary(
    results: list[RunResult],
    *,
    run_id: str,
    source_name: str,
    backend: str,
    report_url: str | None = None,
    max_failures: int = 5,
) -> RunNotification:
    """Project run results into a bounded, format-neutral summary."""
    passed = sum(1 for r in results if r.ok)
    failed_results = [r for r in results if not r.ok]
    capped = failed_results[:max_failures]
    return RunNotification(
        run_id=run_id,
        ok=all(r.ok for r in results),
        total=len(results),
        passed=passed,
        failed=len(failed_results),
        source_name=source_name,
        backend=backend,
        duration_s=sum(r.duration_s for r in results),
        failures=[
            FailureSummary(
                scenario=r.scenario, failure=r.failure or "failed", duration_s=r.duration_s
            )
            for r in capped
        ],
        failures_remaining=max(0, len(failed_results) - max_failures),
        report_url=report_url,
        engine=next((r.engine for r in results if r.engine), ""),
    )


# ---------------------------------------------------------------------------
# Event matching
# ---------------------------------------------------------------------------


def _should_fire(
    endpoint: NotifyEndpoint,
    summary: RunNotification,
    prior_ok: bool | None,
) -> bool:
    """Whether this endpoint's event filter matches the current run.

    The ``targets`` filter is applied upstream (``emit`` builds a filtered summary
    before calling this), so this function only checks event conditions.
    """
    events = set(endpoint.on)
    if "always" in events:
        return True
    if "failure" in events and not summary.ok:
        return True
    return ("change" in events or "recovery" in events) and (
        prior_ok is None or prior_ok != summary.ok
    )


def _find_prior_verdict(
    runs_dir: Path,
    source_name: str,
    current_run_id: str,
) -> bool | None:
    """The previous run's overall verdict for the same source, or None if no prior run."""
    if not runs_dir.is_dir():
        return None
    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir() and d.name != current_run_id),
        key=lambda d: d.name,
        reverse=True,
    )
    for d in candidates:
        manifest = d / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            logger.debug("skipping prior-run manifest %s: %s", manifest, exc)
            continue
        if data.get("sourceName") == source_name:
            return bool(data.get("ok"))
    return None


# ---------------------------------------------------------------------------
# Slack renderer
# ---------------------------------------------------------------------------


def _render_slack(summary: RunNotification) -> dict[str, Any]:
    """Build a Slack Block Kit message payload from the summary."""
    emoji = "✅" if summary.ok else "❌"
    verdict = "PASS" if summary.ok else "FAIL"
    fallback = f"{emoji} bajutsu {verdict} — {summary.passed}/{summary.total} passed ({summary.source_name})"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} bajutsu — {verdict}", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Source:* {summary.source_name}"},
                {"type": "mrkdwn", "text": f"*Backend:* {summary.backend}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Result:* {summary.passed} passed, {summary.failed} failed / {summary.total}",
                },
                {"type": "mrkdwn", "text": f"*Duration:* {summary.duration_s:.1f}s"},
            ],
        },
    ]

    if summary.failures:
        lines = [f"• *{f['scenario']}*: {f['failure']}" for f in summary.failures]
        if summary.failures_remaining > 0:
            lines.append(f"_… and {summary.failures_remaining} more_")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    if summary.report_url:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{summary.report_url}|View report>"},
            }
        )

    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Run `{summary.run_id}`"}]}
    )

    return {"text": fallback, "blocks": blocks}


def _render_slack_start(
    *,
    run_id: str,
    source_name: str,
    target: str,
    scenario_count: int,
) -> dict[str, Any]:
    """Build a Slack Block Kit message for the 'start' event."""
    fallback = f"▶️ bajutsu run starting — {source_name} ({scenario_count} scenarios)"
    return {
        "text": fallback,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"▶️ *bajutsu run starting*\n"
                        f"*Source:* {source_name} · *Target:* {target} · "
                        f"*Scenarios:* {scenario_count}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Run `{run_id}`"}],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _deliver(url: str, payload: dict[str, Any]) -> bool:
    """POST JSON to *url* with bounded timeout and retry. Returns success."""
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(_MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(  # noqa: S310 — the URL comes from team config, not user input
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
                if resp.status < 300:
                    return True
                logger.warning(
                    "webhook POST to %s returned status %d (attempt %d)",
                    url,
                    resp.status,
                    attempt + 1,
                )
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            logger.warning("webhook POST to %s failed (attempt %d): %s", url, attempt + 1, exc)
        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY * (attempt + 1))
    logger.warning("webhook POST to %s failed after %d attempts; giving up", url, _MAX_RETRIES + 1)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def emit(
    results: list[RunResult],
    *,
    run_id: str,
    source_name: str,
    backend: str,
    endpoints: list[NotifyEndpoint],
    bindings: Mapping[str, str],
    runs_dir: Path,
    report_url: str | None = None,
) -> bool:
    """Post run notifications to all matching endpoints. Returns whether any fired.

    Called after the verdict is fixed. A delivery failure is logged, never raised.
    """
    if not endpoints:
        return False

    any_fired = False
    summary = build_summary(
        results, run_id=run_id, source_name=source_name, backend=backend, report_url=report_url
    )

    for ep in endpoints:
        try:
            if "start" in ep.on and len(ep.on) == 1:
                continue

            prior_ok: bool | None = None
            if "change" in ep.on or "recovery" in ep.on:
                prior_ok = _find_prior_verdict(runs_dir, source_name, run_id)

            ep_summary = summary
            if ep.targets:
                filtered = [r for r in results if r.scenario in ep.targets]
                if not filtered:
                    continue
                ep_summary = build_summary(
                    filtered,
                    run_id=run_id,
                    source_name=source_name,
                    backend=backend,
                    report_url=report_url,
                )

            if not _should_fire(ep, ep_summary, prior_ok):
                continue

            url = str(interp.interpolate(ep.url, bindings))
            if "${" in url:
                logger.warning(
                    "webhook URL %r has unresolved tokens; "
                    "check that the required secrets are in the environment",
                    ep.url,
                )
                continue

            renderer = {"slack": _render_slack}.get(ep.format)
            if renderer is None:
                logger.warning("unknown notify format %r; skipping", ep.format)
                continue

            payload = renderer(ep_summary)
            if _deliver(url, payload):
                any_fired = True
        except Exception:
            logger.warning("webhook notification failed for endpoint %s", ep.url, exc_info=True)

    return any_fired


def emit_start(
    *,
    run_id: str,
    source_name: str,
    target: str,
    scenario_count: int,
    endpoints: list[NotifyEndpoint],
    bindings: Mapping[str, str],
) -> bool:
    """Fire the 'start' event for endpoints that subscribe to it."""
    if not endpoints:
        return False

    any_fired = False
    for ep in endpoints:
        try:
            if "start" not in ep.on:
                continue

            url = str(interp.interpolate(ep.url, bindings))
            if "${" in url:
                logger.warning(
                    "webhook URL %r has unresolved tokens; "
                    "check that the required secrets are in the environment",
                    ep.url,
                )
                continue

            start_renderers = {"slack": _render_slack_start}
            renderer = start_renderers.get(ep.format)
            if renderer is None:
                logger.warning("unknown notify format %r; skipping", ep.format)
                continue

            payload = renderer(
                run_id=run_id,
                source_name=source_name,
                target=target,
                scenario_count=scenario_count,
            )
            if _deliver(url, payload):
                any_fired = True
        except Exception:
            logger.warning("webhook start notification failed for %s", ep.url, exc_info=True)

    return any_fired
