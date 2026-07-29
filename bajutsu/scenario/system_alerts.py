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

from typing import Literal, get_args

# The prompts this table covers. `notifications` matches the permission vocabulary's spelling
# (`drivers.base.PERMISSION_SERVICES`) for the same OS prompt; `tracking` is ATT, which that
# vocabulary has no entry for because no `simctl` command can pre-answer it.
SystemAlertPrompt = Literal["notifications", "tracking"]

# What the author means, rather than which button says it. `deny` is the prompt's negative choice,
# which is not always a plain refusal — ATT's is "Ask App Not to Track".
SystemAlertChoice = Literal["grant", "deny"]

# Keyed by prompt, then language subtag, then choice. Note the English deny labels: the notification
# prompt uses a typographic apostrophe (U+2019), not the ASCII one a hand-typed `label` would carry
# — exactly the transcription trap this lookup removes.
_LABELS: dict[SystemAlertPrompt, dict[str, dict[SystemAlertChoice, str]]] = {
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
    by_language = _LABELS[prompt]
    # The same subtag `simctl.language_of` derives for the app's `-AppleLanguages` launch argument
    # and the Simulator's pinned system language; split here rather than imported, so the scenario
    # schema stays a portable inner contract that pulls in no device layer. A test pins the two
    # together so they cannot drift.
    language = locale.split("_", 1)[0]
    labels = by_language.get(language)
    if labels is None:
        covered = ", ".join(sorted(by_language))
        raise UncoveredSystemAlertLocale(
            f"handleSystemAlert prompt: {prompt} has no known button labels for language "
            f"{language!r} (locale {locale!r}); covered: {covered}. Name the button directly with "
            "sel.label instead, or add the language to bajutsu/scenario/system_alerts.py"
        )
    return labels[choice]


def covered_languages(prompt: SystemAlertPrompt) -> tuple[str, ...]:
    """The language subtags this table covers for `prompt`, sorted — the documented, testable surface."""
    return tuple(sorted(_LABELS[prompt]))


# Every prompt in the `Literal` carries a table, and every table covers both choices in *its*
# `Literal` — asserted at import so a future prompt or choice cannot be declared in the schema while
# silently missing its labels (the schema would accept a step no lookup could resolve).
assert set(_LABELS) == set(get_args(SystemAlertPrompt))
assert all(
    set(labels) == set(get_args(SystemAlertChoice))
    for by_language in _LABELS.values()
    for labels in by_language.values()
)
