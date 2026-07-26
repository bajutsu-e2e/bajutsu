"""BE-0317: the serve dispatch reads the `alertHandling` request flag and still accepts the
deprecated `dismissAlerts` key, the back-compat guarantee a saved frontend state or a third-party
`/api/run` client depends on. The CLI/scenario/config alias surfaces each have a dedicated test;
this covers the serve request-body fallback branch."""

from __future__ import annotations

from bajutsu.serve.operations.dispatch import _alert_handling_flag


def test_alert_handling_canonical_key() -> None:
    assert _alert_handling_flag({"alertHandling": True}) is True
    assert _alert_handling_flag({"alertHandling": False}) is False


def test_dismiss_alerts_deprecated_key_still_resolves() -> None:
    # The old spelling a saved frontend state or a legacy /api/run client may still send.
    assert _alert_handling_flag({"dismissAlerts": False}) is False
    assert _alert_handling_flag({"dismissAlerts": True}) is True


def test_canonical_key_wins_when_both_present() -> None:
    assert _alert_handling_flag({"alertHandling": True, "dismissAlerts": False}) is True


def test_absent_or_non_bool_is_none() -> None:
    # Tri-state: neither key set (or a non-bool value) leaves the CLI/scenario default in force.
    assert _alert_handling_flag({}) is None
    assert _alert_handling_flag({"alertHandling": "yes"}) is None
