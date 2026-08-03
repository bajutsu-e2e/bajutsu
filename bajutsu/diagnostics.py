"""What a run says about itself while it happens, and how loud.

Nothing configured logging before this module, so the standard library's last-resort handler carried
every message — which means WARNING and above reached the terminal and every `logger.debug` in the
codebase reached nobody. That is a poor trade for a tool whose hardest failures are timing ones. When
an Android step fails, the question is almost never "what did the assertion say"; it is "which tree
did the actuator resolve against, and had it caught up". The drivers write that down. Until now it
was thrown away, and a CI failure could only be investigated by re-running it with more luck.

`configure` gives the level a knob and leaves the default where it was, so a run that sets nothing
prints exactly what it printed before.
"""

from __future__ import annotations

import logging
import os
import sys

#: The level knob, read from the environment like the run's other lane settings
#: (`BAJUTSU_MIN_WAIT_TIMEOUT`, `BAJUTSU_CRASH_RETRIES`). Accepts a level name in any case.
LEVEL_ENV = "BAJUTSU_LOG_LEVEL"

_DEFAULT_LEVEL = "WARNING"

# The package logger everything under `bajutsu.` propagates to. Configuring here rather than on the
# root keeps a dependency's own logging out of the run's output.
_ROOT_NAME = "bajutsu"

# Marks the handler as this module's, so `configure` can replace its own on a second call without
# discarding a handler the embedding application installed (the `serve` worker installs one).
_OWNED = "_bajutsu_diagnostics"


def configure(level: str | None = None) -> None:
    """Send `bajutsu.*` logging to stderr at `level`, or at whatever `BAJUTSU_LOG_LEVEL` names.

    **A no-op unless a level was actually asked for.** Pinning a level and a handler on the package
    logger unconditionally would be the wrong default twice over: it overrides the effective level an
    embedding application set on the root logger (`serve` raises it there, and its records would be
    filtered out before reaching it), and it hands `bajutsu.*` records to two handlers where there
    used to be one, printing each twice. Asking for a level is the opt-in, and everything below
    happens only then; a run that asks for nothing behaves exactly as it did before this existed.

    Idempotent, and safe to call from an embedding application: it replaces only the handler it
    installed itself.

    Args:
        level: Overrides the environment. An unrecognized name falls back to the default rather than
            failing the command — a mistyped level should not cost a run.
    """
    requested = level or os.environ.get(LEVEL_ENV)
    if requested is None:
        return
    logger = logging.getLogger(_ROOT_NAME)
    for handler in [h for h in logger.handlers if getattr(h, _OWNED, False)]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    # Name the emitter: with debug on, the interleaving of driver, resident-channel, and orchestrator
    # lines is itself the evidence — which one spoke matters as much as what it said.
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    setattr(handler, _OWNED, True)
    logger.addHandler(handler)
    logger.setLevel(resolve_level(requested))
    # Propagation stays on, so the records still reach whatever an embedding application put on the
    # root logger — pytest's `caplog` among them, which is how most of the suite asserts on a
    # warning. The cost is real rather than nil: against a root handler, every record is emitted
    # twice, and this one does not redact. That is why the CLI does not call this for `serve`, whose
    # root sink is the secret-redacting one (BE-0055) — an application that owns its logging should
    # own it alone, and this function is for the commands that have none.


def resolve_level(level: str | None = None) -> int:
    """The numeric level `configure` would use, from the argument, then the environment, then WARNING."""
    name = (level or os.environ.get(LEVEL_ENV) or _DEFAULT_LEVEL).strip().upper()
    resolved = logging.getLevelNamesMapping().get(name)
    return resolved if resolved is not None else logging.WARNING
