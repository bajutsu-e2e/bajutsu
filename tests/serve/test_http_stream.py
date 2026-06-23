"""Tests for the SSE live-log endpoint `/api/jobs/<id>/events` (BE-0015 PR2, real server)."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest
from _shared import _post, _serve, fake_popen, project

from bajutsu import serve as srv


def test_run_events_stream_delivers_lines_and_done(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen(["step 0 ok\n", "PASS  runs/20260610-1/manifest.json\n"]),
    )
    server, port = _serve(state)
    try:
        _, body = _post(port, "/api/run", {"scenario": "smoke.yaml", "target": "demo"})
        jid = body["jobId"]
        # The buffered bus means a subscriber that attaches even after the job finished still
        # replays every line and the terminal event, so reading to EOF is deterministic.
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/jobs/{jid}/events", timeout=5
        ) as r:
            assert "text/event-stream" in r.headers.get("Content-Type", "")
            text = r.read().decode("utf-8")
        assert "event: log" in text
        assert "data: step 0 ok" in text
        assert "event: done" in text
        assert "20260610-1" in text  # the runId rides the terminal `done` payload
    finally:
        server.shutdown()
        server.server_close()


def test_events_unknown_job_is_404(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        with pytest.raises(urllib.error.HTTPError, match="404"):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/nope/events")
    finally:
        server.shutdown()
        server.server_close()
