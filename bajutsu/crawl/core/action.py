"""One replayable action against a screen."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import screen_size
from bajutsu.common.evidence.redaction import PLACEHOLDER


@dataclass(frozen=True)
class Action:
    """A replayable action against a screen.

    `kind` is "tap", "type" (text input), "fill" (enter several fields in one step, to cross a
    precondition that needs more than one field), or "tap_point" (tap a normalized [0,1]
    coordinate — for a control the accessibility tree can't address, e.g. a custom tab bar a
    vision guide located). The element is named by `target` (its accessibility identifier —
    stable, preferred) or, for an id-less element, by `label` (+ `index` to disambiguate
    duplicates); a "type" carries the text in `value`, a "fill" its (id, value) pairs in `fields`,
    a "tap_point" its (x, y) in `point` (`label` optional, for logging). All fields are hashable so
    an Action can key the frontier / tried set.

    `secure` records that the platform marked the field this action enters as a masked input, read
    off the element at the moment the action was built. The screen map keeps no `Element`, so this
    is the only place that trait survives to the artifact, where redaction masks the value (BE-0331).
    """

    kind: str
    target: str = ""
    label: str | None = None
    index: int | None = None
    value: str | None = None
    fields: tuple[tuple[str, str], ...] = ()
    point: tuple[float, float] | None = None
    secure: bool = False

    @property
    def key(self) -> str:
        """Stable identity for de-duplication and the frontier.

        The id, the label[#index], the fill's field set, or the normalized coordinate.
        """
        if self.kind == "fill":
            return "fill:" + ",".join(i for i, _ in self.fields)
        if self.kind == "tap_point" and self.point is not None:
            return f"@@{self.point[0]:.4f},{self.point[1]:.4f}"
        return self.target or f"@{self.label}#{0 if self.index is None else self.index}"

    def as_selector(self) -> base.Selector:
        if self.target:
            return {"id": self.target}
        sel: base.Selector = {}
        if self.label is not None:
            sel["label"] = self.label
        if self.index is not None:
            sel["index"] = self.index
        return sel

    def describe(self) -> str:
        """Name the action and its target, never the value it enters.

        A description is free text that lands in the screen map's node, edge, plan and path fields,
        where no structural masking rule can reach it (BE-0331). Leaving the value out keeps exactly
        one field — the action's own `value` — for redaction to govern; `fill` already counted its
        fields rather than printing them, and replay is unaffected because `perform` reads `value`
        directly and never parses this string.
        """
        if self.kind == "fill":
            return f"fill {len(self.fields)} fields"
        if self.kind == "tap_point" and self.point is not None:
            if self.label:
                return f"tap tab {self.label!r}"
            return f"tap point ({self.point[0]:.2f}, {self.point[1]:.2f})"
        return f"{self.kind} {self.target or (self.label or '?')}"

    def perform(self, driver: base.Driver) -> None:
        """Execute against the live screen.

        A type action focuses the field (tap) then enters its value; a fill does that for each of
        its fields in order; a tap_point taps a coordinate (the normalized point scaled to the live
        screen size); a tap just taps. Replayable because every selector is id- or label-based and
        every coordinate is normalized to the screen.
        """
        if self.kind == "fill":
            for fid, val in self.fields:
                sel: base.Selector = {"id": fid}
                driver.tap(sel)
                driver.type_text(self._replay_value(driver, sel, val, hint=fid))
            return
        if self.kind == "tap_point" and self.point is not None:
            w, h = screen_size(driver)
            driver.tap_point((self.point[0] * w, self.point[1] * h))
            return
        driver.tap(self.as_selector())
        if self.kind == "type":
            driver.type_text(
                self._replay_value(
                    driver,
                    self.as_selector(),
                    self.value or "",
                    hint=f"{self.target} {self.label or ''}",
                )
            )

    def _replay_value(
        self, driver: base.Driver, sel: base.Selector, value: str, *, hint: str
    ) -> str:
        """The text to enter, re-deriving a dummy when the recorded value was masked (BE-0331).

        A warm start (`--continue` / `--resume-src`) rebuilds its actions from the persisted screen
        map, where a masked input's value is the redaction placeholder. Typing that verbatim would
        fail the very password rule `_input_value` is written to satisfy, so the field's own dummy is
        derived again from the element the action resolves to — replay fidelity survives masking.
        """
        # Imported in the method, not at module load: `_functions` builds these actions, and rule 5
        # breaks the cycle the split creates on this side.
        from ._functions import _input_value, value_for_field

        if value != PLACEHOLDER:
            return value
        matched = base.find_all(driver.query(), sel)
        if matched:
            return _input_value(matched[0])
        # The tap above already resolved the field, so this is the near-impossible screen change
        # between the two reads; the action's own record of what it targets still names the field.
        return value_for_field(hint, self.secure)
