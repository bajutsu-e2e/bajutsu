"""Tests for the `SIGTERM`-to-event bridge a cancelled run answers (BE-0370)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bajutsu.cancellation import (
    DEFAULT_GRACE_SECONDS,
    GRACE_ENV,
    HANDLER_MARGIN_SECONDS,
    grace_seconds,
    graceful_sigterm,
    handler_deadline,
    not_cancelled,
)


def test_sigterm_requests_cancellation_instead_of_ending_the_process() -> None:
    # The whole premise: the default disposition would have killed this process here, before any
    # manifest was written. Reaching the assertion at all is the behavior under test.
    with graceful_sigterm() as cancelled:
        assert not cancelled()
        signal.raise_signal(signal.SIGTERM)
        assert cancelled()


def test_a_second_sigterm_is_absorbed_rather_than_killing_the_run() -> None:
    # An operator who clicks Cancel twice must not lose the manifest: `cancel_job` re-signals on every
    # request, so the handler has to stay installed and idempotent.
    with graceful_sigterm() as cancelled:
        signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGTERM)
        assert cancelled()


def test_the_handler_and_its_timer_are_torn_down_on_exit() -> None:
    before = signal.getsignal(signal.SIGTERM)
    with graceful_sigterm():
        signal.raise_signal(signal.SIGTERM)
        armed, _ = signal.getitimer(signal.ITIMER_REAL)
        assert armed > 0  # the internal deadline is running while the shutdown is in flight
    assert signal.getsignal(signal.SIGTERM) is before
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_the_internal_deadline_sits_strictly_beyond_the_grace_window() -> None:
    # The two deadlines are not picked independently: a shorter internal one would kill the run before
    # `_assemble_report` wrote a manifest, reproducing the silent gap for every ordinary cancel.
    assert handler_deadline(30.0) > 30.0
    assert handler_deadline(DEFAULT_GRACE_SECONDS) == DEFAULT_GRACE_SECONDS + HANDLER_MARGIN_SECONDS


def test_the_grace_window_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRACE_ENV, "12.5")
    assert grace_seconds() == 12.5


@pytest.mark.parametrize("raw", ["", "soon", "0", "-5"])
def test_an_unusable_grace_window_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # A run that received no usable window still has to bound itself, so every unusable value lands on
    # the default rather than on zero (which would kill the run at its first boundary).
    monkeypatch.setenv(GRACE_ENV, raw)
    assert grace_seconds() == DEFAULT_GRACE_SECONDS


def test_off_the_main_thread_the_handler_degrades_loudly(caplog: pytest.LogCaptureFixture) -> None:
    # `signal.signal` is main-thread only. The fallback is today's behavior (an immediate kill), which
    # must be disclosed rather than silently assumed away.
    seen: list[object] = []

    def install() -> None:
        with (
            caplog.at_level(logging.WARNING, logger="bajutsu.cancellation"),
            graceful_sigterm() as c,
        ):
            seen.append(c)

    thread = threading.Thread(target=install)
    thread.start()
    thread.join()
    assert seen == [not_cancelled]
    assert "cooperative cancellation is unavailable" in caplog.text


def test_a_second_sigterm_does_not_re_arm_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # The bound has to be one grace window, not one per Cancel click: re-arming on every signal would
    # let an operator tapping Cancel extend the shutdown indefinitely.
    armed: list[float] = []
    real = signal.setitimer
    monkeypatch.setattr(
        signal, "setitimer", lambda which, seconds, *a: armed.append(seconds) or real(which, 0.0)
    )
    with graceful_sigterm():
        signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGTERM)
    assert armed == [handler_deadline(grace_seconds()), 0.0]  # armed once, then disarmed on exit


def test_a_shutdown_that_never_finishes_dies_at_the_internal_deadline(tmp_path: Path) -> None:
    # The bound `serve` is not watching: a `docker stop`, a systemd unit stop, or a CI job
    # cancellation has no external escalator, so the handler enforces its own deadline. Driven in a
    # child process because passing the assertion means the process under test is killed.
    child = tmp_path / "wedged_run.py"
    child.write_text(
        "\n".join(
            (
                "import time",
                "from bajutsu import cancellation",
                # A deadline the test can wait out, in place of the shipped 10s margin.
                "cancellation.HANDLER_MARGIN_SECONDS = 0.1",
                "with cancellation.graceful_sigterm() as cancelled:",
                "    print('ready', flush=True)",
                # A shutdown that never reaches a boundary — the wedged runner the deadline exists for.
                "    while True:",
                "        time.sleep(0.05)",
            )
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(child)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "BAJUTSU_CANCEL_GRACE": "0.1"},
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"  # the handler is installed
        proc.send_signal(signal.SIGTERM)
        # Generously bounded against a loaded host — the child's own deadline is 0.2s — while still
        # failing in a readable time when the deadline is never enforced at all.
        assert proc.wait(timeout=20) == -signal.SIGTERM
    finally:
        if proc.poll() is None:  # a handler that never enforced its deadline would still be running
            proc.kill()
            proc.wait()
