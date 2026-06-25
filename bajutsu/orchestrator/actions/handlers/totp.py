"""The `totp` step: generate an RFC 6238 one-time password into vars.* (BE-0046)."""

from __future__ import annotations

import time

from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.scenario import Step
from bajutsu.totp import totp as _totp


@_handler("totp")
def _do_totp(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.totp is not None
    if bindings is None:
        return  # no var scope to write into (e.g. a bare condition eval) — nothing to do
    # The current code for the wall-clock time; the run that consumes it is happening now, so the
    # OTP must match the same window the app expects. The math is the pure, gate-tested _totp.
    bindings[f"vars.{step.totp.into.var}"] = _totp(step.totp.secret, now=time.time())
