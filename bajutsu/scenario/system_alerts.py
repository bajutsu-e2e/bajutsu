"""Locale-keyed button labels for the two iOS system prompts a permission preset cannot reach (BE-0320).

`handleSystemAlert` taps a SpringBoard prompt's button by its visible text, and BE-0320 pins the
Simulator's system language so that text is deterministic. This table closes the remaining gap for
the two prompts BE-0276's `permissions` presets cannot pre-answer — notification authorization is
not a TCC (Transparency, Consent, and Control) service, and App Tracking Transparency (ATT) has no
`simctl` toggle at all — so a scenario about either can name the *intent* (`grant` / `deny`) instead
of transcribing whichever language the pinned locale renders.

Deliberately narrow. It covers those two prompts alone, never an open-ended translation of arbitrary
SpringBoard text, and only the languages whose values have been read back from a Simulator. Every
other alert keeps using the literal `label` / `labelMatches` a scenario supplies, unchanged.

The values are Apple's own, transcribed from the iOS Simulator runtime's shipped strings:
`UserNotificationsServer.framework/<lang>.lproj/Localizable.strings` (`PERMISSION_ALERT_ALLOW` /
`PERMISSION_ALERT_DENY`) and `TCC.framework/<lang>.lproj/Localizable.strings`
(`REQUEST_ACCESS_ALLOW_kTCCServiceUserTracking` / `REQUEST_ACCESS_DENY_kTCCServiceUserTracking`).
Re-reading those files under a new runtime is what checks this table, rather than trusting it.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

# The prompts this table covers. `notifications` matches the permission vocabulary's spelling
# (`drivers.base.PERMISSION_SERVICES`) for the same OS prompt; `tracking` is ATT, which that
# vocabulary has no entry for because no `simctl` command can pre-answer it.
SystemAlertPrompt = Literal["notifications", "tracking"]

# What the author means, rather than which button says it. `deny` is the prompt's negative choice,
# which is not always a plain refusal — ATT's is "Ask App Not to Track".
SystemAlertChoice = Literal["grant", "deny"]


class _Choices(TypedDict):
    """Both choices for one prompt in one language.

    A `TypedDict` so mypy, not a runtime check, rejects a half-filled entry — one that would resolve
    `deny` while `grant` raised a `KeyError` mid-run.
    """

    grant: str
    deny: str


class _Prompts(TypedDict):
    """Every prompt `SystemAlertPrompt` names, with its labels.

    Declaring a new prompt without its labels is then a type error, rather than a schema that
    accepts a step no lookup can resolve. The keys must stay in step with `SystemAlertPrompt`; the
    per-prompt maps are keyed by language subtag (lowercase, no region — see `system_alert_label`).
    """

    notifications: dict[str, _Choices]
    tracking: dict[str, _Choices]


# Keyed by prompt, then language subtag, then choice. Note the English deny labels: the notification
# prompt uses a typographic apostrophe (U+2019), not the ASCII one a hand-typed `label` would carry
# — exactly the transcription trap this lookup removes.
_LABELS: _Prompts = {
    "notifications": {
        "en": {"grant": "Allow", "deny": "Don’t Allow"},
        "ja": {"grant": "許可", "deny": "許可しない"},
    },
    "tracking": {
        "en": {"grant": "Allow", "deny": "Ask App Not to Track"},
        "ja": {"grant": "許可", "deny": "アプリにトラッキングしないように要求"},
    },
}


class UncoveredSystemAlertLocale(ValueError):
    """A `prompt` / `choice` pair was asked for under a language the table does not cover.

    Raised rather than guessed at: a wrong label would tap nothing (or, worse, the other button),
    and BE-0320 exists to remove exactly that kind of accident.
    """


def system_alert_label(prompt: SystemAlertPrompt, choice: SystemAlertChoice, locale: str) -> str:
    """The button label SpringBoard renders for `prompt`'s `choice` under `locale`.

    Args:
        prompt: Which of the two covered OS prompts the step is answering.
        choice: What the author means by the tap, rather than which button says it.
        locale: The scenario's resolved locale (`Preconditions.resolved_locale`); only its language
            subtag selects the labels, since SpringBoard localizes by language, not by region.

    Raises:
        UncoveredSystemAlertLocale: the table has no entry for that language.
    """
    # The same subtag `simctl.language_of` derives for the app's `-AppleLanguages` launch argument
    # and the Simulator's pinned system language; split here rather than imported, so the scenario
    # schema stays a portable inner contract that pulls in no device layer. A test pins the two
    # together so they cannot drift.
    language = re.split(r"[_-]", locale, maxsplit=1)[0]
    labels = _LABELS[prompt].get(language)
    if labels is None:
        # Built from the exported helper, so the message and the documented surface cannot drift.
        covered = ", ".join(covered_languages(prompt))
        raise UncoveredSystemAlertLocale(
            f"handleSystemAlert prompt: {prompt} has no known button labels for language "
            f"{language!r} (locale {locale!r}); covered: {covered}. Name the button directly with "
            "sel.label instead, or add the language to bajutsu/scenario/system_alerts.py"
        )
    return labels[choice]


def covered_languages(prompt: SystemAlertPrompt) -> tuple[str, ...]:
    """The language subtags this table covers for `prompt`, sorted — the documented, testable surface."""
    return tuple(sorted(_LABELS[prompt]))
