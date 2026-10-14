"""How one codegen target renders a scenario's `cleanup` rules (BE-0392)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CleanupEmission:
    """How one target renders a scenario's `cleanup` rules (BE-0392).

    Two shapes cover the three targets. A target whose teardown is *registered* (XCTest's
    `addTeardownBlock`) fills `prologue` alone; one that must *wrap* the body in a
    `try`/`catch`/`finally` to observe the outcome (Playwright, UI Automator) fills all three, and
    `body_indent` is how many extra levels the wrapped body sits at. A target that can express
    neither must still say so out loud: it returns the labeled `// TODO` lines as its `prologue`,
    the convention BE-0026 and BE-0314 already use, never a silent skip. All three targets today
    express the phase natively, so no such fallback is written yet.
    """

    prologue: list[str] = field(default_factory=list)
    epilogue: list[str] = field(default_factory=list)
    body_indent: int = 0
