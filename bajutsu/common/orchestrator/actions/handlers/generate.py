"""The `generate` step: compute a random or current-datetime value into vars.* (BE-0377)."""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.scenario import DatetimeValue, Generate, RandomValue, Step

# A system-entropy source rather than the `random` module's shared Mersenne Twister: the point of a
# generated value is that it collides with nothing another run (or another worker in the same run)
# produced, and a seeded PRNG two processes start alike defeats exactly that.
_RNG = secrets.SystemRandom()

_CHARSETS = {
    "alnum": string.ascii_letters + string.digits,
    "alpha": string.ascii_letters,
    "numeric": string.digits,
    "hex": string.digits + "abcdef",
}


def _random_value(spec: RandomValue) -> str:
    """The text one `random` generator kind produces (exactly one is set — §6.2)."""
    if spec.string is not None:
        alphabet = _CHARSETS[spec.string.charset]
        return "".join(_RNG.choice(alphabet) for _ in range(spec.string.length))
    if spec.int_ is not None:
        return str(_RNG.randint(spec.int_.min, spec.int_.max))
    if spec.float_ is not None:
        value = _RNG.uniform(spec.float_.min, spec.float_.max)
        # `precision` fixes the decimal places, trailing zeros included, so a field that wants a
        # money-shaped value gets one; without it the shortest round-trip form is used.
        return (
            f"{value:.{spec.float_.precision}f}"
            if spec.float_.precision is not None
            else str(value)
        )
    assert spec.uuid is not None
    return str(uuid.uuid4())


def _datetime_value(spec: DatetimeValue, now: datetime | None = None) -> str:
    """The text a `datetime` generator produces: the current time, shifted and zoned per `spec`.

    Args:
        spec: The generator's fields, already validated at load time — `timezone` resolves and
            `format` renders, so nothing here can fail mid-run.
        now: The instant to read instead of the clock. Tests pin it; a run leaves it None.
    """
    tz = ZoneInfo(spec.timezone) if spec.timezone is not None else UTC
    moment = (now.astimezone(tz) if now is not None else datetime.now(tz=tz)) + timedelta(
        seconds=spec.offset_seconds or 0,
        minutes=spec.offset_minutes or 0,
        hours=spec.offset_hours or 0,
        days=spec.offset_days or 0,
    )
    # Seconds resolution by default: an ISO 8601 stamp carrying microseconds is noise in a form
    # field, and an author who wants them says so with `format`.
    return (
        moment.strftime(spec.format)
        if spec.format is not None
        else moment.isoformat("T", "seconds")
    )


def generated_value(spec: Generate) -> str:
    """The value a `generate` step produces, as the text `${vars.*}` will carry."""
    if spec.random is not None:
        return _random_value(spec.random)
    assert spec.datetime is not None
    return _datetime_value(spec.datetime)


@_handler("generate")
def _do_generate(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.generate is not None
    if bindings is None:
        return  # no var scope to write into (e.g. a bare condition eval) — nothing to do
    bindings[f"vars.{step.generate.into.var}"] = generated_value(step.generate)
