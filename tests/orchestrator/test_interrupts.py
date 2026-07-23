"""Tests for `interrupts` — deterministic handlers for unpredictable interstitial screens (BE-0314).

The runner checks each `interrupts` entry's `condition` opportunistically against trees it has
already fetched (a `wait`'s poll tick, an act step's pre-action read) and runs the entry's `steps`
to clear the screen wherever it surfaces. The check is the assertion DSL — a machine predicate,
never a model call (prime directive 1) — so the whole feature is covered here with a FakeDriver.
"""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.config import load_config, resolve
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Interrupt, Scenario, dump_scenarios, load_scenarios


def _interrupt(condition: dict[str, object], steps: list[dict[str, object]]) -> Interrupt:
    return Interrupt.model_validate({"condition": condition, "steps": steps})


# --- schema (Units 1) ---------------------------------------------------------------------------


def test_scenario_interrupts_parse() -> None:
    s = load_scenarios(
        """
        - name: t
          interrupts:
            - condition: { exists: { id: att.dialog } }
              steps:
                - tap: { id: att.allow }
          steps:
            - tap: { id: login.button }
        """
    )[0]
    assert len(s.interrupts) == 1
    assert s.interrupts[0].condition.exists is not None
    assert s.interrupts[0].steps[0].tap is not None


def test_empty_interrupts_prunes_from_dump() -> None:
    s = load_scenarios("- name: t\n  steps:\n    - tap: { id: x }\n")
    assert s[0].interrupts == []
    assert "interrupts" not in dump_scenarios(s)


def test_config_interrupts_resolve() -> None:
    cfg = load_config(
        """
        defaults: { backend: [web] }
        targets:
          myapp:
            baseUrl: http://x
            interrupts:
              - condition: { exists: { id: onboarding.skip } }
                steps:
                  - tap: { id: onboarding.skip }
        """
    )
    eff = resolve(cfg, "myapp")
    assert len(eff.run_defaults.interrupts) == 1
    assert eff.run_defaults.interrupts[0].condition.exists is not None


# --- opportunistic check + resume (Units 2/3) ---------------------------------------------------


def test_interrupt_clears_overlay_before_a_tap() -> None:
    """A bare act step: the interstitial is cleared on the pre-action read, then the act proceeds."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "ov.close":
            d.screen = [el("login.button", "Login", ["button"])]

    driver = FakeDriver(
        [el("ov.close", "X", ["button"]), el("login.button", "Login", ["button"])], react=react
    )
    result = run_scenario(
        driver,
        _scenario({"name": "a", "steps": [{"tap": {"id": "login.button"}}]}),
        clock=FakeClock(),
        interrupts=[_interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "ov.close"}}])],
    )
    assert result.ok, result.failure
    taps = [a.get("id") for k, a in driver.actions if k == "tap"]
    assert taps == ["ov.close", "login.button"]  # overlay cleared, then the real act


def test_interrupt_fires_mid_wait_and_resumes_to_target() -> None:
    """A `for` wait: the screen surfaces mid-poll; recovery clears it and the wait finds its target."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "ov.close":
            d.screen = [el("home.title", "Home")]

    driver = FakeDriver([el("ov.close", "X", ["button"])], react=react)
    result = run_scenario(
        driver,
        _scenario({"name": "b", "steps": [{"wait": {"for": {"id": "home.title"}, "timeout": 10}}]}),
        clock=FakeClock(),
        interrupts=[_interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "ov.close"}}])],
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "ov.close"}) in driver.actions


def test_interrupt_fires_during_a_screen_changed_wait() -> None:
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "ov.close":
            d.screen = [el("home.title", "Home")]

    driver = FakeDriver([el("ov.close", "X", ["button"])], react=react)
    result = run_scenario(
        driver,
        _scenario({"name": "sc", "steps": [{"wait": {"until": "screenChanged", "timeout": 10}}]}),
        clock=FakeClock(),
        interrupts=[_interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "ov.close"}}])],
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "ov.close"}) in driver.actions


def test_interrupt_fires_during_a_settled_wait() -> None:
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "ov.close":
            d.screen = [el("home.title", "Home")]

    driver = FakeDriver([el("ov.close", "X", ["button"])], react=react)
    result = run_scenario(
        driver,
        _scenario({"name": "st", "steps": [{"wait": {"until": "settled", "timeout": 10}}]}),
        clock=FakeClock(),
        interrupts=[_interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "ov.close"}}])],
    )
    assert result.ok, result.failure  # settle is best-effort; the point is the recovery ran
    assert ("tap", {"id": "ov.close"}) in driver.actions


# --- re-entrancy cap (Unit 4) -------------------------------------------------------------------


def test_reentrancy_cap_falls_back_to_the_steps_ordinary_outcome() -> None:
    """A recovery that never clears its condition fires a bounded number of times, then gives up."""

    driver = FakeDriver([el("ov.close", "X", ["button"])])  # the tap changes nothing
    result = run_scenario(
        driver,
        _scenario({"name": "c", "steps": [{"wait": {"for": {"id": "home.title"}, "timeout": 5}}]}),
        clock=FakeClock(),
        interrupts=[_interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "ov.close"}}])],
    )
    assert not result.ok  # the wait falls back to its own timeout instead of hanging
    assert "wait timeout" in (result.failure or "")
    tap_count = sum(1 for k, _ in driver.actions if k == "tap")
    assert tap_count == 3  # _INTERRUPT_MAX_FIRES; a mis-set entry cannot loop forever


# --- vars sharing + recovery failure (Unit 7) ---------------------------------------------------


def test_recovery_steps_share_the_scenario_vars() -> None:
    """An entry's `condition` and `steps` interpolate against the same `vars.*` as the scenario."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "att.allow":
            d.screen = [el("login.button", "Login", ["button"])]

    driver = FakeDriver(
        [
            el("att.dialog", "Allow tracking?"),
            el("att.allow", "Allow", ["button"]),
            el("login.button", "Login", ["button"]),
        ],
        react=react,
    )
    result = run_scenario(
        driver,
        _scenario({"name": "v", "steps": [{"tap": {"id": "login.button"}}]}),
        clock=FakeClock(),
        bindings={"vars.dialog": "att.dialog", "vars.close": "att.allow"},
        interrupts=[
            _interrupt({"exists": {"id": "${vars.dialog}"}}, [{"tap": {"id": "${vars.close}"}}])
        ],
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "att.allow"}) in driver.actions  # var-resolved recovery target


def test_recovery_step_failure_fails_the_step_loudly() -> None:
    """A failing recovery step surfaces as the step's failure rather than being swallowed."""

    driver = FakeDriver([el("ov.close", "X", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "f", "steps": [{"tap": {"id": "ov.close"}}]}),
        clock=FakeClock(),
        interrupts=[
            _interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "does.not.exist"}}])
        ],
    )
    assert not result.ok
    assert "does.not.exist" in (result.failure or "")


# --- config-then-scenario order (Units 1/2) -----------------------------------------------------


def test_config_interrupts_run_before_scenario_interrupts() -> None:
    """The effective list is config entries first, then the scenario's own — checked in that order."""

    driver = FakeDriver(
        [
            el("ov", "overlay"),
            el("cfg.btn", "Cfg", ["button"]),
            el("scn.btn", "Scn", ["button"]),
            el("go", "Go", ["button"]),
        ]
    )
    scenario: Scenario = _scenario(
        {
            "name": "order",
            "interrupts": [
                {"condition": {"exists": {"id": "ov"}}, "steps": [{"tap": {"id": "scn.btn"}}]}
            ],
            "steps": [{"tap": {"id": "go"}}],
        }
    )
    config_interrupts = [_interrupt({"exists": {"id": "ov"}}, [{"tap": {"id": "cfg.btn"}}])]
    # Exactly the concatenation `pipeline` performs: config entries first, then the scenario's own.
    result = run_scenario(
        driver,
        scenario,
        clock=FakeClock(),
        interrupts=[*config_interrupts, *scenario.interrupts],
    )
    assert result.ok, result.failure
    first_two = [a.get("id") for k, a in driver.actions if k == "tap"][:2]
    assert first_two == ["cfg.btn", "scn.btn"]  # config handler acts before the scenario's
