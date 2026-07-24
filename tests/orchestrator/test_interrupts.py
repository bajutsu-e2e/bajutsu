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
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import Artifact, intervals
from bajutsu.orchestrator import AlertGuardConfig, run_scenario
from bajutsu.orchestrator.types import AlertEvent
from bajutsu.scenario import Interrupt, Scenario, dump_scenarios, load_scenarios


def _interrupt(condition: dict[str, object], steps: list[dict[str, object]]) -> Interrupt:
    return Interrupt.model_validate({"condition": condition, "steps": steps})


class _RecordingSink:
    """A minimal sink that records the instant-capture kinds fired per step (BE-0314 screenChanged)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
    ) -> list[Artifact]:
        if kinds:
            self.calls.append((step_id, kinds))
        return []

    def start_scenario_intervals(self, sid: str, kinds: list[str]) -> list[intervals.Interval]:
        return []

    def finish_scenario_intervals(
        self, sid: str, started: list[intervals.Interval]
    ) -> list[Artifact]:
        return []


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


def test_recovery_failure_before_the_act_skips_the_steps_own_action() -> None:
    """A pre-act recovery failure is a decided outcome: the step's own action must not still run
    against the screen a failed recovery left broken."""

    driver = FakeDriver([el("ov.close", "X", ["button"]), el("go", "Go", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "g", "steps": [{"tap": {"id": "go"}}]}),
        clock=FakeClock(),
        interrupts=[
            _interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "does.not.exist"}}])
        ],
    )
    assert not result.ok
    assert "does.not.exist" in (result.failure or "")
    assert ("tap", {"id": "go"}) not in driver.actions


def test_recovery_failure_mid_wait_ends_the_wait_immediately() -> None:
    """A recovery failure decided on the first poll must not poll on toward a long wait timeout.

    Before the interrupt guard's failure could short-circuit a wait's own poll loop, a recovery
    failure discovered on the first poll still let `for`/`settled`/`screenChanged` keep polling
    all the way to their own `timeout` — the verdict was still correct (the guard's failure
    overrides once the step returns), but "fail loudly" became "fail loudly, slowly": a long
    `timeout:` turned a decided failure into a real-time stall. `on_interrupt_poll` now returns a
    bool the wait checks alongside its deadline, so the wait ends on the very poll the recovery
    failed, before any `_adaptive_sleep` — `sleep_calls` stays empty, not filled with ~2000
    entries toward a 100s timeout.
    """
    sleep_calls: list[float] = []

    driver = FakeDriver([el("ov.close", "X", ["button"])])  # target never appears
    result = run_scenario(
        driver,
        _scenario(
            {"name": "d", "steps": [{"wait": {"for": {"id": "home.title"}, "timeout": 100}}]}
        ),
        clock=FakeClock(sleep_calls.append),
        interrupts=[
            _interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "does.not.exist"}}])
        ],
    )
    assert not result.ok
    assert "does.not.exist" in (result.failure or "")
    assert sleep_calls == []


def test_recovery_failure_mid_wait_skips_the_end_of_step_alert_guard() -> None:
    """A decided mid-wait recovery failure must not also fire the *outer* step's alert-guard retry.

    The recovery's own failing tap is an ordinary step, so it goes through the standard
    `on_blocked` retry once, like any failing step — that one call is expected and unrelated to
    this fix. What must NOT also happen is the *outer* wait step's own post-`_run_step_body`
    check firing a second, redundant `on_blocked` call: `_run_step_body` returns not-ok once the
    wait aborts on the guard's failure signal, and without a check ahead of that retry, the bare
    not-ok status would trigger a second AI-vision dismiss attempt (and a possible full step
    retry) against a screen the failed recovery already left broken — symmetric with the pre-act
    short-circuit that skips the step's own action for the same reason. So the total stays at 1,
    not 2.
    """
    calls = {"n": 0}

    def on_blocked(_driver: object) -> AlertEvent:
        calls["n"] += 1
        return AlertEvent(label="Not Now")

    driver = FakeDriver([el("ov.close", "X", ["button"])])  # target never appears
    result = run_scenario(
        driver,
        _scenario({"name": "d", "steps": [{"wait": {"for": {"id": "home.title"}, "timeout": 5}}]}),
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(vision=on_blocked),
        interrupts=[
            _interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "does.not.exist"}}])
        ],
    )
    assert not result.ok
    assert "does.not.exist" in (result.failure or "")
    # 1 call: the recovery step's own retry. Not 2: the outer wait step's decided failure must not
    # also trigger the end-of-step alert-guard retry.
    assert calls["n"] == 1


# --- screenChanged capture is not misattributed to the step (Unit 3) ----------------------------


def test_cleared_interstitial_is_not_misattributed_as_the_steps_screen_change() -> None:
    """A `screenChanged` policy must not fire on the recovery's own screen change, only the step's."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        # The recovery clears the overlay (a real screen change); the `go` tap changes nothing.
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "ov.close":
            d.screen = [el("go", "Go", ["button"])]

    driver = FakeDriver([el("ov.close", "X", ["button"]), el("go", "Go", ["button"])], react=react)
    sink = _RecordingSink()
    run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"tap": {"id": "go"}}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
            }
        ),
        clock=FakeClock(),
        sink=sink,
        interrupts=[_interrupt({"exists": {"id": "ov.close"}}, [{"tap": {"id": "ov.close"}}])],
    )
    # step0 is the `go` tap; its `before` is re-baselined to the post-recovery tree, so its own
    # (no-op) action fires no screenChanged capture — only the always-on baseline.
    step0 = [kinds for sid, kinds in sink.calls if sid == "x/step0"]
    assert step0 == [["screenshot.after", "elements"]]


def test_pre_act_guard_reads_fresh_not_a_stale_prev_after_snapshot() -> None:
    """The pre-act check must catch an overlay that appears after the previous step's read.

    On a `screenChanged`-policy step, `before` can be the *previous* step's post-step tree
    (`prev_after`), reused under BE-0234's "nothing we actuated in between" assumption. That
    assumption doesn't cover an interstitial that surfaces asynchronously (a timer/network
    overlay) in the gap between the previous step's read and this step's pre-act check — exactly
    the case `interrupts` exists to catch. The guard must read the live screen there, not trust a
    carried-over snapshot that predates the overlay.
    """

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "overlay.close":
            d.screen = [e for e in d.screen if e["identifier"] != "overlay.close"]

    driver = FakeDriver(
        [el("go", "Go", ["button"]), el("home.button", "Home", ["button"])], react=react
    )

    calls = {"n": 0, "triggered": False}
    original_query = driver.query

    def counting_query() -> list[base.Element]:
        calls["n"] += 1
        # The 1st call is step 0's `before`; the 2nd is step 0's post-step read (becomes step 1's
        # `prev_after`). From the 3rd call on (step 1's pre-act guard check onward), an overlay
        # "appears" once — simulating it surfacing in the gap `prev_after` cannot see.
        if calls["n"] >= 3 and not calls["triggered"]:
            calls["triggered"] = True
            driver.screen = [*driver.screen, el("overlay.close", "X", ["button"])]
        return original_query()

    driver.query = counting_query  # type: ignore[method-assign]

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "stale-before",
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
                "steps": [{"tap": {"id": "go"}}, {"tap": {"id": "home.button"}}],
            }
        ),
        clock=FakeClock(),
        interrupts=[
            _interrupt({"exists": {"id": "overlay.close"}}, [{"tap": {"id": "overlay.close"}}])
        ],
    )
    assert result.ok, result.failure
    taps = [a.get("id") for k, a in driver.actions if k == "tap"]
    assert taps == ["go", "overlay.close", "home.button"]  # caught mid-gap, then step 1 proceeded


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
