"""Read a config file into the schema, coercing the shapes YAML admits."""

from __future__ import annotations

from typing import Any


def _as_list(v: Any) -> Any:
    return [v] if isinstance(v, str) else v


def _check_platform(v: str | None) -> str | None:
    """Reject an unknown `platform` token at load time, so a typo fails loudly here.

    `None` means "derive the platform from the backend", so it passes through (BE-0009 Slice 4).
    """
    if v is None:
        return v
    # Imported lazily so this schema module carries no top-level dependency on `bajutsu.common.backends`
    # (only the sibling `resolve` module does); the token set is tiny and this validator runs once.
    from bajutsu.common.backends import PLATFORMS

    if v not in PLATFORMS:
        raise ValueError(f"invalid platform {v!r}: use one of {', '.join(PLATFORMS)}")
    return v


def _check_requires(v: list[str], surface: str) -> list[str]:
    """Validate a written `requires` list: refuse the reserved prefixes, and warn it is deprecated.

    A worker fills `environment:` and `host:` itself (BE-0448, BE-0450), so a target naming one by
    hand could only send its job to a worker that cannot run it, or to none. An empty list says
    nothing, so it neither fails nor warns.
    """
    if not v:
        return v
    # Imported lazily for the same reason as `_check_platform`: `worker_capability` reaches
    # `bajutsu.common.backends`, which this schema module keeps off its import path.
    from bajutsu.common.capability.worker_capability import (
        RESERVED_ROUTING_REASON,
        reserved_routing_tokens,
    )
    from bajutsu.common.deprecations import warn_requires

    if reserved := reserved_routing_tokens(v):
        raise ValueError(f"{', '.join(reserved)}: {RESERVED_ROUTING_REASON}")
    warn_requires(surface)
    return v
