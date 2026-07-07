"""Human-in-the-loop handoff contract (Tier 1, BE-0179).

The `record` loop can pause and hand control to a human — to supply a value the AI cannot
know (a one-time password), or to perform an operation the AI cannot (a CAPTCHA, a biometric
prompt) — then resume by re-observing the live screen. This module defines the
transport-neutral request/response contract so the terminal (`record` reading stdin) and the
Web UI (`serve` streaming the request over server-sent events and taking the response back
over the spawned-`record` process boundary) implement the same protocol.

The substrate owns the *mechanism* and the *boundary*: every handoff must resolve to a
re-runnable artifact, so a recording made with human help still replays with no human on the
deterministic `run` path. It deliberately does not pick the artifact shape (a value
placeholder versus an explicit manual step) — that is the child items' decision. What it
guarantees is that the human stays at authoring time and never lands on the `run` / CI gate.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# Marks a serialized handoff request on the `record` process's stdout so `serve` can lift it
# out of the otherwise-textual narration stream and turn it into a structured `human-request`
# event rather than a `log` line. The control characters keep it from colliding with narration.
REQUEST_LINE_PREFIX = "\x1eBAJUTSU-HANDOFF-REQUEST\x1e"

# How long a handoff waits on a human before it resolves to a cancel — bounded so no surface
# (a terminal prompt, a `serve` worker) ever hangs indefinitely on someone who walked away.
DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass
class HandoffRequest:
    """What a paused `record` asks a human: why it stopped, and the screen it stopped on.

    `target` is a compact description of the selector the paused action was aiming at (empty
    for a screen-level request); `screenshot` is the current screen as PNG bytes, shown in the
    Web UI pane.
    """

    reason: str
    screen: str = ""
    target: str = ""
    screenshot: bytes | None = None


@dataclass
class HandoffResponse:
    """A human's answer to a handoff: values supplied, an action performed, or a cancel.

    `values` are literal strings the human supplied (e.g. a one-time password); `acted` says
    the human operated the device and the loop should re-observe from the new screen;
    `cancelled` ends the record cleanly. The substrate carries these and resumes by
    re-observation — a child item decides what, if anything, to record from them.
    """

    values: list[str] = field(default_factory=list)
    acted: bool = False
    cancelled: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffResponse:
        """Build a response from a decoded payload, coercing types — the one authority for turning
        an untrusted map (a stdin line, a `respond-human` POST body) into a response.

        A bare `values` string (or any non-list) is wrapped as a single value, never iterated
        character by character — so `{"values": "123456"}` supplies one code, not six.
        """
        raw = data.get("values")
        if raw is None:
            values: list[str] = []
        elif isinstance(raw, (list, tuple)):
            values = [str(v) for v in raw]
        else:
            values = [str(raw)]
        return cls(
            values=values,
            acted=bool(data.get("acted", False)),
            cancelled=bool(data.get("cancelled", False)),
        )

    @property
    def kind(self) -> Literal["cancel", "value", "acted"]:
        """The single outcome, resolving precedence so consumers never hand-order the checks: a
        cancel wins over everything, then a value response, then a bare acted flag."""
        if self.cancelled:
            return "cancel"
        return "value" if self.values else "acted"


class Handoff(Protocol):
    """A bounded, cancelable channel that asks a human and returns their response.

    Implementations block on a human but never unbounded: a responder who never answers
    resolves to a cancelled response, never a hang. `record` calls this when a turn's outcome
    is "needs human".
    """

    def request(self, request: HandoffRequest) -> HandoffResponse:
        """Present *request* to the human and return their response (cancelled on timeout)."""


class HumanHandoffUnavailable(RuntimeError):
    """Raised when a `record` turn needs a human but no responder is available.

    The non-interactive / CI case: the tooling stays deterministic by failing cleanly and
    labeled rather than hanging or letting the AI guess.
    """


def request_to_json(request: HandoffRequest) -> str:
    """Serialize a handoff request for the stdout stream / server-sent event (screenshot base64)."""
    shot = base64.b64encode(request.screenshot).decode("ascii") if request.screenshot else None
    return json.dumps(
        {
            "reason": request.reason,
            "screen": request.screen,
            "target": request.target,
            "screenshot": shot,
        }
    )


def request_from_json(payload: str) -> HandoffRequest:
    """Parse a handoff request from its serialized form."""
    data = json.loads(payload)
    shot = data.get("screenshot")
    return HandoffRequest(
        reason=str(data.get("reason", "")),
        screen=str(data.get("screen", "")),
        target=str(data.get("target", "")),
        screenshot=base64.b64decode(shot) if shot else None,
    )


def response_to_json(response: HandoffResponse) -> str:
    """Serialize a handoff response for the response channel (stdin / the response endpoint)."""
    return json.dumps(
        {"values": response.values, "acted": response.acted, "cancelled": response.cancelled}
    )


def response_from_json(payload: str) -> HandoffResponse:
    """Parse a handoff response from its serialized form.

    Raises `ValueError` on anything that is not a JSON object — malformed text, or valid JSON that
    is a list / string / number / null. The `StreamHandoff` responder maps that to a cancel, so a
    bad response never crashes `record` with an `AttributeError`.
    """
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("handoff response must be a JSON object")
    return HandoffResponse.from_dict(data)
