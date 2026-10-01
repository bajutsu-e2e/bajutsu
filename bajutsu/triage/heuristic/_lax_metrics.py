"""A scenario's check strength as plain counts, so a fix's before and after are comparable."""

from __future__ import annotations

from dataclasses import dataclass

# --- laxer guard (BE-0023) ---


@dataclass(frozen=True)
class _LaxMetrics:
    """The check-strength of a scenario, reduced to counts so before/after are comparable."""

    assertions: int  # every machine check (scenario `expect` + each step `assert`)
    equals_matchers: int  # value/label matchers pinned to `equals` — the tightest kind
    id_selectors: int  # selectors anchored on an `id`, the uniqueness anchor
    waits: int  # bounded condition waits
    wait_timeout_total: float  # summed wait budget; a raise grows it, a lowering shrinks it
    sleeps: int = 0  # fixed `sleep` pauses — each one a timing workaround a human must justify
