"""Platform-neutral predicates and geometry over a normalized element tree.

Pure helpers the deterministic core shares across backends and paths — assertions, the crawl,
the runner pipeline, and record — computed from a ``list[base.Element]`` alone (no browser, no
Simulator). They live here, not in a periphery module, so the core does not depend on the record
/ AI paths to read an element tree (BE-0112).
"""

from __future__ import annotations

from bajutsu.common.drivers import base


def screen_size_from_elements(elements: list[base.Element]) -> tuple[float, float]:
    """Max frame width/height across all elements (the screen bounds)."""
    w = max((el["frame"][0] + el["frame"][2] for el in elements), default=0.0)
    h = max((el["frame"][1] + el["frame"][3] for el in elements), default=0.0)
    return (w, h)


def shows_app_ui(elements: list[base.Element]) -> bool:
    """Whether the tree shows the app's own UI (rather than being collapsed under a system overlay).

    A SpringBoard alert collapses the app's tree to a bare window; a live app screen
    has actionable content. "Actionable" = any non-application element carrying an `identifier`
    OR a `label`, so apps WITHOUT accessibility identifiers (label/coordinate-driven, e.g. the
    showcase `-noax` variants) are not mistaken for a blocked screen — the bug that made the
    guard fire every turn.
    """
    return any(
        (el.get("identifier") or el.get("label")) and "application" not in (el.get("traits") or [])
        for el in elements
    )


def tree_signature(elements: list[base.Element]) -> tuple[tuple[str | None, str | None], ...]:
    """A cheap identity for one poll's screen, used to tell a tap that did nothing from one that did.

    A label still matching a poll's own tree after a tap is not by itself evidence that whatever it
    named is still up: an app-authored button carrying the same label, revealed once a covering
    sheet closed, matches just as well, and treating the two as the same element re-taps the app
    under test rather than the sheet (`_AlertGuardGate._dismiss_from_tree`,
    `waits/_alert_guard_gate.py`) or keeps a shape `AlertGuardConfig.__call__` has already dismissed
    in its `exclude` record past the tap that cleared it. `AlertGuardConfig.__call__`'s own tree
    bound-exhaustion check deliberately does *not* consult this signature — a sheet that accepts a
    tap and re-presents itself with a validation error changes the tree by construction — so what
    `__call__` uses it for is narrower: retracting an already-dismissed shape from its own `exclude`
    once the tree has moved (BE-0418).

    A tap the app never acted on leaves the screen byte-identical; a tap that dismissed a sheet does
    not. Comparing this signature is what makes "the tap did not land" a measured claim rather than
    an assumption. Labels and identifiers rather than frames, so an animation settling a few pixels
    does not read as a changed screen.
    """
    return tuple((el["label"], el["identifier"]) for el in elements)
