"""The serve dispatch reads the `systemAlertHandling` request flag and still accepts the
deprecated `alertHandling` (originally BE-0317's canonical name) and `dismissAlerts` keys, the
back-compat guarantee a saved frontend state or a third-party `/api/run` client depends on. The
CLI/scenario/config alias surfaces each have a dedicated test; this covers the serve request-body
fallback branch."""

from __future__ import annotations

from bajutsu.serve.operations.dispatch import _system_alert_handling_flag


def test_system_alert_handling_canonical_key() -> None:
    assert _system_alert_handling_flag({"systemAlertHandling": True}) is True
    assert _system_alert_handling_flag({"systemAlertHandling": False}) is False


def test_alert_handling_deprecated_key_still_resolves() -> None:
    # The old spelling a saved frontend state or a legacy /api/run client may still send.
    assert _system_alert_handling_flag({"alertHandling": False}) is False
    assert _system_alert_handling_flag({"alertHandling": True}) is True


def test_dismiss_alerts_deprecated_key_still_resolves() -> None:
    # The oldest spelling a saved frontend state or a legacy /api/run client may still send.
    assert _system_alert_handling_flag({"dismissAlerts": False}) is False
    assert _system_alert_handling_flag({"dismissAlerts": True}) is True


def test_canonical_key_wins_when_all_present() -> None:
    assert (
        _system_alert_handling_flag(
            {"systemAlertHandling": True, "alertHandling": False, "dismissAlerts": False}
        )
        is True
    )


def test_alert_handling_wins_over_dismiss_alerts_when_both_present() -> None:
    assert _system_alert_handling_flag({"alertHandling": True, "dismissAlerts": False}) is True


def test_absent_or_non_bool_is_none() -> None:
    # Tri-state: no key set (or a non-bool value) leaves the CLI/scenario default in force.
    assert _system_alert_handling_flag({}) is None
    assert _system_alert_handling_flag({"systemAlertHandling": "yes"}) is None
