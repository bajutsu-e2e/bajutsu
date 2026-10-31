"""The `interrupts` entries: what a run clears on its own when it surfaces mid-scenario (BE-0314, BE-0473).

One list holds three forms, told apart by the key an entry writes rather than by a value's type: a
screen in the app's own tree (`condition` + `steps`), an operating-system prompt (`systemAlert` +
`choice`), and Apple's TipKit popover (`tipKit`). A target's `hooks.interrupts` and a scenario's own
`interrupts` take the same forms, so one list answers "what does this run dismiss on its own?".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Discriminator, Field, Tag, field_validator, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.assertions import Assertion
from bajutsu.common.scenario.system_alerts import SystemAlertPrompt, alert_surfaces

from .step import Step


class Interrupt(_Model):
    """A handler for an interstitial screen that can appear at an unpredictable point (BE-0314).

    ``condition`` is the same assertion the ``if`` step evaluates; the runner checks it
    opportunistically against trees it has already fetched (a ``wait``'s poll tick, an act step's
    pre-action read), wherever in the step sequence the screen happens to surface, and runs
    ``steps`` to clear it when it matches. The steps share the enclosing scenario's ``vars.*``, the
    same as ``if``'s branches do.

    ``target`` names which of the scenario's declared targets the entry watches (BE-0438). Unlike
    ``Step.target`` it stays optional however many targets the scenario declares: an omitted value
    resolves to the primary target. A step in ``steps`` that omits its own ``target`` runs on the
    target the entry watches, not on the primary one — or, nested under an ``if`` / ``forEach``
    that names a target, on that step's target.
    """

    condition: Assertion
    steps: list[Step] = Field(default_factory=list)
    target: str | None = None


class SystemAlertInterrupt(_Model):
    """An operating-system prompt the reactive guard answers, by name (BE-0406, BE-0473).

    ``systemAlert`` names the prompt and ``choice`` the answer, in the vocabulary the
    ``handleSystemAlert`` step takes, so the same entry grants or denies under any locale
    `bajutsu.common.scenario.system_alerts` covers. ``leave`` answers nothing: a scenario writes it to
    turn off the target's answer for that prompt, typically because its own ``handleSystemAlert``
    step does the work. An alert no entry names is left alone rather than answered by a guessed
    button.
    """

    system_alert: SystemAlertPrompt = Field(alias="systemAlert")
    choice: Literal["grant", "deny", "leave"]
    # How often (seconds) the guard checks SpringBoard while a wait is pending. The guard has one
    # cadence per scenario, so the layer that sets it wins and, within a layer, the smallest value —
    # a shorter interval satisfies every entry that asked for a longer one (BE-0473).
    poll_interval: float | None = Field(default=None, alias="pollInterval")
    # How long (seconds) a guarded wait tolerates a frozen screen before it stops, for a prompt raised
    # into the app's own tree (`savePassword`); 0 turns the stop off. It describes the app, so a
    # scenario entry for the same prompt that omits it inherits the target's value.
    frozen_screen_timeout: float | None = Field(default=None, alias="frozenScreenTimeout")

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

    @model_validator(mode="after")
    def _frozen_needs_in_tree(self) -> SystemAlertInterrupt:
        # The frozen-screen stop arms only for a prompt answered inside the app's own tree, so the
        # value on any other prompt would be read by nothing.
        if (
            self.frozen_screen_timeout is not None
            and not alert_surfaces(self.system_alert)["in_tree"]
        ):
            raise ValueError(
                f"frozenScreenTimeout applies only to a prompt raised into the app's own tree "
                f"(such as savePassword), not to {self.system_alert!r}"
            )
        return self


class TipKitInterrupt(_Model):
    """Apple's TipKit popover: ``dismiss`` clears a blocking tip, ``leave`` keeps it (BE-0473).

    iOS only. Off unless an entry asks for it, since a tip is sometimes the very thing a scenario
    asserts on; a scenario writes ``leave`` to turn off its target's ``dismiss``.
    """

    tip_kit: Literal["dismiss", "leave"] = Field(alias="tipKit")


# The key that decides each form, in the order an error lists them.
_FORM_KEYS: dict[str, str] = {
    "condition": "screen",
    "systemAlert": "systemAlert",
    "tipKit": "tipKit",
}
# Keys that belong to exactly one form, so a mix of two forms is caught by any one of them.
_FORM_ONLY_KEYS: dict[str, str] = {
    "condition": "screen",
    "steps": "screen",
    "target": "screen",
    "systemAlert": "systemAlert",
    "choice": "systemAlert",
    "pollInterval": "systemAlert",
    "frozenScreenTimeout": "systemAlert",
    "tipKit": "tipKit",
}


def _check_form(data: Any) -> Any:
    # Pydantic's own union error lists every branch's failures, which hides the one mistake: an entry
    # that writes keys of two forms, or of none.
    if not isinstance(data, dict):
        return data
    forms = list(dict.fromkeys(_FORM_ONLY_KEYS[k] for k in data if k in _FORM_ONLY_KEYS))
    if len(forms) > 1:
        raise ValueError(
            f"an interrupts entry mixes the {' and '.join(forms)} forms; write one entry per form"
        )
    if not any(k in data for k in _FORM_KEYS):
        raise ValueError(
            "an interrupts entry needs one of `condition` (a screen), `systemAlert` (an OS prompt), "
            "or `tipKit` (Apple's TipKit popover)"
        )
    return data


def _form_of(data: Any) -> str | None:
    if isinstance(data, dict):
        return next((form for key, form in _FORM_KEYS.items() if key in data), None)
    if isinstance(data, SystemAlertInterrupt):
        return "systemAlert"
    if isinstance(data, TipKitInterrupt):
        return "tipKit"
    return "screen" if isinstance(data, Interrupt) else None


InterruptEntry = Annotated[
    Annotated[Interrupt, Tag("screen")]
    | Annotated[SystemAlertInterrupt, Tag("systemAlert")]
    | Annotated[TipKitInterrupt, Tag("tipKit")],
    Discriminator(_form_of),
    BeforeValidator(_check_form),
]


def screen_entries(entries: Sequence[object]) -> list[Interrupt]:
    """The screen entries of an `interrupts` list, in order."""
    return [e for e in entries if isinstance(e, Interrupt)]


def system_alert_entries(entries: Sequence[object]) -> list[SystemAlertInterrupt]:
    """The system-alert entries of an `interrupts` list, in order."""
    return [e for e in entries if isinstance(e, SystemAlertInterrupt)]


def tip_kit_entry(entries: Sequence[object]) -> TipKitInterrupt | None:
    """The TipKit entry of an `interrupts` list, or None when it has none."""
    return next((e for e in entries if isinstance(e, TipKitInterrupt)), None)


def check_interrupt_entries(entries: Sequence[object], where: str) -> None:
    """Refuse two entries for one prompt, or two TipKit entries, within one list.

    Silently taking the first of two would hide an authoring mistake — the same reason an ambiguous
    selector fails rather than tapping its first match.

    Raises:
        ValueError: A prompt, or the TipKit entry, appears more than once in *where*.
    """
    prompts = [e.system_alert for e in system_alert_entries(entries)]
    if dupes := sorted({p for p in prompts if prompts.count(p) > 1}):
        raise ValueError(
            f"{where} names systemAlert {', '.join(dupes)} more than once; "
            "each prompt takes exactly one entry"
        )
    if sum(isinstance(e, TipKitInterrupt) for e in entries) > 1:
        raise ValueError(f"{where} has more than one tipKit entry")
