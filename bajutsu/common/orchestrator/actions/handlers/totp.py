"""The `totp` and `otp` steps: RFC 6238 one-time passwords into vars.* (BE-0046)."""

from __future__ import annotations

import time

from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.common.scenario import Step
from bajutsu.common.totp import totp as _totp


def generator_key(name: str) -> str:
    """The bindings key a `totp` step's `name` registers its secret under, for `otp` to read.

    The `}` keeps the key out of reach of every `${...}` token (whose grammar cannot contain one), so
    a scenario can never splice a registered secret into a typed field or an assertion.
    """
    return f"totp}}{name}"


def _code(kind: str, secret: str) -> str:
    # The current code for the wall-clock time; the run that consumes it is happening now, so the
    # OTP must match the same window the app expects. The math is the pure, gate-tested _totp.
    try:
        return _totp(secret, now=time.time())
    except ValueError as e:
        # An invalid base32 secret fails the step cleanly (a SelectorError the run loop catches)
        # rather than bubbling a decode error that aborts the run; `e` never echoes the secret.
        raise base.SelectorError(f"{kind}: {e}") from e


@_handler("totp")
def _do_totp(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.totp is not None
    if bindings is None:
        return  # no var scope to write into (e.g. a bare condition eval) — nothing to do
    # Computed even for a register-only step, so a bad secret fails here, not at a later `otp`.
    code = _code("totp", step.totp.secret)
    if step.totp.name is not None:
        key = generator_key(step.totp.name)
        if bindings.get(key, step.totp.secret) != step.totp.secret:
            # Two authenticators given one name by mistake would otherwise swap codes silently; the
            # same secret again (a `totp` in a `forEach` body) re-registers harmlessly.
            raise base.SelectorError(
                f"totp: generator {step.totp.name!r} is already registered with a different secret"
            )
        bindings[key] = step.totp.secret
    if step.totp.into is not None:
        bindings[f"vars.{step.totp.into.var}"] = code


@_handler("otp")
def _do_otp(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.otp is not None
    if bindings is None:
        return  # no var scope: no generator could have been registered, and nowhere to write
    secret = bindings.get(generator_key(step.otp.from_))
    if secret is None:
        raise base.SelectorError(
            f"otp: no totp generator named {step.otp.from_!r} has run yet in this scenario "
            "(a `totp` step with `name` must run first)"
        )
    bindings[f"vars.{step.otp.into.var}"] = _code("otp", secret)
