"""The three forms an `interrupts` entry takes, and the per-layer rules over a list of them."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import BeforeValidator, Discriminator, Tag

from .interrupt import Interrupt
from .system_alert_interrupt import SystemAlertInterrupt
from .tip_kit_interrupt import TipKitInterrupt

# The keys that decide an entry's form (BE-0473), so a reader never infers it from a value's type.
_FORM_KEYS: dict[str, frozenset[str]] = {
    "screen": frozenset({"condition", "steps", "target"}),
    "systemAlert": frozenset({"systemAlert", "choice", "pollInterval", "frozenScreenTimeout"}),
    "tipKit": frozenset({"tipKit"}),
}
_FORM_NAMES = {
    "screen": "a screen entry",
    "systemAlert": "`systemAlert`",
    "tipKit": "`tipKit`",
}


def _forms_of(data: dict[str, Any]) -> list[str]:
    return [form for form, keys in _FORM_KEYS.items() if keys & data.keys()]


def _one_form(data: Any) -> Any:
    if isinstance(data, dict):
        forms = _forms_of(data)
        if len(forms) > 1:
            # Name the keys too: `target` beside `systemAlert` puts an entry in the screen bucket
            # with no `condition` written, so the form alone would send the author looking for one.
            named = " and ".join(
                f"{_FORM_NAMES[f]} ({', '.join(sorted(_FORM_KEYS[f] & data.keys()))})"
                for f in forms
            )
            raise ValueError(f"an interrupts entry takes one form, but this one mixes {named}")
    return data


def _form_tag(data: Any) -> str:
    if isinstance(data, SystemAlertInterrupt):
        return "systemAlert"
    if isinstance(data, TipKitInterrupt):
        return "tipKit"
    if isinstance(data, dict) and (forms := _forms_of(data)):
        return forms[0]
    # A screen entry with no form key fails on its missing `condition`, the error today's authors
    # already know.
    return "screen"


InterruptEntry = Annotated[
    Annotated[Interrupt, Tag("screen")]
    | Annotated[SystemAlertInterrupt, Tag("systemAlert")]
    | Annotated[TipKitInterrupt, Tag("tipKit")],
    Discriminator(_form_tag),
    BeforeValidator(_one_form),
]


def screen_interrupts(entries: Iterable[object]) -> list[Interrupt]:
    """The screen-form entries of *entries*, the ones whose `condition` the runner polls."""
    return [e for e in entries if isinstance(e, Interrupt)]


def system_alert_interrupts(entries: Iterable[object]) -> list[SystemAlertInterrupt]:
    """The `systemAlert` entries of *entries*, in order."""
    return [e for e in entries if isinstance(e, SystemAlertInterrupt)]


def tip_kit_interrupt(entries: Iterable[object]) -> TipKitInterrupt | None:
    """The `tipKit` entry of *entries*, or None when the list declares none."""
    return next((e for e in entries if isinstance(e, TipKitInterrupt)), None)


def check_interrupt_layer(entries: list[Any], where: str) -> None:
    """Refuse two answers within one layer: two entries for one prompt, or two `tipKit` entries.

    Silently taking the first would hide an authoring mistake, the same reason an ambiguous selector
    fails rather than tapping its first match.

    Raises:
        ValueError: the layer answers one prompt, or TipKit, more than once.
    """
    prompts = [e.system_alert for e in system_alert_interrupts(entries)]
    if dupes := sorted({p for p in prompts if prompts.count(p) > 1}):
        raise ValueError(
            f"{where} names systemAlert {', '.join(dupes)} more than once; "
            "each prompt takes exactly one entry"
        )
    if sum(isinstance(e, TipKitInterrupt) for e in entries) > 1:
        raise ValueError(f"{where} has more than one tipKit entry")
