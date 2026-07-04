"""Common Test Report Format (CTRF) export (BE-0161).

A pure, deterministic projection of the run's result model into a CTRF document
(https://ctrf.io/) — the same input as `junit_xml()`, a richer output beside it. CTRF is an
open-standard JSON schema (`reportFormat: "CTRF"`, a `results` object of `tool` / `summary` /
`tests`) that a growing ecosystem — PR-comment reporters, dashboards, flaky-test analytics —
reads without per-tool adapters, so Bajutsu's structured extras (steps, engine, device,
attachments) survive the trip in a way JUnit XML can't carry.

Being a post-verdict serialization of already-decided results, it sits outside the
determinism-first contract by construction: no LLM, and writing it can't move the verdict.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from platform import release

from bajutsu import __version__
from bajutsu.orchestrator import RunResult, StepOutcome
from bajutsu.report.manifest import _details, _matrix

# The CTRF spec version this projection targets; the vendored test schema is pinned to it.
SPEC_VERSION = "0.0.0"

# Artifact `kind` → MIME content type. Unknown kinds fall back to a safe octet-stream, so a new
# evidence kind still exports (as an opaque attachment) rather than breaking the document.
_ARTIFACT_MIME = {
    "video": "video/mp4",
    "screenshot": "image/png",
    "deviceLog": "text/plain",
    "elements": "application/json",
    "network": "application/json",
    "appTrace": "application/json",
}
_DEFAULT_MIME = "application/octet-stream"


def _content_type(kind: str) -> str:
    return _ARTIFACT_MIME.get(kind, _DEFAULT_MIME)


def _run_start_ms(run_id: str) -> int:
    """The run's start as epoch milliseconds, parsed from the `YYYYmmdd-HHMMSS` runId.

    The runId is stamped in UTC (`bajutsu run`), so it is parsed as UTC — exact to the second for
    a real run, and 0 when the id isn't a timestamp (e.g. a test runId), since CTRF requires
    `summary.start`/`stop` as integers and a fabricated value would mislead.
    """
    try:
        return int(
            datetime.strptime(run_id, "%Y%m%d-%H%M%S").replace(tzinfo=UTC).timestamp() * 1000
        )
    except ValueError:
        return 0


def _ms(seconds: float) -> int:
    """Seconds → whole milliseconds (CTRF's duration unit)."""
    return int(seconds * 1000)


def _status(ok: bool) -> str:
    """The only two states Bajutsu emits; CTRF's other states stay at zero."""
    return "passed" if ok else "failed"


def _test_name(r: RunResult) -> str:
    """The scenario name, suffixed with the engine on a cross-browser cell (BE-0076)."""
    return f"{r.scenario} [{r.engine}]" if r.engine else r.scenario


def _device(r: RunResult) -> str:
    """The model + runtime label for the CTRF `device` field, e.g. `iPhone 15 (iOS 17.2)`."""
    if r.device_name and r.device_runtime:
        return f"{r.device_name} ({r.device_runtime})"
    return r.device_name or r.device_runtime


def _step(s: StepOutcome) -> dict[str, object]:
    """One CTRF step — name/status only at the top level, everything richer into `extra`.

    A CTRF step allows just `{name, status, extra}`, so Bajutsu's duration / reason / per-step
    assertions / artifacts (all richer than the schema's top level) are preserved under `extra`.
    """
    extra: dict[str, object] = {"index": s.index, "duration": _ms(s.duration_s)}
    if s.reason:
        extra["reason"] = s.reason
    if s.assertion_results:
        extra["assertions"] = [
            {"ok": a.ok, "kind": a.kind, "detail": a.detail, "reason": a.reason}
            for a in s.assertion_results
        ]
    if s.artifacts:
        extra["artifacts"] = [{"name": a.name, "kind": a.kind} for a in s.artifacts]
    return {"name": s.action, "status": _status(s.ok), "extra": extra}


def _attachments(r: RunResult) -> list[dict[str, object]]:
    """Scenario-level artifacts as CTRF attachments, MIME-typed by kind.

    Paths stay run-directory relative, matching how `manifest.json` records them.
    """
    return [
        {"name": a.name, "contentType": _content_type(a.kind), "path": a.name} for a in r.artifacts
    ]


def _test_extra(r: RunResult) -> dict[str, object]:
    """Bajutsu surplus with no first-class CTRF home, kept under the test's `extra`."""
    extra: dict[str, object] = {"backend": r.backend}
    if r.sid:
        extra["sid"] = r.sid
    if r.expect_results:
        extra["expect"] = [
            {"ok": a.ok, "kind": a.kind, "detail": a.detail, "reason": a.reason}
            for a in r.expect_results
        ]
    if r.expect_alerts:
        extra["expectAlerts"] = [{"label": a.label} for a in r.expect_alerts]
    if r.skipped_captures:
        extra["skippedCaptures"] = [
            {"kind": c.kind, "reason": c.reason} for c in r.skipped_captures
        ]
    return extra


def _test(r: RunResult) -> dict[str, object]:
    test: dict[str, object] = {
        "name": _test_name(r),
        "status": _status(r.ok),
        "duration": _ms(r.duration_s),
        "steps": [_step(s) for s in r.steps],
        "extra": _test_extra(r),
    }
    if not r.ok:
        test["message"] = r.failure or "failed"
        test["trace"] = _details(r)
    if r.engine:
        test["browser"] = r.engine
    if device := _device(r):
        test["device"] = device
    if attachments := _attachments(r):
        test["attachments"] = attachments
    return test


def _environment(provenance: dict[str, object] | None) -> dict[str, object]:
    """Best-effort run environment: the commit from provenance, plus the host OS at export time."""
    env: dict[str, object] = {"osPlatform": sys.platform, "osRelease": release()}
    if provenance and (commit := provenance.get("gitRevision")):
        env["commit"] = commit
    return env


def _summary(run_id: str, results: list[RunResult]) -> dict[str, object]:
    passed = sum(1 for r in results if r.ok)
    duration = sum(_ms(r.duration_s) for r in results)
    start = _run_start_ms(run_id)
    return {
        "tests": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "skipped": 0,
        "pending": 0,
        "other": 0,
        "start": start,
        "stop": start + duration,
        "duration": duration,
    }


def ctrf_json(
    run_id: str,
    results: list[RunResult],
    *,
    provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    """Project the run's result model into a CTRF document (BE-0161).

    Reads the same in-memory model the JUnit/HTML reporters build, so iOS, web, and future
    backends export identically with no per-app branching. `provenance` is the manifest's
    run-identity stamp (BE-0049); its `toolVersion` / `gitRevision` feed `tool.version` and
    `environment.commit`, and it is None for a run without the stamp.

    Args:
        run_id: The run's `YYYYmmdd-HHMMSS` id, used for `summary.start`.
        results: One `RunResult` per scenario — or per engine x scenario cell on a matrix run,
            each becoming a CTRF test with the engine in its name and `browser`.
        provenance: The manifest provenance block, or None.

    Returns:
        The CTRF document as a plain dict, ready to serialize beside `manifest.json`.
    """
    tool: dict[str, object] = {"name": "bajutsu"}
    tool["version"] = (provenance or {}).get("toolVersion") or __version__
    inner: dict[str, object] = {
        "tool": tool,
        "summary": _summary(run_id, results),
        "tests": [_test(r) for r in results],
        "environment": _environment(provenance),
    }
    # The engine x scenario matrix (BE-0076) has no first-class CTRF home; carry the aggregate under
    # `results.extra`, omitted for a single-engine / iOS run (which keeps the plain shape).
    if (matrix := _matrix(results)) is not None:
        inner["extra"] = {"matrix": matrix}
    return {
        "reportFormat": "CTRF",
        "specVersion": SPEC_VERSION,
        "generatedBy": "bajutsu",
        "timestamp": datetime.now(UTC).isoformat(),
        "results": inner,
    }
