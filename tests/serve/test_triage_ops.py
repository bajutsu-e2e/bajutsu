"""Operations-level tests for the serve triage endpoint (BE-0147).

Validation and dispatch wiring, driven directly against `start_triage` — no HTTP server. The
device/AI boundary is the only thing stubbed (a fake Popen, a credential-gap override); the
heuristic diagnosis itself runs for real in the HTTP integration test.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from _shared import FakeProc, project

from bajutsu.serve import operations as ops
from bajutsu.serve.state import ServeState


def _state(tmp_path: Path, popen: Any = None) -> ServeState:
    scn_dir, cfg, runs = project(tmp_path)
    kw = {"popen": popen} if popen is not None else {}
    return ServeState(runs_dir=runs, config=cfg, scenarios_dir=scn_dir, cwd=tmp_path, **kw)


def _write_run(state: ServeState, run_id: str) -> str:
    """A run dir with a manifest under the served runs store — the presence check triage relies on."""
    run = state.base_cwd / state.runs_dir / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"runId": run_id, "ok": False}), encoding="utf-8")
    return run_id


def _await_done(state: ServeState, job_id: str) -> None:
    for _ in range(300):
        if state.jobs[job_id].status == "done":
            return
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_start_triage_requires_config(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", config=None)
    payload, status = ops.start_triage(
        state, {"runId": "r", "target": "demo", "scenario": "smoke.yaml"}
    )
    assert status == 400 and "config" in payload["error"]


def test_start_triage_rejects_unsafe_run_id(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.start_triage(
        state, {"runId": "../secrets", "target": "demo", "scenario": "smoke.yaml"}
    )
    assert status == 400 and "runId" in payload["error"]


def test_start_triage_requires_run_id(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.start_triage(state, {"target": "demo", "scenario": "smoke.yaml"})
    assert status == 400 and "runId" in payload["error"]


def test_start_triage_requires_target_and_scenario(tmp_path: Path) -> None:
    state = _state(tmp_path)
    p1, s1 = ops.start_triage(state, {"runId": "r", "scenario": "smoke.yaml"})
    assert s1 == 400 and "target" in p1["error"]
    p2, s2 = ops.start_triage(state, {"runId": "r", "target": "demo"})
    assert s2 == 400 and "scenario" in p2["error"]


def test_start_triage_unknown_scenario_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.start_triage(
        state, {"runId": "r", "target": "demo", "scenario": "ghost.yaml"}
    )
    assert status == 400 and "scenario" in payload["error"]


def test_start_triage_run_not_found(tmp_path: Path) -> None:
    state = _state(tmp_path)  # no run dir written
    payload, status = ops.start_triage(
        state, {"runId": "20260101-000000", "target": "demo", "scenario": "smoke.yaml"}
    )
    assert status == 404 and "run" in payload["error"]


def test_start_triage_ai_requires_a_credential(tmp_path: Path, monkeypatch: Any) -> None:
    import bajutsu.ai as ai

    monkeypatch.setattr(ai, "credential_gap", lambda _ai: "set ANTHROPIC_API_KEY")
    state = _state(tmp_path)
    run_id = _write_run(state, "20260101-000000")
    payload, status = ops.start_triage(
        state, {"runId": run_id, "target": "demo", "scenario": "smoke.yaml", "ai": True}
    )
    assert status == 400 and "credential" in payload["error"]


def test_start_triage_dispatches_a_heuristic_job(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        captured.append(cmd)
        return FakeProc(["triage · alpha\n"])

    state = _state(tmp_path, popen=popen)
    run_id = _write_run(state, "20260101-000000")
    payload, status = ops.start_triage(
        state, {"runId": run_id, "target": "demo", "scenario": "smoke.yaml"}
    )
    assert status == 200 and payload["jobId"]
    _await_done(state, payload["jobId"])
    cmd = captured[0]
    assert cmd[1:5] == ["-m", "bajutsu", "triage", str(state.runs_dir / run_id)]
    assert cmd[cmd.index("--apply") + 1] == str(tmp_path / "scenarios" / "smoke.yaml")
    assert cmd[cmd.index("--json") + 1] == str(state.runs_dir / run_id / "triage.json")
    assert "--ai" not in cmd  # deterministic heuristic is the default


def test_start_triage_ai_string_false_stays_heuristic(tmp_path: Path) -> None:
    # A non-boolean JSON value ("false" as a string) must not opt into AI (it is truthy under bool()).
    captured: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        captured.append(cmd)
        return FakeProc(["triage · alpha\n"])

    state = _state(tmp_path, popen=popen)
    run_id = _write_run(state, "20260101-000000")
    payload, status = ops.start_triage(
        state, {"runId": run_id, "target": "demo", "scenario": "smoke.yaml", "ai": "false"}
    )
    assert status == 200  # not gated on a credential — it stayed heuristic
    _await_done(state, payload["jobId"])
    assert "--ai" not in captured[0]


def test_start_triage_ai_job_opts_in_with_target(tmp_path: Path, monkeypatch: Any) -> None:
    import bajutsu.ai as ai

    monkeypatch.setattr(ai, "credential_gap", lambda _ai: None)  # a credential is available
    captured: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        captured.append(cmd)
        return FakeProc(["triage · alpha\n"])

    state = _state(tmp_path, popen=popen)
    run_id = _write_run(state, "20260101-000000")
    payload, status = ops.start_triage(
        state, {"runId": run_id, "target": "demo", "scenario": "smoke.yaml", "ai": True}
    )
    assert status == 200
    _await_done(state, payload["jobId"])
    cmd = captured[0]
    assert "--ai" in cmd
    # --target carries the target's ai config + redaction rules for the AI path (BE-0047).
    assert cmd[cmd.index("--target") + 1] == "demo"
