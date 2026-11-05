"""Tests for the locale-keyed system-alert label lookup (BE-0320 unit 3).

The table stands in for text SpringBoard renders, which no off-Simulator gate can observe, so these
pin the two properties that *are* checkable here: that a covered prompt/choice/language resolves to
the exact string Apple ships, and that an uncovered one fails loudly rather than guessing.
"""

from __future__ import annotations

from typing import get_args

import pytest

from bajutsu.common.backend_cli import simctl
from bajutsu.common.scenario import (
    HandleSystemAlert,
    Selector,
    SystemAlertChoice,
    SystemAlertPrompt,
    SystemAlertRole,
    UncoveredSystemAlertLocale,
    alert_surfaces,
    covered_languages,
    labels_cover,
    system_alert_label,
    system_alert_role,
    system_alert_shapes,
)


@pytest.mark.parametrize(
    ("prompt", "choice", "locale", "expected"),
    [
        # Transcribed from the iOS Simulator runtime's own strings: UserNotificationsServer's
        # PERMISSION_ALERT_ALLOW / PERMISSION_ALERT_DENY, TCC's
        # REQUEST_ACCESS_{ALLOW,DENY}_kTCCServiceUserTracking, and DragUI's
        # PASTE_AUTHORIZATION_BUTTON_{ALLOW,DENY} (BE-0369).
        ("notifications", "grant", "en_US", "Allow"),
        ("notifications", "deny", "en_US", "Don’t Allow"),
        ("notifications", "grant", "ja_JP", "許可"),
        ("notifications", "deny", "ja_JP", "許可しない"),
        ("tracking", "grant", "en_US", "Allow"),
        ("tracking", "deny", "en_US", "Ask App Not to Track"),
        ("tracking", "grant", "ja_JP", "許可"),
        ("tracking", "deny", "ja_JP", "アプリにトラッキングしないように要求"),
        ("paste", "grant", "en_US", "Allow Paste"),
        ("paste", "deny", "en_US", "Don’t Allow Paste"),
        ("paste", "grant", "ja_JP", "ペーストを許可"),
        ("paste", "deny", "ja_JP", "ペーストを許可しない"),
    ],
)
def test_the_lookup_resolves_the_label_the_os_renders(
    prompt: SystemAlertPrompt, choice: SystemAlertChoice, locale: str, expected: str
) -> None:
    assert system_alert_label(prompt, choice, locale) == expected


@pytest.mark.parametrize("prompt", ["notifications", "paste"])
def test_the_english_deny_label_is_not_the_ascii_apostrophe(prompt: SystemAlertPrompt) -> None:
    # The whole point of the lookup: a hand-typed "Don't Allow" would never match, and the
    # difference is invisible in a scenario file. Pinned so a well-meaning edit cannot "fix" it.
    label = system_alert_label(prompt, "deny", "en_US")
    assert "’" in label
    assert "'" not in label


def test_no_label_carries_the_ascii_apostrophe() -> None:
    # The negative half of the guard above, over the whole table rather than the two prompts whose
    # label happens to contain an apostrophe today: a further prompt transcribed with the ASCII
    # character cannot then reach `_LABELS` unnoticed (BE-0369). Walks every shape, not only the
    # first, since a prompt may render several (BE-0406) and each carries its own transcription.
    for prompt in get_args(SystemAlertPrompt):
        for language in covered_languages(prompt):
            for choice in get_args(SystemAlertChoice):
                for shape in system_alert_shapes(prompt, choice, language):
                    assert "'" not in shape.tap_label
                    assert not any("'" in label for label in shape.identifying_labels)
                    assert not any("'" in label for label in shape.excluded_labels)


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
    # A half-filled entry would make `deny` resolve while `grant` raised a KeyError mid-run. Both
    # axes come from the types themselves, so neither a further prompt nor a third choice can reach
    # the table without this guard covering it too (BE-0369).
    for prompt in get_args(SystemAlertPrompt):
        for language in covered_languages(prompt):
            for choice in get_args(SystemAlertChoice):
                shapes = system_alert_shapes(prompt, choice, language)
                assert shapes and all(s.tap_label for s in shapes)


# --- The shapes a prompt renders as, and which answer paths reach it (BE-0406)


@pytest.mark.parametrize(
    ("choice", "locale", "expected"),
    [
        # Transcribed from the iOS Simulator runtime's own `WebUI.framework` strings, read back from
        # both an 18.6 and a 26.5 runtime: `Save Password (save login information sheet)`, the same
        # key suffixed `in app`, `Never for This Website (save login information sheet)` and
        # `Not Now (save login information sheet)`. The three shapes are the web form, the app's own
        # fields on 18.6, and the same on 26.5 — where only the accepting button's text moved.
        ("grant", "en_US", ["Save Password", "Save Password", "Save"]),
        ("deny", "en_US", ["Not Now", "Not Now", "Not Now"]),
        ("grant", "ja_JP", ["パスワードを保存", "パスワードを保存", "保存"]),
        ("deny", "ja_JP", ["今はしない", "今はしない", "今はしない"]),
    ],
)
def test_save_password_renders_three_shapes(
    choice: SystemAlertChoice, locale: str, expected: list[str]
) -> None:
    assert [s.tap_label for s in system_alert_shapes("savePassword", choice, locale)] == expected


def test_only_the_ios_26_in_app_save_shape_carries_an_exclusion() -> None:
    # "Save" and "Not Now" is also the credit-card update sheet's pair, so that shape alone would
    # answer a sheet no scenario declared; "Never for This Card" is the one label telling them apart.
    # The other two shapes name buttons specific enough to need none.
    shapes = system_alert_shapes("savePassword", "deny", "en_US")
    assert [sorted(s.excluded_labels) for s in shapes] == [[], [], ["Never for This Card"]]


def test_a_step_capable_prompt_renders_exactly_one_shape() -> None:
    # `system_alert_label` answers the `handleSystemAlert` step by taking the first shape, which is
    # only honest while a step-capable prompt has just the one. A second shape added to such a prompt
    # must fail here rather than silently make the step answer with whichever came first.
    for prompt in get_args(SystemAlertPrompt):
        if not alert_surfaces(prompt)["step"]:
            continue
        for language in covered_languages(prompt):
            assert len(system_alert_shapes(prompt, "grant", language)) == 1


def test_the_surface_record_covers_every_prompt_and_leaves_none_unreachable() -> None:
    # The record is what rejects `savePassword` in a step, arms the in-tree paths, and filters the
    # policy pushed to the interruption monitor. A prompt reaching no surface at all would be
    # declarable and unanswerable, which is the state this pins against.
    for prompt in get_args(SystemAlertPrompt):
        surfaces = alert_surfaces(prompt)
        assert any(surfaces.values())
        # A step reads the SpringBoard query, so it can only name what that query can see.
        assert not surfaces["step"] or surfaces["native"]


def test_save_password_is_reachable_only_in_the_tree() -> None:
    # iOS raises it inside the application's own process, so `springboard.alerts` never sees it: the
    # step cannot name it, the native probe cannot answer it, and the in-tree dismissal is the whole
    # mechanism it has.
    assert alert_surfaces("savePassword") == {"step": False, "native": False, "in_tree": True}


@pytest.mark.parametrize("prompt", ["notifications", "tracking", "paste"])
def test_every_springboard_prompt_has_the_measured_position_rule(prompt: SystemAlertPrompt) -> None:
    # BE-0445 Unit 1 read deny first and grant second, two buttons, on every run of all three.
    assert system_alert_role(prompt, "deny") == SystemAlertRole(ordinal=0, count=2)
    assert system_alert_role(prompt, "grant") == SystemAlertRole(ordinal=1, count=2)


def test_a_prompt_the_step_cannot_reach_has_no_position_rule() -> None:
    # iOS draws the save-password prompt inside the application, where the measurement says nothing.
    assert system_alert_role("savePassword", "grant") is None


def test_every_step_capable_prompt_has_a_position_rule() -> None:
    # A step-capable prompt without one would fail the step under every uncovered language again.
    for prompt in get_args(SystemAlertPrompt):
        if alert_surfaces(prompt)["step"]:
            for choice in get_args(SystemAlertChoice):
                assert system_alert_role(prompt, choice) is not None, (prompt, choice)


def test_a_position_rule_names_a_button_only_on_an_alert_of_its_own_size() -> None:
    role = SystemAlertRole(ordinal=1, count=2)
    assert role.pick(["Nicht erlauben", "Erlauben"]) == "Erlauben"
    # Any other size is not the prompt the rule was measured on, so it names nothing.
    assert role.pick(["Erlauben"]) is None
    assert role.pick(["A", "B", "C"]) is None
    assert role.pick([]) is None
    assert role.describe() == "button 2 of 2"


def test_labels_cover_reads_the_language_subtag() -> None:
    assert labels_cover("notifications", "ja_JP")
    assert labels_cover("notifications", "en")
    assert not labels_cover("notifications", "de_DE")


def test_an_uncovered_language_leaves_a_prompt_with_a_position_rule_unresolved() -> None:
    # The rule is resolved against the live alert, since its label is what this run cannot know.
    step = HandleSystemAlert(prompt="notifications", choice="grant", timeout=5)
    assert step.resolved("de_DE") == step
    assert step.role() == SystemAlertRole(ordinal=1, count=2)


def test_a_covered_language_still_resolves_through_the_label_table() -> None:
    step = HandleSystemAlert(prompt="notifications", choice="deny", timeout=5).resolved("ja_JP")
    assert step.sel == Selector(label="許可しない")
    assert step.prompt is None
    assert step.role() is None


def test_every_springboard_prompt_covers_the_same_languages() -> None:
    # The step skips its reservation wherever the table misses its own prompt's language, which is
    # safe only while a guard rule for any other SpringBoard prompt is refused under that same
    # language. A language added for one prompt alone would break that, so pin them together.
    springboard = [p for p in get_args(SystemAlertPrompt) if alert_surfaces(p)["native"]]
    assert len({covered_languages(p) for p in springboard}) == 1


def test_an_alert_title_reduces_to_apple_s_own_template() -> None:
    from bajutsu.common.scenario.system_alerts import alert_title_marker

    assert alert_title_marker("Allow “Showcase SwiftUI” to find devices on local networks?") == (
        "title: Allow “%@” to find devices on local networks?"
    )
    assert alert_title_marker(
        "“ショーケース”がローカルネットワーク上のデバイスを見つけることを許可しますか?"
    ) == ("title: “%@”がローカルネットワーク上のデバイスを見つけることを許可しますか?")
    assert alert_title_marker("No quotes") == "title: No quotes"


def test_local_network_and_notifications_are_told_apart_by_the_title_alone() -> None:
    from bajutsu.common.scenario.system_alerts import system_alert_shapes

    for locale in ("en_US", "ja_JP"):
        (local,) = system_alert_shapes("localNetwork", "grant", locale)
        (notif,) = system_alert_shapes("notifications", "grant", locale)
        marker = next(label for label in local.identifying_labels if label.startswith("title: "))
        assert local.identifying_labels - {marker} == notif.identifying_labels  # same buttons
        assert marker in notif.excluded_labels
