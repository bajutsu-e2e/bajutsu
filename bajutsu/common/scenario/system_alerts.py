"""Locale-keyed button labels for the iOS system prompts a permission preset cannot reach (BE-0320).

`handleSystemAlert` taps a SpringBoard prompt's button by its visible text, and BE-0320 pins the
Simulator's system language so that text is deterministic. This table closes the remaining gap for
the prompts BE-0276's `permissions` presets cannot pre-answer — notification authorization is not a
TCC (Transparency, Consent, and Control) service, App Tracking Transparency (ATT) has no `simctl`
toggle at all, and the cross-process paste consent is TCC-backed as `kTCCServicePasteboard` yet has
no `simctl` toggle either (BE-0369) — so a scenario about any of them can name the *intent*
(`grant` / `deny`) instead of transcribing whichever language the pinned locale renders.

Deliberately narrow. It covers those named prompts alone, never an open-ended translation of
arbitrary SpringBoard text, and only the languages whose values have been read back from a
Simulator. Every other alert keeps using the literal `label` / `labelMatches` a scenario supplies,
unchanged.

This is a source of button *labels*, not a claim about which process owns the alert: `savePassword`
is raised into the application's own process, and BE-0406 added it here anyway because the path that
taps a label is chosen separately, by whether the SpringBoard query can see the alert. `_SURFACES`
below is what records that difference, per prompt.

The values are Apple's own, transcribed from the iOS Simulator runtime's shipped strings:
`UserNotificationsServer.framework/<lang>.lproj/Localizable.strings` (`PERMISSION_ALERT_ALLOW` /
`PERMISSION_ALERT_DENY`), `TCC.framework/<lang>.lproj/Localizable.strings`
(`REQUEST_ACCESS_ALLOW_kTCCServiceUserTracking` / `REQUEST_ACCESS_DENY_kTCCServiceUserTracking`),
`DragUI.framework/<lang>.lproj/Localizable.strings` (`PASTE_AUTHORIZATION_BUTTON_ALLOW` /
`PASTE_AUTHORIZATION_BUTTON_DENY`), and — for `savePassword` — `WebUI.framework`'s
`Save Password (save login information sheet)`, `... (save login information sheet in app)`,
`Never for This Website (save login information sheet)`, `Not Now (save login information sheet)`
and `Never for This Card (save credit card data sheet)`.
Re-reading those files under a new runtime is what checks this table, rather than trusting it. Two
properties of `WebUI.framework`'s location matter when re-checking it: it lives in the runtime's
cryptex, at `System/Cryptexes/OS/System/Library/PrivateFrameworks/`, rather than beside the three
frameworks above; and every `.strings` file is an Apple binary property list, so it yields its
contents to `plutil` rather than to a plain-text search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypedDict

# The prompts this table covers. `notifications` matches the permission vocabulary's spelling
# (`drivers.base.PERMISSION_SERVICES`) for the same OS prompt; `tracking` is ATT and `paste` is the
# cross-process pasteboard read, neither of which that vocabulary has an entry for because no
# `simctl` command can pre-answer them. `savePassword` (BE-0406) is the first entry not owned by
# SpringBoard — see `_SURFACES`.
SystemAlertPrompt = Literal["notifications", "tracking", "paste", "savePassword"]

# What the author means, rather than which button says it. `deny` is the prompt's negative choice,
# which is not always a plain refusal — ATT's is "Ask App Not to Track".
SystemAlertChoice = Literal["grant", "deny"]


class _Shape(TypedDict):
    """One rendering of a prompt in one language: how to recognize it, and what each choice taps.

    A prompt renders as more than one shape when the operating system varies its buttons by context
    or by version — `savePassword` does both. `identifying` is every label that must be present for
    this shape to be the alert on screen; `excludes` is the labels whose presence rules it out, for
    a shape another alert's button set would otherwise satisfy. A `TypedDict` so mypy, not a runtime
    check, rejects a half-filled entry — one that would resolve `deny` while `grant` raised a
    `KeyError` mid-run.
    """

    identifying: tuple[str, ...]
    grant: str
    deny: str
    excludes: tuple[str, ...]


class _Prompts(TypedDict):
    """Every prompt `SystemAlertPrompt` names, with its labels.

    Declaring a new prompt without its labels is then a type error, rather than a schema that
    accepts a step no lookup can resolve. The keys must stay in step with `SystemAlertPrompt`; the
    per-prompt maps are keyed by language subtag (lowercase, no region — see `system_alert_label`).
    """

    notifications: dict[str, list[_Shape]]
    tracking: dict[str, list[_Shape]]
    paste: dict[str, list[_Shape]]
    savePassword: dict[str, list[_Shape]]


# Keyed by prompt, then language subtag, then one entry per shape. Note the English deny labels for
# the first three: the notification and paste prompts use a typographic apostrophe (U+2019), not the
# ASCII one a hand-typed `label` would carry — exactly the transcription trap this lookup removes.
#
# `savePassword`'s three shapes are ordered web form, then the application's own fields on iOS 18.6,
# then the same on 26.5. Only the accepting button moves: 26.5 carries a key 18.6 does not
# (`Save Password (save login information sheet in app)`, whose value is "Save"), so the same intent
# reads "Save Password" on a web form and "Save" in an application's own fields. The refusing button
# is "Not Now" throughout. The 26.5 shape is the one needing an exclusion: "Save" and "Not Now"
# alone are also the credit-card update sheet's pair, and "Never for This Card" is the one label
# that tells the two apart.
_LABELS: _Prompts = {
    "notifications": {
        "en": [
            {
                "identifying": ("Allow", "Don’t Allow"),
                "grant": "Allow",
                "deny": "Don’t Allow",
                "excludes": (),
            }
        ],
        "ja": [
            {
                "identifying": ("許可", "許可しない"),
                "grant": "許可",
                "deny": "許可しない",
                "excludes": (),
            }
        ],
    },
    "tracking": {
        "en": [
            {
                "identifying": ("Allow", "Ask App Not to Track"),
                "grant": "Allow",
                "deny": "Ask App Not to Track",
                "excludes": (),
            }
        ],
        "ja": [
            {
                "identifying": ("許可", "アプリにトラッキングしないように要求"),
                "grant": "許可",
                "deny": "アプリにトラッキングしないように要求",
                "excludes": (),
            }
        ],
    },
    "paste": {
        "en": [
            {
                "identifying": ("Allow Paste", "Don’t Allow Paste"),
                "grant": "Allow Paste",
                "deny": "Don’t Allow Paste",
                "excludes": (),
            }
        ],
        "ja": [
            {
                "identifying": ("ペーストを許可", "ペーストを許可しない"),
                "grant": "ペーストを許可",
                "deny": "ペーストを許可しない",
                "excludes": (),
            }
        ],
    },
    "savePassword": {
        "en": [
            {
                "identifying": ("Save Password", "Never for This Website", "Not Now"),
                "grant": "Save Password",
                "deny": "Not Now",
                "excludes": (),
            },
            {
                "identifying": ("Save Password", "Not Now"),
                "grant": "Save Password",
                "deny": "Not Now",
                "excludes": (),
            },
            {
                "identifying": ("Save", "Not Now"),
                "grant": "Save",
                "deny": "Not Now",
                "excludes": ("Never for This Card",),
            },
        ],
        "ja": [
            {
                "identifying": ("パスワードを保存", "このWebサイトでは保存しない", "今はしない"),
                "grant": "パスワードを保存",
                "deny": "今はしない",
                "excludes": (),
            },
            {
                "identifying": ("パスワードを保存", "今はしない"),
                "grant": "パスワードを保存",
                "deny": "今はしない",
                "excludes": (),
            },
            {
                "identifying": ("保存", "今はしない"),
                "grant": "保存",
                "deny": "今はしない",
                "excludes": ("このカードの情報は保存しない",),
            },
        ],
    },
}


class AlertSurfaces(TypedDict):
    """Which of Bajutsu's three answer paths a prompt can actually be declared and answered on.

    Recorded per prompt because `savePassword` is the first to diverge (BE-0406): iOS raises it into
    the application's own process, so `springboard.alerts` never sees it and only the guard's in-tree
    dismissal can clear it. Three consumers read this — the `handleSystemAlert` step rejects a prompt
    it could never resolve, the in-tree paths arm only on rules a tree match can reach, and the
    interruption policy pushed to the runner drops a rule that surface can never meet.
    """

    step: bool
    native: bool
    in_tree: bool


class _PromptSurfaces(TypedDict):
    """Every prompt `SystemAlertPrompt` names, with the surfaces it reaches.

    A `TypedDict` for the same reason `_Prompts` is one: declaring a prompt without its record is
    then a type error, rather than a `KeyError` raised out of `alert_surfaces` at parse time.
    """

    notifications: AlertSurfaces
    tracking: AlertSurfaces
    paste: AlertSurfaces
    savePassword: AlertSurfaces


# SpringBoard owns the first three, so each reaches the step and the native probe and nothing else.
# `savePassword` is the mirror image: the in-tree dismissal alone.
_SURFACES: _PromptSurfaces = {
    "notifications": {"step": True, "native": True, "in_tree": False},
    "tracking": {"step": True, "native": True, "in_tree": False},
    "paste": {"step": True, "native": True, "in_tree": False},
    "savePassword": {"step": False, "native": False, "in_tree": True},
}


@dataclass(frozen=True)
class ResolvedAlertShape:
    """One shape of one prompt, resolved for a locale and a choice.

    The scenario layer's half of `orchestrator.types.ResolvedAlertRule`, kept here so the schema
    stays a portable inner contract that pulls in no orchestrator layer; `run` pairs each of these
    with the prompt's `AlertSurfaces` to build the rule the guard matches with.
    """

    identifying_labels: frozenset[str]
    tap_label: str
    excluded_labels: frozenset[str]


class UncoveredSystemAlertLocale(ValueError):
    """A `prompt` / `choice` pair was asked for under a language the table does not cover.

    Raised rather than guessed at: a wrong label would tap nothing (or, worse, the other button),
    and BE-0320 exists to remove exactly that kind of accident.
    """


def alert_surfaces(prompt: SystemAlertPrompt) -> AlertSurfaces:
    """Which answer paths `prompt` reaches — see `AlertSurfaces`."""
    return _SURFACES[prompt]


def _shapes(prompt: SystemAlertPrompt, locale: str) -> list[_Shape]:
    # The same subtag `simctl.language_of` derives for the app's `-AppleLanguages` launch argument
    # and the Simulator's pinned system language; split here rather than imported, so the scenario
    # schema stays a portable inner contract that pulls in no device layer. A test pins the two
    # together so they cannot drift.
    language = re.split(r"[_-]", locale, maxsplit=1)[0]
    shapes = _LABELS[prompt].get(language)
    if shapes is None:
        # Built from the exported helper, so the message and the documented surface cannot drift.
        covered = ", ".join(covered_languages(prompt))
        raise UncoveredSystemAlertLocale(
            f"handleSystemAlert prompt: {prompt} has no known button labels for language "
            f"{language!r} (locale {locale!r}); covered: {covered}. Name the button directly with "
            "sel.label instead, or add the language to bajutsu/common/scenario/system_alerts.py"
        )
    return shapes


def system_alert_label(prompt: SystemAlertPrompt, choice: SystemAlertChoice, locale: str) -> str:
    """The button label SpringBoard renders for `prompt`'s `choice` under `locale`.

    For the `handleSystemAlert` step, which taps one button. Only a step-capable prompt reaches
    here, and every one of those renders a single shape — a test pins that, so the indexing below
    cannot silently start answering with the first of several.

    Args:
        prompt: Which of the covered OS prompts the step is answering.
        choice: What the author means by the tap, rather than which button says it.
        locale: The scenario's resolved locale (`Preconditions.resolved_locale`); only its language
            subtag selects the labels, since SpringBoard localizes by language, not by region.

    Raises:
        UncoveredSystemAlertLocale: the table has no entry for that language.
    """
    return _shapes(prompt, locale)[0][choice]


def system_alert_shapes(
    prompt: SystemAlertPrompt, choice: SystemAlertChoice, locale: str
) -> tuple[ResolvedAlertShape, ...]:
    """Every shape of `prompt` under `locale`, each with the label `choice` taps on it.

    For the reactive guard, which identifies an alert before tapping it and so needs all of a
    prompt's renderings rather than one button (BE-0406).

    Raises:
        UncoveredSystemAlertLocale: the table has no entry for that language.
    """
    return tuple(
        ResolvedAlertShape(
            identifying_labels=frozenset(shape["identifying"]),
            tap_label=shape[choice],
            excluded_labels=frozenset(shape["excludes"]),
        )
        for shape in _shapes(prompt, locale)
    )


def covered_languages(prompt: SystemAlertPrompt) -> tuple[str, ...]:
    """The language subtags this table covers for `prompt`, sorted — the documented, testable surface."""
    return tuple(sorted(_LABELS[prompt]))
