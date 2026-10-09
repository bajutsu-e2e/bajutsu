"""Resolve a named prompt and choice into the shape a backend can act on."""

from __future__ import annotations

import re
from typing import Literal

from ._prompt_surfaces import _PromptSurfaces
from ._prompts import _Prompts
from ._shape import _Shape
from .alert_surfaces import AlertSurfaces
from .resolved_alert_shape import ResolvedAlertShape
from .system_alert_role import SystemAlertRole
from .uncovered_system_alert_locale import UncoveredSystemAlertLocale

# The prompts this table covers. `notifications` matches the permission vocabulary's spelling
# (`drivers.base.PERMISSION_SERVICES`) for the same OS prompt; `tracking` is ATT and `paste` is the
# cross-process pasteboard read, neither of which that vocabulary has an entry for because no
# `simctl` command can pre-answer them. `savePassword` (BE-0406) is the first entry not owned by
# SpringBoard — see `_SURFACES`.
SystemAlertPrompt = Literal["notifications", "tracking", "paste", "savePassword", "localNetwork"]

# The prefix of the one entry an alert's *title* contributes to the labels a rule matches against,
# beside its buttons. The title names the app, so it is first reduced to Apple's own template: every
# “…”-quoted span becomes “%@”, which is exactly how the shipped strings spell the placeholder. The
# prefix keeps the entry from ever equalling a real button label.
TITLE_MARKER = "title: "


def alert_title_marker(title: str) -> str:
    """The label-like entry an alert's title contributes to rule matching (see `TITLE_MARKER`)."""
    return TITLE_MARKER + re.sub(r"“[^”]*”", "“%@”", title)


# `localNetwork` (iOS 14's Local Network privacy prompt) shares its buttons with `notifications` in
# every language below, so its title is what tells the two apart: `localNetwork` names the title's
# marker among its identifying labels, and `notifications` excludes it. Transcribed from
# `NetworkExtension.framework/<lang>.lproj/Localizable.strings` (`APP_WANTS_LOCAL_NETWORK_HEADER`,
# `ALLOW_BUTTON`, `DONT_ALLOW_BUTTON`), identical on the iOS 18.6, 26.5 and 27.0 runtimes.
_LOCAL_NETWORK_TITLE = {
    "en": TITLE_MARKER + "Allow “%@” to find devices on local networks?",
    "ja": TITLE_MARKER + "“%@”がローカルネットワーク上のデバイスを見つけることを許可しますか?",
}

# What the author means, rather than which button says it. `deny` is the prompt's negative choice,
# which is not always a plain refusal — ATT's is "Ask App Not to Track".
SystemAlertChoice = Literal["grant", "deny"]


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
                "excludes": (_LOCAL_NETWORK_TITLE["en"],),
            }
        ],
        "ja": [
            {
                "identifying": ("許可", "許可しない"),
                "grant": "許可",
                "deny": "許可しない",
                "excludes": (_LOCAL_NETWORK_TITLE["ja"],),
            }
        ],
    },
    "localNetwork": {
        "en": [
            {
                "identifying": ("Allow", "Don’t Allow", _LOCAL_NETWORK_TITLE["en"]),
                "grant": "Allow",
                "deny": "Don’t Allow",
                "excludes": (),
            }
        ],
        "ja": [
            {
                "identifying": ("許可", "許可しない", _LOCAL_NETWORK_TITLE["ja"]),
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


# SpringBoard owns every prompt but `savePassword`, so each reaches the step and the native probe and
# nothing else.
# `savePassword` is the mirror image: the in-tree dismissal alone.
_SURFACES: _PromptSurfaces = {
    "notifications": {"step": True, "native": True, "in_tree": False},
    "tracking": {"step": True, "native": True, "in_tree": False},
    "paste": {"step": True, "native": True, "in_tree": False},
    "savePassword": {"step": False, "native": False, "in_tree": True},
    "localNetwork": {"step": True, "native": True, "in_tree": False},
}


# The language-independent rule for each SpringBoard prompt (BE-0445). Measured, not assumed: the
# probe in roadmaps/BE-0445-system-alert-locale-agnostic-answer/misc/ read every button of these
# three prompts under English and Japanese on iOS 18.6 and 26.5, and under Arabic on 26.5, and tapped
# each ordinal to read the authorization status the app was left with. The deny button came first and
# the grant button second in every run, while the identifier and value SpringBoard exposes were empty
# throughout. The ordinal, not the frame: under Arabic the notification prompt draws its deny button
# on the right, so a rule reading screen position would have swapped the two choices. `savePassword` has no entry: iOS draws it
# inside the application, where this measurement says nothing, so it keeps the label table alone.
_DENY_THEN_GRANT: dict[SystemAlertChoice, SystemAlertRole] = {
    "deny": SystemAlertRole(ordinal=0, count=2),
    "grant": SystemAlertRole(ordinal=1, count=2),
}
# `localNetwork` was read on a real iPhone (iOS 27.0.1, English): deny, then grant — the
# same order as the three above. Its other languages are not yet measured.
_ROLES: dict[SystemAlertPrompt, dict[SystemAlertChoice, SystemAlertRole]] = {
    "notifications": _DENY_THEN_GRANT,
    "tracking": _DENY_THEN_GRANT,
    "paste": _DENY_THEN_GRANT,
    "localNetwork": _DENY_THEN_GRANT,
}


def alert_surfaces(prompt: SystemAlertPrompt) -> AlertSurfaces:
    """Which answer paths `prompt` reaches — see `AlertSurfaces`."""
    return _SURFACES[prompt]


def _language(locale: str) -> str:
    # The same subtag `simctl.language_of` derives for the app's `-AppleLanguages` launch argument
    # and the Simulator's pinned system language; split here rather than imported, so the scenario
    # schema stays a portable inner contract that pulls in no device layer. A test pins the two
    # together so they cannot drift.
    return re.split(r"[_-]", locale, maxsplit=1)[0]


def _shapes(prompt: SystemAlertPrompt, locale: str) -> list[_Shape]:
    language = _language(locale)
    shapes = _LABELS[prompt].get(language)
    if shapes is None:
        # Built from the exported helper, so the message and the documented surface cannot drift.
        # Worded for the step, which reaches here only for a prompt with no position rule (BE-0445);
        # the guard's caller (`run/cli.py`) re-scopes it to `systemAlertHandling.rules`.
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


def labels_cover(prompt: SystemAlertPrompt, locale: str) -> bool:
    """Whether the label table knows `prompt`'s buttons under `locale`'s language."""
    return _language(locale) in _LABELS[prompt]


def system_alert_role(
    prompt: SystemAlertPrompt, choice: SystemAlertChoice
) -> SystemAlertRole | None:
    """The language-independent rule naming `prompt`'s `choice` button, or None where none holds.

    For the `handleSystemAlert` step under a language the label table does not cover (BE-0445). The
    reactive guard never uses it: a guard rule must first recognize which declared prompt is on
    screen, and a position says nothing about that.
    """
    return _ROLES.get(prompt, {}).get(choice)
