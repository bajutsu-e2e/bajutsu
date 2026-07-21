"""Tests for `serve`'s side of the human-in-the-loop handoff (BE-0179): the SSE classification,
the stdin response channel, the awaiting-human job state, and the record argv flag."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

from _shared import FakeProc, fake_popen, project

from bajutsu import serve as srv
from bajutsu.handoff import (
    REQUEST_LINE_PREFIX,
    HandoffRequest,
    request_to_json,
    response_from_json,
)
from bajutsu.serve import jobs as srv_jobs
from bajutsu.serve.commands import record_command
from bajutsu.serve.operations import respond_human
from bajutsu.serve.operations.sse import _classify, format_sse


class _RaisingStdin:
    """A stdin whose write fails — the record process died between request and response."""

    def write(self, _s: str) -> int:
        raise ValueError("I/O operation on closed file")

    def flush(self) -> None:
        pass


class _ProcWithStdin:
    """A fake subprocess exposing a captured stdin, for the resume/response channel."""

    def __init__(self, raises: bool = False) -> None:
        self.stdin: object = _RaisingStdin() if raises else io.StringIO()


def test_classify_splits_handoff_requests_from_logs() -> None:
    payload = request_to_json(HandoffRequest(reason="enter OTP"))
    assert _classify(REQUEST_LINE_PREFIX + payload) == ("human-request", payload)
    assert _classify("[3] observing 5 elements") == ("log", "[3] observing 5 elements")


def test_sse_frame_for_a_handoff_request_is_a_human_request_event() -> None:
    # The wire frame the browser binds to: `event: human-request` with the prefix stripped, exactly
    # as `_job_sse_frames` emits it via format_sse(*_classify(line)).
    payload = request_to_json(HandoffRequest(reason="enter OTP", screen="1 el"))
    frame = format_sse(*_classify(REQUEST_LINE_PREFIX + payload))
    assert frame.startswith("event: human-request\n")
    assert REQUEST_LINE_PREFIX not in frame
    assert f"data: {payload}\n" in frame


def test_record_command_drives_handoff_over_the_stream() -> None:
    cmd = record_command("out.yaml", "demo", "log in")
    assert "--handoff" in cmd and cmd[cmd.index("--handoff") + 1] == "stream"


def test_send_response_writes_to_stdin_and_clears_awaiting_human() -> None:
    job = srv.Job(cmd=["x"], awaiting_human=True, proc=_ProcWithStdin())
    assert srv_jobs.send_response(job, '{"acted": true}') is True
    assert job.awaiting_human is False
    assert job.proc.stdin.getvalue() == '{"acted": true}\n'


def test_send_response_is_false_when_there_is_no_live_stdin() -> None:
    job = srv.Job(cmd=["x"], awaiting_human=True, proc=None)
    assert srv_jobs.send_response(job, "{}") is False
    assert job.awaiting_human is False


def test_send_response_is_false_when_the_stdin_write_fails() -> None:
    # The process died between request and response: the write raises, the resume reports it didn't
    # land, and the awaiting-human flag is still cleared (no lingering paused state).
    job = srv.Job(cmd=["x"], awaiting_human=True, proc=_ProcWithStdin(raises=True))
    assert srv_jobs.send_response(job, '{"acted": true}') is False
    assert job.awaiting_human is False


def test_awaiting_human_surfaces_in_the_job_view() -> None:
    # The camelCase key the UI polls to render the paused state.
    assert srv.Job(cmd=["x"], awaiting_human=True).view()["awaitingHuman"] is True
    assert srv.Job(cmd=["x"]).view()["awaitingHuman"] is False


def test_respond_human_delivers_the_response_to_the_job(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    body, code = respond_human(state, job.id, {"values": ["999111"]})
    assert code == 200 and body["resumed"] is True
    assert response_from_json(job.proc.stdin.getvalue()).values == ["999111"]


def test_respond_human_relays_an_acted_response(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    _body, code = respond_human(state, job.id, {"acted": True})
    assert code == 200
    assert response_from_json(job.proc.stdin.getvalue()).kind == "acted"


def test_respond_human_reports_a_resume_that_did_not_land(tmp_path: Path) -> None:
    # The job finished (no live stdin): the resume is reported as not landed, not a false success.
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    job = state.register(srv.Job(cmd=["x"], proc=None))
    body, code = respond_human(state, job.id, {"values": ["x"]})
    assert code == 200 and body["resumed"] is False


def test_respond_human_404s_for_an_unknown_job(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    _body, code = respond_human(state, "nope", {"cancelled": True})
    assert code == 404


def test_respond_human_refuses_a_device_takeover_on_a_hosted_serve(tmp_path: Path) -> None:
    # BE-0185 box 3: a hosted (remote) serve's author is not at the device, so a device-operation
    # takeover (`acted`, no value) cannot be honored — it is refused with a fallback message rather
    # than pretending the author can operate a device they cannot see. The paused record is NOT
    # resumed, and nothing is written to its stdin.
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, hosted=True
    )
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    body, code = respond_human(state, job.id, {"acted": True})
    assert code == 409
    assert body["resumed"] is False
    assert "error" in body  # the fallback message the browser surfaces to the author
    assert job.proc.stdin.getvalue() == ""  # the takeover never reached the record process


def test_respond_human_honors_cancel_over_acted_on_a_hosted_serve(tmp_path: Path) -> None:
    # A body carrying both cancelled and acted is honored as a cancel (HandoffResponse.kind's
    # cancel > value > acted precedence), never refused as a device takeover — otherwise the paused
    # job would be left stuck (409, no stdin write, never resumes to exit).
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, hosted=True
    )
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    body, code = respond_human(state, job.id, {"cancelled": True, "acted": True})
    assert code == 200 and body["resumed"] is True  # cancel wins — not refused
    assert response_from_json(job.proc.stdin.getvalue()).kind == "cancel"


def test_respond_human_does_not_refuse_a_bare_resume_on_a_hosted_serve(tmp_path: Path) -> None:
    # A bare/empty response ({} — nothing acted, no value, no cancel) resolves to kind == "acted"
    # only as HandoffResponse.kind's fallback, not because a device takeover was attempted. It must
    # pass through as a plain re-observe, never be refused as a device takeover (which would strand a
    # paused job on a malformed or empty POST to this public endpoint).
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, hosted=True
    )
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    body, code = respond_human(state, job.id, {})
    assert code == 200 and body["resumed"] is True  # not refused — a bare resume still lands


def test_respond_human_allows_a_value_handoff_on_a_hosted_serve(tmp_path: Path) -> None:
    # A value handoff completes entirely in the browser, so it still works on a hosted serve — only a
    # device-operation takeover needs the device within the author's reach.
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, hosted=True
    )
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    body, code = respond_human(state, job.id, {"values": ["999111"]})
    assert code == 200 and body["resumed"] is True
    assert response_from_json(job.proc.stdin.getvalue()).values == ["999111"]


def test_respond_human_allows_a_cancel_on_a_hosted_serve(tmp_path: Path) -> None:
    # Cancelling a paused record always works — the author can end the recording even on a hosted
    # serve; only the device-operation takeover is refused.
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, hosted=True
    )
    job = state.register(srv.Job(cmd=["x"], proc=_ProcWithStdin()))

    body, code = respond_human(state, job.id, {"cancelled": True})
    assert code == 200 and body["resumed"] is True


def test_run_job_pipes_stdin_only_for_handoff_capable_jobs(tmp_path: Path) -> None:
    # Only a handoff-capable command (record --handoff stream) gets a stdin PIPE (the response
    # channel); every other job gets DEVNULL, so a subprocess reading stdin sees EOF, not a hang.
    scn_dir, cfg, runs = project(tmp_path)
    seen: dict[str, object] = {}

    def spy_popen(_cmd: list[str], **kw: Any) -> FakeProc:
        seen["stdin"] = kw.get("stdin")
        return FakeProc(["done\n"])

    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, popen=spy_popen
    )
    srv.run_job(state, state.register(srv.Job(cmd=["bajutsu", "record", "--handoff", "stream"])))
    assert seen["stdin"] is subprocess.PIPE

    srv.run_job(state, state.register(srv.Job(cmd=["bajutsu", "run", "--scenario", "s.yaml"])))
    assert seen["stdin"] is subprocess.DEVNULL


def test_run_job_handles_the_request_line_and_clears_awaiting_human_on_completion(
    tmp_path: Path,
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    request = REQUEST_LINE_PREFIX + request_to_json(HandoffRequest(reason="solve the CAPTCHA"))
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        popen=fake_popen([request + "\n", "resumed, tapping next\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))
    srv.run_job(state, job)

    # The request line was recognized (kept out of the transcript, unlike a normal narration line)…
    assert request not in job.view()["lines"]  # the serialized payload isn't dumped into the log
    assert "resumed, tapping next" in job.view()["lines"]
    # …and awaiting-human is cleared once the job ends, so a job that paused but got no response
    # doesn't report an un-resumable paused state forever (BE-0179).
    assert job.view()["awaitingHuman"] is False
