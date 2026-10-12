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


def screen_size(driver: base.Driver, elements: list[base.Element] | None = None) -> base.Point:
    """The screen size a normalized point or a screen fraction scales by.

    A `ViewportProvider` reports it directly (BE-0326). The tree's extent overshoots it wherever the
    tree keeps off-screen or oversized nodes — a lazy list's buffered rows, the web DOM, and
    SpringBoard's pixel-sized containers, which tripled every coordinate tap on the Home Screen
    (docs/specs/ios-home-screen-widget-feasibility.md). Any other backend falls back to that extent,
    read from *elements* when the caller already holds a tree.
    """
    if isinstance(driver, base.ViewportProvider):
        return driver.viewport()
    return screen_size_from_elements(driver.query() if elements is None else elements)


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


def tree_buttons(elements: list[base.Element]) -> list[str]:
    """The identifier-less, labelled button text among *elements*.

    The in-tree prompt buttons a rule matches against — the set the XCUITest runner's in-tree pass
    builds too (`AlertResolver.treeButtons`, BajutsuKit) — which the gate reads to tell whether an
    in-tree prompt it gave up on is still showing (BE-0461).
    """
    return [
        el["label"]
        for el in elements
        if el["label"] and not el["identifier"] and base.Trait.BUTTON in el["traits"]
    ]
