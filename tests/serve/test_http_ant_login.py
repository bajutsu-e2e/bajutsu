"""Tests for the ant-CLI SSO sign-in endpoint (/api/ant/login), real ThreadingHTTPServer (BE-0175).

POST starts an `ant auth login` subprocess in serve's environment; GET polls it. The subprocess is
injected through `ServeState.popen` (the same seam the run/build paths use) so no real `ant` binary
is ever spawned. Local serve only — a hosted deployment refuses the sign-in.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from _shared import _get_json, _post, _serve, project

from bajutsu import ai_availability
from bajutsu import anthropic_client as ac
from bajutsu import serve as srv
from bajutsu.serve import operations as ops


class _FakeAnt:
    """A stand-in for the `ant auth login` Popen: `poll()` returns None while running or the exit
    code once done, `terminate()` records the supersede-and-restart tear-down, and `stdout` is the
    merged output the status op tails for an error detail."""

    def __init__(self, code: int | None, out: str = "") -> None:
        self._code = code
        self.stdout = io.StringIO(out)
        self.terminated = False

    def poll(self) -> int | None:
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self._code = -15  # SIGTERM — poll() now reports it exited


def _popen_returning(proc: _FakeAnt):  # type: ignore[no-untyped-def]
    """A fake `popen` that hands back *proc* and records the kwargs of each call."""
    calls: list[dict[str, Any]] = []

    def popen(_cmd: list[str], **kw: Any) -> _FakeAnt:
        calls.append(kw)
        return proc

    popen.calls = calls  # type: ignore[attr-defined]
    return popen


def _popen_sequence(*procs: _FakeAnt):  # type: ignore[no-untyped-def]
    """A fake `popen` that hands back *procs* in order (one per call) and records the count, so a
    supersede test can watch the first process torn down and a distinct second one spawned."""
    seq = iter(procs)
    calls: list[dict[str, Any]] = []

    def popen(_cmd: list[str], **kw: Any) -> _FakeAnt:
        calls.append(kw)
        return next(seq)

    popen.calls = calls  # type: ignore[attr-defined]
    return popen


def _install_ant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report the `ant` binary present regardless of the CI host (the op probes with shutil.which)."""
    monkeypatch.setattr(ops.config.shutil, "which", lambda _exe: "/usr/bin/ant")


def test_ant_login_starts_and_reports_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST spawns the CLI once (202, running); once it exits 0 the status reads ok — the credential
    is written and the provider gate flips to reachable elsewhere."""
    scn_dir, cfg, runs = project(tmp_path)
    _install_ant(monkeypatch)
    popen = _popen_returning(_FakeAnt(code=0))
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    state.popen = popen
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/ant/login", {})
        assert code == 202 and body["started"] is True and body["state"] == "running"
        assert len(popen.calls) == 1  # type: ignore[attr-defined]
        assert _get_json(port, "/api/ant/login") == {"state": "ok"}
    finally:
        server.shutdown()
        server.server_close()


def test_ant_login_supersedes_a_running_signin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click while a previous sign-in is still waiting terminates the stale CLI and spawns a fresh
    one, so an abandoned browser flow can be retried at once instead of wedging until the CLI's
    timeout."""
    scn_dir, cfg, runs = project(tmp_path)
    _install_ant(monkeypatch)
    first_proc, second_proc = _FakeAnt(code=None), _FakeAnt(code=None)  # both start "running"
    popen = _popen_sequence(first_proc, second_proc)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    state.popen = popen
    server, port = _serve(state)
    try:
        first_code, first = _post(port, "/api/ant/login", {})
        second_code, second = _post(port, "/api/ant/login", {})
        assert first_code == 202 and first["started"] is True
        assert second_code == 202 and second["started"] is True  # a fresh sign-in, not a refusal
        assert first_proc.terminated is True  # the stale attempt was torn down
        assert len(popen.calls) == 2  # type: ignore[attr-defined]
        assert state.ant_login_proc is second_proc
    finally:
        server.shutdown()
        server.server_close()


def test_ant_login_refused_when_hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hosted / multi-tenant deployment refuses the sign-in (it writes a machine-global credential
    the whole server would share) — 403, and the CLI is never spawned."""
    scn_dir, cfg, runs = project(tmp_path)
    _install_ant(monkeypatch)
    popen = _popen_returning(_FakeAnt(code=0))
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, hosted=True
    )
    state.popen = popen
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/ant/login", {})
        assert code == 403 and "local serve" in body["error"]
        assert len(popen.calls) == 0  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()


def test_ant_login_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `ant` on PATH → 400 with the same install hint the availability check surfaces."""
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.setattr(ops.config.shutil, "which", lambda _exe: None)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/ant/login", {})
        assert code == 400 and body["error"] == ai_availability.message(ac.ANT_CLI_MISSING)
    finally:
        server.shutdown()
        server.server_close()


def test_ant_login_status_idle_before_any_login(tmp_path: Path) -> None:
    """No sign-in started yet → idle."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    assert ops.ant_login_status(state) == ({"state": "idle"}, 200)


def test_ant_login_status_error_tails_output(tmp_path: Path) -> None:
    """A non-zero exit reports error with the CLI's last output line as the detail (op-level, so the
    fake proc's stdout can carry a canned failure without shelling out)."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    state.ant_login_proc = _FakeAnt(
        code=1, out="Opening your browser...\nauthorization denied: access_denied"
    )
    payload, status = ops.ant_login_status(state)
    assert status == 200
    assert payload["state"] == "error"
    assert payload["detail"] == "authorization denied: access_denied"
