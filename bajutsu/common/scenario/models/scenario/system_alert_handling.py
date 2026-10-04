"""A scenario's control over the reactive system-alert guard."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from bajutsu.common.scenario.models._base import _Model

from .system_alert_rule import SystemAlertRule


class SystemAlertHandling(_Model):
    """Per-scenario control of the reactive system-alert guard.

    Handling of OS prompts (e.g. a notification or App Tracking Transparency request) that the
    app-scoped accessibility tree cannot see or tap, fired reactively when a step (or `expect`) is
    blocked or a guarded wait finds an alert. The guard is ON by default. On the iOS XCUITest backend
    it clears the prompt deterministically and natively (BE-0315), reusing BE-0316's SpringBoard query
    + tap — no screenshot and no model round trip. Where no path can act the guard does nothing and
    the blocked step reports what it saw (BE-0402). This is the *reactive* counterpart to the
    *proactive* `handleSystemAlert` step (BE-0316): the step taps a named button at an author-chosen
    point, this guard clears prompts automatically wherever they surface.

    `rules` is the whole declaration (BE-0406): a scenario names the prompts it expects and the
    choice to make on each, and every answer path — the native SpringBoard tap and the in-tree
    dismissal alike — matches against those alone. On-disk forms — the bare boolean carries on and
    off, so a mapping always means on:
        systemAlertHandling: false                       — disable the guard for this scenario
        systemAlertHandling: { rules: [{ prompt: notifications, choice: grant }] } — answer a named
                                                             prompt by its own choice, regardless of
                                                             which label it shares with another
    An alert no rule identifies is left alone rather than answered by a guessed button, which is why
    the ordered `labels` list BE-0401 introduced is gone: it said which words the author would accept
    seeing tapped, never which alert they expected, so it licensed a tap on a screen no scenario had
    described.
    """

    # Ordered answers to specific covered prompts, by name rather than by button text. The guard
    # identifies the alert on screen from a rule's own prompt (its resolved identifying labels), so
    # ordering only matters for two rules whose shapes could both match the same alert — none of the
    # prompts `system_alerts.py` covers today can.
    rules: list[SystemAlertRule] = Field(default_factory=list)
    # Free text only the AI vision fallback reads ("tap Allow"), for an alert the native path cannot
    # name — including every alert on a backend with no native path at all. The native path compares
    # labels exactly, so it never reads this; `rules` above is what steers it (BE-0406).
    vision_instruction: str | None = Field(default=None, alias="visionInstruction")
    # How often (seconds) the reactive guard polls the native system-alert presence query while a
    # wait is pending, on its own wall clock decoupled from the wait's condition poll (BE-0315). A
    # heuristic trading detection latency against runner load, so it is a knob rather than hard-coded;
    # None inherits the built-in default (one second).
    poll_interval: float | None = Field(default=None, alias="pollInterval")
    # How long (seconds) a guarded `for` / `screenChanged` wait tolerates a frozen screen — no system
    # alert up, yet no element below the application carrying a label or an identifier — before it
    # stops instead of polling to its own deadline. Armed only for a scenario that declares an in-app
    # prompt such as `savePassword`; an app whose own unlabelled loading screen can outlast the
    # default raises it, and 0 turns the stop off. None inherits the built-in default (10 seconds).
    frozen_screen_timeout: float | None = Field(default=None, alias="frozenScreenTimeout")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_keys(cls, data: Any) -> Any:
        # `extra="forbid"` already rejects each of these, but with Pydantic's generic "extra fields
        # not permitted", which names no replacement. Each was removed with no alias, so the error is
        # the whole migration path an author gets — name the key that replaces each.
        if not isinstance(data, dict):
            return data
        if "labels" in data:
            raise ValueError(
                "systemAlertHandling.labels was removed (BE-0406): a button label named a button, "
                "never the alert it sat on. Declare the prompt instead — rules: "
                "[{ prompt: notifications, choice: grant }]. A prompt the label table does not "
                "cover has no rule form: either add it to "
                "bajutsu/common/scenario/system_alerts.py, or let the guard leave that alert alone "
                "and name it in the blocked step's own failure reason"
            )
        if "instruction" in data:
            raise ValueError(
                "systemAlertHandling.instruction was removed (BE-0401); use 'rules' instead for a "
                "prompt the guard should answer, or 'visionInstruction' for the free text only the "
                "vision fallback reads"
            )
        if "enabled" in data:
            raise ValueError(
                "systemAlertHandling.enabled was removed (BE-0401); write the boolean directly "
                "(`systemAlertHandling: false` to disable, a mapping to configure the policy)"
            )
        return data

    @field_validator("vision_instruction")
    @classmethod
    def _non_empty_vision_instruction(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("systemAlertHandling.visionInstruction must not be empty")
        return v

    @field_validator("poll_interval")
    @classmethod
    def _positive_interval(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("pollInterval must be positive")
        return v

    @field_validator("frozen_screen_timeout")
    @classmethod
    def _non_negative_frozen_timeout(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("frozenScreenTimeout must not be negative (0 turns the stop off)")
        return v

    @field_validator("rules")
    @classmethod
    def _unique_prompts(cls, v: list[SystemAlertRule]) -> list[SystemAlertRule]:
        # Silently taking the first of two rules naming the same prompt would hide an authoring
        # mistake — the same reason an ambiguous selector fails rather than tapping its first match.
        seen = [r.prompt for r in v]
        dupes = sorted({p for p in seen if seen.count(p) > 1})
        if dupes:
            raise ValueError(
                f"systemAlertHandling.rules names {dupes} more than once; "
                "each prompt takes exactly one rule"
            )
        return v
