"""Project run results into a summary and deliver it to each configured channel."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bajutsu.common.config import NotifyEndpoint
from bajutsu.common.orchestrator import RunResult
from bajutsu.common.scenario import interp

from .failure_summary import FailureSummary
from .run_notification import RunNotification

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


def _failure_name(r: RunResult) -> str:
    # The coordinates alone extend the name (BE-0452): a `--browsers` failure kept its bare name
    # before them, and the summary's `engine` already names the engine.
    return f"{r.scenario} [{r.coordinates}]" if r.coordinates else r.scenario


def build_summary(
    results: list[RunResult],
    *,
    run_id: str,
    source_name: str,
    backend: str,
    report_url: str | None = None,
    max_failures: int = 5,
    not_applicable: int = 0,
) -> RunNotification:
    """Project run results into a bounded, format-neutral summary.

    A failure names its run's coordinates as test reports do (BE-0452), so one scenario's runs read
    apart. *not_applicable* counts the records no device fell in range for; an invocation with
    nothing but them executed nothing, so it is not ok.
    """
    passed = sum(1 for r in results if r.ok)
    failed_results = [r for r in results if not r.ok]
    capped = failed_results[:max_failures]
    return RunNotification(
        run_id=run_id,
        ok=all(r.ok for r in results) and bool(results or not not_applicable),
        total=len(results),
        passed=passed,
        failed=len(failed_results),
        source_name=source_name,
        backend=backend,
        duration_s=sum(r.duration_s for r in results),
        failures=[
            FailureSummary(
                scenario=_failure_name(r), failure=r.failure or "failed", duration_s=r.duration_s
            )
            for r in capped
        ],
        failures_remaining=max(0, len(failed_results) - max_failures),
        report_url=report_url,
        engine=next((r.engine for r in results if r.engine), ""),
        not_applicable=not_applicable,
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
                    "text": f"*Result:* {summary.passed} passed, {summary.failed} failed / {summary.total}"
                    + (
                        f", {summary.not_applicable} not applicable"
                        if summary.not_applicable
                        else ""
                    ),
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


def _mask_url(url: str) -> str:
    """Mask a webhook URL for safe logging (host only, path/query redacted)."""
    try:
        parsed = urllib.parse.urlparse(url)
        # The f-string is inside the guard on purpose: a urlparse result whose `hostname` access
        # raises must still mask to "***" rather than leak the URL through the traceback.
        return f"{parsed.scheme}://{parsed.hostname}/***"  # noqa: TRY300
    except Exception:
        return "***"


def _mask_endpoint(ep_url: str) -> str:
    """Mask an endpoint's *un-interpolated* URL template for safe, distinguishing log labels.

    The documented config shape puts the whole URL inside a secret token
    (``url: "${secrets.SLACK_WEBHOOK_URL}"``, docs/configuration.md), so ``ep_url`` alone can
    carry no ``://`` for `_mask_url` to find a host in — it would degrade every such endpoint to
    the same "://None/***" label. Falling back to the template string itself keeps endpoints
    distinguishable; it only ever names a secret, never holds one.
    """
    return _mask_url(ep_url) if "://" in ep_url else f"endpoint {ep_url!r}"


def _deliver(url: str, payload: dict[str, Any], *, masked: str) -> bool:
    """POST JSON to *url* with bounded timeout and retry. Returns success.

    *masked* is precomputed by the caller from the endpoint's un-interpolated URL
    template, not from *url* itself: *url* has secret bindings resolved into it, and
    deriving the log label from it would keep that secret-tainted value on the path to
    every log call below (even through `_mask_url`), which static analysis flags as
    clear-text logging of sensitive data (CWE-312/532).
    """
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
                    masked,
                    resp.status,
                    attempt + 1,
                )
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("webhook POST to %s failed (attempt %d): %s", masked, attempt + 1, exc)
        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY * (attempt + 1))
    logger.warning(
        "webhook POST to %s failed after %d attempts; giving up", masked, _MAX_RETRIES + 1
    )
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
    not_applicable: Sequence[Mapping[str, str]] = (),
) -> bool:
    """Post run notifications to all matching endpoints. Returns whether any fired.

    Called after the verdict is fixed. A delivery failure is logged, never raised. *not_applicable*
    holds the records of the runs no available device fell in range for (BE-0452); an endpoint
    scoped by `targets:` counts only its own scenarios' records.
    """
    if not endpoints:
        return False

    any_fired = False
    summary = build_summary(
        results,
        run_id=run_id,
        source_name=source_name,
        backend=backend,
        report_url=report_url,
        not_applicable=len(not_applicable),
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
                    not_applicable=sum(
                        1 for rec in not_applicable if rec.get("scenario") in ep.targets
                    ),
                )

            if not _should_fire(ep, ep_summary, prior_ok):
                continue

            masked = _mask_endpoint(ep.url)
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
            if _deliver(url, payload, masked=masked):
                any_fired = True
        except Exception:
            logger.warning(
                "webhook notification failed for %s",
                _mask_endpoint(ep.url),
                exc_info=True,
            )

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

            masked = _mask_endpoint(ep.url)
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
            if _deliver(url, payload, masked=masked):
                any_fired = True
        except Exception:
            logger.warning(
                "webhook start notification failed for %s",
                _mask_endpoint(ep.url),
                exc_info=True,
            )

    return any_fired
