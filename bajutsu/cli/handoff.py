"""Handoff responders for `record` (BE-0179).

Two implementations of the `Handoff` contract, both bounded and cancelable:

- `PromptHandoff` — a terminal author answers at an interactive stdin prompt.
- `StreamHandoff` — `serve` drives the record: the request is emitted as a sentinel line on
  stdout (which `serve` lifts into a `human-request` server-sent event) and the response is read
  back from stdin (which `serve` feeds over the spawned-`record` process boundary).

Both read stdin through a bounded `select`, so a responder who never answers resolves to a
cancelled response rather than an unbounded hang. When no responder is available at all
(non-interactive, no `serve`), `record` gets no handoff and fails cleanly instead (the CLI maps
that to a labeled non-zero exit) — the human never lands on the deterministic `run` path.
"""

from __future__ import annotations

import select
import sys
from collections.abc import Callable

from bajutsu.handoff import (
    DEFAULT_TIMEOUT_SECONDS,
    REQUEST_LINE_PREFIX,
    Handoff,
    HandoffRequest,
    HandoffResponse,
    request_to_json,
    response_from_json,
)

Say = Callable[[str], None]


def _read_line_bounded(timeout: float) -> str | None:
    """Read one line from stdin, waiting at most *timeout* seconds; None on timeout or EOF."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return None  # stdin has no waitable fd (closed / not a real stream): treat as no responder
    if not ready:
        return None
    line = sys.stdin.readline()
    return line if line else None  # readline == "" means EOF (the write end closed)


class PromptHandoff:
    """An interactive terminal responder: shows the request and reads the human's answer on stdin.

    The human types a value to supply it, `done` (or an empty line) after operating the device
    themselves, or `cancel` to stop the record. A silent responder times out to a cancel.
    """

    def __init__(self, say: Say, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._say = say
        self._timeout = timeout

    def request(self, request: HandoffRequest) -> HandoffResponse:
        self._say(f"✋ handoff needed: {request.reason}")
        if request.target:
            self._say(f"   target: {request.target}")
        if request.screen:
            self._say(f"   screen: {request.screen}")
        self._say(
            "   type a value to supply it, `done` after you operate the device, or `cancel` "
            f"(waiting up to {self._timeout:g}s) …"
        )
        line = _read_line_bounded(self._timeout)
        if line is None:
            self._say("   (no response — cancelling the handoff)")
            return HandoffResponse(cancelled=True)
        answer = line.strip()
        if answer.lower() == "cancel":
            return HandoffResponse(cancelled=True)
        if answer == "" or answer.lower() == "done":
            return HandoffResponse(acted=True)
        return HandoffResponse(values=[answer])


class StreamHandoff:
    """A `serve`-driven responder: emits the request on stdout and reads the response from stdin.

    The request travels out as a `REQUEST_LINE_PREFIX`-tagged JSON line (`serve` turns it into a
    `human-request` event); the response arrives as a JSON line `serve` writes to this process's
    stdin. Bounded by *timeout* so a `serve` worker never hangs on an absent human.
    """

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def request(self, request: HandoffRequest) -> HandoffResponse:
        # A single flushed line so `serve`'s line-buffered reader sees it whole and at once.
        sys.stdout.write(f"{REQUEST_LINE_PREFIX}{request_to_json(request)}\n")
        sys.stdout.flush()
        line = _read_line_bounded(self._timeout)
        if line is None:
            # Narrate the cancel on the stream (not silently), so the record log shows *why* it ended.
            self._say(f"✋ no handoff response within {self._timeout:g}s — cancelling")
            return HandoffResponse(cancelled=True)
        try:
            return response_from_json(line)
        except ValueError:
            # A malformed response is not a value to guess — but serve builds it, so this signals a
            # transport bug; surface it rather than letting it masquerade as a human cancel.
            self._say("✋ malformed handoff response — cancelling")
            return HandoffResponse(cancelled=True)

    def _say(self, message: str) -> None:
        """Emit a line on stdout — the record narration stream `serve` relays to the browser."""
        sys.stdout.write(message + "\n")
        sys.stdout.flush()


def make_handoff(mode: str, *, say: Say) -> Handoff | None:
    """The handoff responder for a `record` invocation, or None when there is no responder.

    `auto` (the default) is interactive when stdin is a TTY, else None — so CI, with no human,
    gets the clean labeled failure. `prompt` / `stream` force the terminal / `serve` responder;
    `off` forces no responder. An unknown mode raises rather than silently degrading to `auto`,
    so a typo (`--handoff promt`) fails loudly instead of quietly changing whether `record` can pause.
    """
    if mode == "off":
        return None
    if mode == "prompt":
        return PromptHandoff(say)
    if mode == "stream":
        return StreamHandoff()
    if mode == "auto":
        return PromptHandoff(say) if sys.stdin.isatty() else None
    raise ValueError(f"unknown handoff mode: {mode!r} (expected auto | prompt | stream | off)")
