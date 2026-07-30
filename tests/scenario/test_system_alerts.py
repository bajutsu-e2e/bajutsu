"""Tests for the locale-keyed system-alert label lookup (BE-0320 unit 3).

The table stands in for text SpringBoard renders, which no off-Simulator gate can observe, so these
pin the two properties that *are* checkable here: that a covered prompt/choice/language resolves to
the exact string Apple ships, and that an uncovered one fails loudly rather than guessing.
"""

from __future__ import annotations

import pytest

from bajutsu import simctl
from bajutsu.scenario import (
    UncoveredSystemAlertLocale,
    covered_languages,
    system_alert_label,
)


@pytest.mark.parametrize(
    ("prompt", "choice", "locale", "expected"),
    [
        # Transcribed from the iOS Simulator runtime's own strings: UserNotificationsServer's
        # PERMISSION_ALERT_ALLOW / PERMISSION_ALERT_DENY, and TCC's
        # REQUEST_ACCESS_{ALLOW,DENY}_kTCCServiceUserTracking.
        ("notifications", "grant", "en_US", "Allow"),
        ("notifications", "deny", "en_US", "Don’t Allow"),
        ("notifications", "grant", "ja_JP", "許可"),
        ("notifications", "deny", "ja_JP", "許可しない"),
        ("tracking", "grant", "en_US", "Allow"),
        ("tracking", "deny", "en_US", "Ask App Not to Track"),
        ("tracking", "grant", "ja_JP", "許可"),
        ("tracking", "deny", "ja_JP", "アプリにトラッキングしないように要求"),
    ],
)
def test_the_lookup_resolves_the_label_the_os_renders(
    prompt: str, choice: str, locale: str, expected: str
) -> None:
    assert system_alert_label(prompt, choice, locale) == expected  # type: ignore[arg-type]


def test_the_english_deny_label_is_not_the_ascii_apostrophe() -> None:
    # The whole point of the lookup: a hand-typed "Don't Allow" would never match, and the
    # difference is invisible in a scenario file. Pinned so a well-meaning edit cannot "fix" it.
    assert "’" in system_alert_label("notifications", "deny", "en_US")
    assert "'" not in system_alert_label("notifications", "deny", "en_US")


def test_the_region_does_not_change_the_labels() -> None:
    # SpringBoard localizes by language, not by region, so every `en_*` resolves alike — which is
    # what lets one table cover a locale the run was configured with, whatever its region.
    for locale in ("en", "en_US", "en_GB", "en_AU"):
        assert system_alert_label("notifications", "deny", locale) == system_alert_label(
            "notifications", "deny", "en"
        )


def test_the_language_split_matches_the_app_launch_argument() -> None:
    # The lookup keys on the same subtag `simctl.language_of` derives for the app's own
    # `-AppleLanguages` and for the Simulator's pinned system language; the split is duplicated to
    # keep the scenario schema free of the device layer, so pin the two together here.
    for locale in ("en_US", "ja_JP", "zh_Hans_CN", "fr"):
        assert locale.split("_", 1)[0] == simctl.language_of(locale)


def test_an_uncovered_language_fails_loudly_and_names_what_is_covered() -> None:
    # A guessed label would tap nothing — or the other button. The error names the covered languages
    # and the escape hatch, so an author is not left to read the source to find either.
    with pytest.raises(UncoveredSystemAlertLocale) as exc:
        system_alert_label("notifications", "grant", "de_DE")
    assert "de" in str(exc.value)
    assert "sel.label" in str(exc.value)
    for language in covered_languages("notifications"):
        assert language in str(exc.value)


def test_every_covered_language_answers_both_choices() -> None:
    # A half-filled entry would make `deny` resolve while `grant` raised a KeyError mid-run.
    for prompt in ("notifications", "tracking"):
        for language in covered_languages(prompt):  # type: ignore[arg-type]
            for choice in ("grant", "deny"):
                assert system_alert_label(prompt, choice, language)  # type: ignore[arg-type]
