"""The serve dispatch reads the `systemAlertHandling` request flag.

The deprecated `alertHandling` (originally BE-0317's canonical name) and `dismissAlerts` body keys
were deleted with the schema aliases they mirrored (BE-0401), so a client sending one now gets the
tri-state "unset" and the CLI's own default applies — never the opposite of what it asked for, since
the key it names no longer decides anything.
"""

from __future__ import annotations

import pytest

from bajutsu.serve.operations.dispatch import _system_alert_handling_flag


def test_system_alert_handling_canonical_key() -> None:
    assert _system_alert_handling_flag({"systemAlertHandling": True}) is True
    assert _system_alert_handling_flag({"systemAlertHandling": False}) is False


@pytest.mark.parametrize("removed", ["alertHandling", "dismissAlerts"])
def test_removed_keys_no_longer_decide_the_flag(removed: str) -> None:
    assert _system_alert_handling_flag({removed: False}) is None
    assert _system_alert_handling_flag({removed: True}) is None
    # ...and never displace the canonical key when both are present.
    assert _system_alert_handling_flag({"systemAlertHandling": True, removed: False}) is True


def test_absent_or_non_bool_is_none() -> None:
    # Tri-state: no key set (or a non-bool value) leaves the CLI/scenario default in force.
    assert _system_alert_handling_flag({}) is None
    assert _system_alert_handling_flag({"systemAlertHandling": "yes"}) is None
