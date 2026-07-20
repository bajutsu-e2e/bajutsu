"""The `manual` human-takeover step at run time (BE-0185).

A takeover marker has no deterministic run-time equivalent, so the run loop must surface it as an
explicit, labeled failure — never a silent pass, never a hang. Verified through the orchestrator's
dispatch against the fake driver.
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import load_scenarios


def _run(spec: str) -> tuple[bool, list[tuple[str, object]], str | None]:
    driver = FakeDriver(screen=[])
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n{spec}")[0])
    return result.ok, driver.actions, result.failure


def test_manual_step_fails_loudly_with_its_label() -> None:
    ok, actions, failure = _run('    - manual: { label: "solve the CAPTCHA" }\n')
    assert not ok  # never a silent pass
    assert failure is not None and "solve the CAPTCHA" in failure
    assert actions == []  # nothing is actuated on the device


def test_unreproducible_manual_step_names_the_missing_equivalent() -> None:
    _ok, _actions, failure = _run('    - manual: { label: "solve the CAPTCHA" }\n')
    assert failure is not None and "no deterministic" in failure.lower()


def test_bypassable_manual_step_points_at_the_bypass() -> None:
    _ok, _actions, failure = _run(
        '    - manual: { label: "approve Face ID", bypass: "disable biometrics behind a test flag" }\n'
    )
    assert failure is not None
    assert "disable biometrics behind a test flag" in failure


def test_manual_step_raises_manual_step_required() -> None:
    # The dedicated error is a clean, labeled action failure (a subclass of UnsupportedAction),
    # so the loop surfaces it rather than crashing — and callers can distinguish it.
    assert issubclass(base.ManualStepRequired, base.UnsupportedAction)
