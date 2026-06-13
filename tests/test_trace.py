"""`bajutsu trace` — the text timeline over a finished run."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu import trace


def _write_run(runs: Path, run_id: str, *, ok: bool = True) -> Path:
    run = runs / run_id
    sid = "00-s"
    (run / sid).mkdir(parents=True)
    manifest = {
        "runId": run_id,
        "ok": ok,
        "backend": "idb",
        "scenarios": [
            {
                "scenario": "s",
                "ok": ok,
                "backend": "idb",
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": True,
                        "reason": "",
                        "duration_s": 0.3,
                        "started_at": 0.0,
                    },
                    {
                        "index": 1,
                        "action": "wait",
                        "ok": ok,
                        "reason": "" if ok else "timeout",
                        "duration_s": 0.1,
                        "started_at": 0.7,
                    },
                ],
                "expect_results": [
                    {
                        "ok": True,
                        "kind": "request",
                        "detail": "request GET status=200",
                        "reason": "",
                    }
                ],
                "failure": None if ok else "expect: no match",
                "artifacts": [
                    {"name": f"{sid}/network.json", "kind": "network", "provider": "collector"},
                    {"name": f"{sid}/appTrace.json", "kind": "appTrace", "provider": "simctl"},
                ],
            }
        ],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / sid / "network.json").write_text(
        json.dumps(
            [
                {
                    "method": "GET",
                    "url": "https://example.com",
                    "status": 200,
                    "durationMs": 150.0,
                    "startedAt": 0.4,
                },
            ]
        ),
        encoding="utf-8",
    )
    (run / sid / "appTrace.json").write_text(
        json.dumps([{"name": "reindex", "durationMs": 1282.3}]), encoding="utf-8"
    )
    return run


def test_trace_run_renders_timeline(tmp_path: Path) -> None:
    out = trace.trace_run(_write_run(tmp_path / "runs", "20260101-000000"))
    assert "bajutsu trace · run 20260101-000000 · PASS · driver: idb" in out
    assert "▸ s   PASS   [idb]" in out
    # Chronological interleave: tap (0.0s) → net (0.4s) → wait (0.7s).
    assert out.index("✓ tap") < out.index("net  GET") < out.index("✓ wait")
    assert "https://example.com → 200" in out
    assert "✓ request" in out
    assert "reindex   1282.3ms" in out
    assert "evidence: appTrace · network" in out


def test_trace_failure_shows_reason(tmp_path: Path) -> None:
    out = trace.trace_run(_write_run(tmp_path / "runs", "20260101-000001", ok=False))
    assert "FAIL" in out and "✗ wait" in out and "timeout" in out
    assert "failure: expect:" in out


def test_latest_run_picks_newest(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, "20260101-000000")
    _write_run(runs, "20260102-000000")
    newest = trace.latest_run(runs)
    assert newest is not None and newest.name == "20260102-000000"
    assert trace.latest_run(tmp_path / "empty") is None


def test_scenario_filter(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", "20260101-000000")
    assert "▸ s" in trace.trace_run(run, "s")
    assert "▸ s" not in trace.trace_run(run, "other")
