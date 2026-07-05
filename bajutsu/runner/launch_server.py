"""Bring up an app's target server (the host behind `baseUrl`) for the duration of a run.

The web (Playwright) backend navigates to `baseUrl`, but nothing brings that server up — so a
dogfood run of the WebUI, or `demos/web`, needs the target serving first. This is the web analog of
the iOS `build` hook (which produces the `.app` before a run), but for a long-running process:

    probe readyUrl → reuse if already serving, else run `cmd` → wait on the probe → (run) → teardown

It stays inside the prime directives: the lifecycle is deterministic infrastructure (a shell command
plus an HTTP readiness poll, no LLM), and readiness is a *condition wait* — poll until the server
answers — never a blind fixed sleep. If `readyUrl` already answers, the server was started elsewhere
(a Makefile, CI, a manual launch); we reuse it and never tear it down.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from bajutsu.config import Effective, web_base_url

_POLL_INTERVAL = 0.25  # seconds between readiness probes (the grain of the condition wait)


def _default_log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _probe(url: str, timeout: float = 2.0) -> bool:
    """True if `url` answers with an HTTP status < 400 (the server is up and serving)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # config-driven URL (intentional)
            return int(r.status) < 400
    except urllib.error.HTTPError as e:  # a live server answering 4xx/5xx — up, but not "ready"
        return e.code < 400
    except (urllib.error.URLError, OSError, ValueError):  # not listening yet / bad url
        return False


def _decision(decision: str) -> dict[str, str | None]:
    """A launchServer policy-decision record (BE-0090), matching the sandbox path's shape."""
    return {"decision": decision, "field": "launchServer", "source": None, "image": None}


def start_launch_server(
    eff: Effective,
    *,
    upload_exec: str | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[Callable[[], None], dict[str, str | None] | None]:
    """Bring up `eff.launch_server` (the web target's host) if declared, returning `(teardown, decision)`.

    Idempotent: a no-op stop is returned when no server is declared, or when `readyUrl` already
    answers (an externally-started server is reused and left running). Otherwise the command is
    started and probed until `readyUrl` responds within `readyTimeout`.

    `upload_exec` governs an *uploaded* bundle's command (BE-0090): `None` is an ungoverned,
    operator-trusted local/Git run (the bare-host path below); `sandbox` runs the command in a
    container; `reuse` / `deny` never run the command and only accept an externally-answering
    `readyUrl`. `serve` sets the flag only for an upload-sourced config, so a local run is unaffected.

    Args:
        eff: The resolved target config; `launch_server` and `base_url` decide what to start and
            probe.
        upload_exec: The upload-execution policy (`deny` / `reuse` / `sandbox`), or None when the run
            is not upload-governed.
        log: Receives one-line status messages. None uses the default logger.

    Returns:
        A teardown callable (a no-op when nothing was started / an existing server is reused), and a
        policy-decision record — None for an ungoverned run, else `denied` / `reused` / `sandboxed`.

    Raises:
        RuntimeError: `launchServer` declares no `readyUrl` and the target has no `baseUrl`, or the
            command exited / the server wasn't ready within `readyTimeout`. `SandboxError` (a
            subclass) covers a forbidden or misconfigured upload-governed command. The caller exits 2
            with the message.
    """
    # Deferred import: sandbox imports this module's probe/log helpers, so importing it at module
    # scope would be circular.
    from bajutsu.runner.sandbox import SandboxError, start_sandboxed_server

    ls = eff.launch_server
    if ls is None:
        return (lambda: None), None
    say = log or _default_log
    if upload_exec == "sandbox":
        return start_sandboxed_server(eff, log=log)
    ready_url = ls.ready_url or web_base_url(eff)
    if not ready_url:
        raise RuntimeError("launchServer needs readyUrl (or set the target's baseUrl to probe)")

    if upload_exec in ("deny", "reuse"):
        # Neither mode runs the uploaded command: only an externally-answering readyUrl is accepted,
        # else the run fails loud (DESIGN §2 — no silent fallback to running the command).
        if _probe(ready_url):
            verdict = "denied" if upload_exec == "deny" else "reused"
            say(f"launchServer({upload_exec}): {ready_url} answered externally — not running cmd")
            return (lambda: None), _decision(verdict)
        raise SandboxError(
            f"launchServer({upload_exec}): nothing answering {ready_url} and the uploaded cmd is "
            f"not run under '{upload_exec}' — cmd: {ls.cmd!r}"
        )
    if upload_exec is not None:
        raise SandboxError(f"unknown --upload-exec mode: {upload_exec!r}")

    if _probe(ready_url):
        say(f"launchServer: {ready_url} already serving — reusing it (not started by bajutsu)")
        return (lambda: None), None

    say(f"launchServer: starting target server — {ls.cmd!r}")
    proc = subprocess.Popen(
        shlex.split(ls.cmd),
        cwd=ls.cwd,
        env={**os.environ, **ls.env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # own process group, so teardown reaches the server's children too
    )
    deadline = time.monotonic() + ls.ready_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"launchServer: command exited (code {proc.returncode}) before {ready_url} "
                f"was ready — cmd: {ls.cmd!r}"
            )
        if _probe(ready_url):
            say(f"launchServer: {ready_url} ready")
            return (lambda: _terminate(proc, say)), None
        time.sleep(_POLL_INTERVAL)
    _terminate(proc, say)
    raise RuntimeError(
        f"launchServer: {ready_url} not ready after {ls.ready_timeout}s — cmd: {ls.cmd!r}"
    )


def _terminate(proc: subprocess.Popen[bytes], say: Callable[[str], None]) -> None:
    """SIGTERM the server's process group (then SIGKILL after a grace). Idempotent."""
    if proc.poll() is not None:
        return
    say("launchServer: stopping target server")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        # Reap the killed child so it doesn't linger as a zombie until the parent exits.
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
