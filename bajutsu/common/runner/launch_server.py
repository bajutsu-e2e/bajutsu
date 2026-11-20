"""Bring up an app's target server (the host behind `baseUrl`) for the duration of a run.

The web (Playwright) backend navigates to `baseUrl`, but nothing brings that server up — so a
dogfood run of the WebUI, or `demos/web`, needs the target serving first. This is the web analog of
the iOS `build` hook (which produces the `.app` before a run), but for a long-running process:

    probe readyUrl → reuse if already serving, else run `cmd` → wait on the probe → (run) → teardown

It stays inside the prime directives: the lifecycle is deterministic infrastructure (a shell command
plus an HTTP readiness poll, no LLM), and readiness is a *condition wait* — poll until the server
answers — never a blind fixed sleep. If `readyUrl` already answers, the server was started elsewhere
(a Makefile, CI, a manual launch); we reuse it and never tear it down. Whether the command may
run at all is the worker's command policy (BE-0492), which the run's gate applies first.
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
from pathlib import Path

from bajutsu.common.capability import command_gate
from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import SERVER_FIELD
from bajutsu.common.config import Effective, web_base_url

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
    eff: Effective,
    *,
    gate: CommandGate | None = None,
    log: Callable[[str], None] | None = None,
) -> Callable[[], None]:
    """Bring up `eff.launch_server` (the web target's host) if declared, returning its teardown.

    Idempotent: a no-op stop is returned when no server is declared, or when `readyUrl` already
    answers (an externally-started server is reused and left running). Otherwise the command is
    started and probed until `readyUrl` responds within `readyTimeout`.

    *gate* applies the worker's command policy to the command first (BE-0492): `allow` runs it on
    this host, `sandbox` runs it in a container, and `named` / `deny` refuse it. A server that already
    answers is reused under every mode, since the run then executes no command at all.

    Args:
        eff: The resolved target config; `launch_server` and `base_url` decide what to start and
            probe.
        gate: The run's command gate, which decides and records the command; the installed gate
            (`command_gate.current`) when None.
        log: Receives one-line status messages. None uses the default logger.

    Returns:
        A teardown callable (a no-op when nothing was started / an existing server is reused).

    Raises:
        CommandRefused: The policy refuses the command and nothing already answers `readyUrl`.
        RuntimeError: `launchServer` declares no `readyUrl` and the target has no `baseUrl`, or the
            command exited / the server wasn't ready within `readyTimeout`. `SandboxError` (a
            subclass) covers a misconfigured sandboxed command. The caller exits 2 with the message.
    """
    # Deferred import: sandbox imports this module's probe/log helpers, so importing it at module
    # scope would be circular.
    from bajutsu.common.runner.sandbox import start_sandboxed_server

    ls = eff.launch_server
    if ls is None:
        return lambda: None
    say = log or _default_log
    gate = gate if gate is not None else command_gate.current()
    ready_url = ls.ready_url or web_base_url(eff)
    if not ready_url:
        raise RuntimeError("launchServer needs readyUrl (or set the target's subject.url to probe)")

    if _probe(ready_url):
        say(f"launchServer: {ready_url} already serving — reusing it (not started by bajutsu)")
        return lambda: None

    if gate.check("command", SERVER_FIELD, ls.cmd) == "sandbox":
        teardown = start_sandboxed_server(eff, log=log)
        gate.record(SERVER_FIELD, ls.cmd, sandboxed=True)
        return teardown

    say(f"launchServer: starting target server — {ls.cmd!r}")
    proc = subprocess.Popen(
        shlex.split(ls.cmd),
        cwd=ls.cwd,
        env={**os.environ, **ls.env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # own process group, so teardown reaches the server's children too
    )
    cwd = Path(ls.cwd) if ls.cwd else None
    deadline = time.monotonic() + ls.ready_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            gate.record(SERVER_FIELD, ls.cmd, exit_code=proc.returncode, cwd=cwd)
            raise RuntimeError(
                f"launchServer: command exited (code {proc.returncode}) before {ready_url} "
                f"was ready — cmd: {ls.cmd!r}"
            )
        if _probe(ready_url):
            say(f"launchServer: {ready_url} ready")
            gate.record(SERVER_FIELD, ls.cmd, cwd=cwd)
            return lambda: _terminate(proc, say)
        time.sleep(_POLL_INTERVAL)
    _terminate(proc, say)
    gate.record(SERVER_FIELD, ls.cmd, exit_code=proc.returncode, cwd=cwd)
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
