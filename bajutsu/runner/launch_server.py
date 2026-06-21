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

from bajutsu.config import Effective

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


def start_launch_server(
    eff: Effective, *, log: Callable[[str], None] | None = None
) -> Callable[[], None]:
    """Bring up `eff.launch_server` if declared, returning a teardown callable (idempotent).

    No-op (returns a no-op stop) when no server is declared, or when `readyUrl` already answers
    (reuse an externally-started server, and leave it running). Raises `RuntimeError` if the command
    exits or the server isn't ready within `readyTimeout` — the caller exits 2 with the message.
    """
    ls = eff.launch_server
    if ls is None:
        return lambda: None
    say = log or _default_log
    ready_url = ls.ready_url or eff.base_url
    if not ready_url:
        raise RuntimeError("launchServer needs readyUrl (or set the app's baseUrl to probe)")

    if _probe(ready_url):
        say(f"launchServer: {ready_url} already serving — reusing it (not started by bajutsu)")
        return lambda: None

    say(f"launchServer: starting target server — {ls.cmd}")
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
                f"was ready — cmd: {ls.cmd}"
            )
        if _probe(ready_url):
            say(f"launchServer: {ready_url} ready")
            return lambda: _terminate(proc, say)
        time.sleep(_POLL_INTERVAL)
    _terminate(proc, say)
    raise RuntimeError(
        f"launchServer: {ready_url} not ready after {ls.ready_timeout}s — cmd: {ls.cmd}"
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
