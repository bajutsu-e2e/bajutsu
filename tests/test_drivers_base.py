"""Tests for the shared driver helpers in `bajutsu.drivers.base`.

`wait_until` is the single deadline-polling loop every backend shares (BE-0118): each
driver's `wait_for` is a single-shot check, and this helper turns it into a timeout-honouring
wait uniformly, so a `timeout` means the same real seconds regardless of which backend drives.
"""

from __future__ import annotations

from bajutsu.drivers import base


class LateDriver:
    """A stub driver whose selector matches only after `appear_after` single-shot checks.

    Simulates an element that renders slightly after the call — the case idb's old bespoke
    loop handled and Playwright's single check did not. `poll=0` keeps the test instant.
    """

    name = "late"

    def __init__(self, appear_after: int) -> None:
        self._appear_after = appear_after
        self.checks = 0

    def wait_for(self, sel: base.Selector) -> bool:
        self.checks += 1
        return self.checks > self._appear_after


def test_wait_until_polls_until_a_late_element_appears() -> None:
    driver = LateDriver(appear_after=2)
    assert base.wait_until(driver, {"id": "spinner"}, timeout=5, poll=0) is True
    assert driver.checks >= 3  # kept polling past the not-yet-present checks


def test_wait_until_returns_true_when_already_present() -> None:
    driver = LateDriver(appear_after=0)
    assert base.wait_until(driver, {"id": "home"}, timeout=1, poll=0) is True
    assert driver.checks == 1  # first check already matched


def test_wait_until_times_out_when_never_present() -> None:
    driver = LateDriver(appear_after=10_000)
    assert base.wait_until(driver, {"id": "nope"}, timeout=0, poll=0) is False
