"""Tests for `ResolvingFakeDriver`, the fake that answers alerts inside its reads (BE-0461)."""

from __future__ import annotations

from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import ResolvingFakeDriver

_NOTIFICATIONS = (frozenset({"Allow", "Don't Allow"}), "Don't Allow", frozenset[str]())
_SAVE_PASSWORD = (frozenset({"Save Password", "Not Now"}), "Not Now", frozenset[str]())


def _button(label: str) -> base.Element:
    return el(label=label, traits=[base.Trait.BUTTON])


def _driver(*, governs: bool = True) -> ResolvingFakeDriver:
    driver = ResolvingFakeDriver(screen=[el("home", "Home")])
    driver.set_interruption_policy([_NOTIFICATIONS], governs, in_tree_rules=[_SAVE_PASSWORD])
    return driver


def test_it_advertises_the_capability_and_the_protocol() -> None:
    driver = _driver()
    assert base.Capability.RESOLVE_ALERTS in driver.capabilities()
    assert isinstance(driver, base.AlertResolvingTarget)


def test_a_resolving_read_presses_the_native_alert_and_reports_it_once() -> None:
    driver = _driver()
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    read = driver.query_resolving("true")
    assert read.drained.tapped == ["Don't Allow"]
    assert read.drained.tapped_surfaces == ["native"]
    assert driver.system_alert_buttons == []
    assert not read.springboard_checked
    assert driver.drain_interruptions().tapped == ["Don't Allow"]
    assert driver.drain_interruptions().tapped == []


def test_an_in_tree_mode_read_leaves_springboard_alone() -> None:
    driver = _driver()
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    read = driver.query_resolving("inTree")
    assert read.drained.tapped == []
    assert driver.system_alert_buttons != []


def test_a_clear_screen_reads_springboard_checked() -> None:
    assert _driver().query_resolving("true").springboard_checked


def test_an_alert_no_rule_identifies_is_recorded_unidentified_only_when_governed() -> None:
    governed, ungoverned = _driver(), _driver(governs=False)
    for driver in (governed, ungoverned):
        driver.system_alert_buttons = [_button("Save"), _button("Cancel")]
    assert governed.query_resolving("true").drained.unidentified == [["Save", "Cancel"]]
    assert ungoverned.query_resolving("true").drained.unidentified == []


def test_an_in_tree_prompt_is_tapped_and_the_tree_is_re_read() -> None:
    sheet = [_button("Save Password"), _button("Not Now")]

    def react(driver: object, action: str, arg: object) -> None:
        assert isinstance(driver, ResolvingFakeDriver)
        if action == "tap":
            driver.screen = [el("home", "Home")]

    driver = ResolvingFakeDriver(screen=sheet, react=react)
    driver.set_interruption_policy([], True, in_tree_rules=[_SAVE_PASSWORD])
    read = driver.query_resolving("inTree")
    assert read.drained.tapped == ["Not Now"]
    assert read.drained.tapped_surfaces == ["inTree"]
    assert [e["identifier"] for e in read.elements] == ["home"]


def test_a_queued_read_wins_and_its_records_reach_the_drain() -> None:
    driver = _driver()
    queued = base.ResolvingRead(
        elements=[],
        drained=base.DrainedInterruptions(
            tapped=[],
            declined=[],
            banners=[],
            gave_up=[base.GaveUpAlert(["Allow"], "native", "Allow")],
        ),
        withheld=True,
    )
    driver.resolving_reads.append(queued)
    assert driver.query_resolving("true", force_check=True) is queued
    assert ("query_resolving", ("true", True)) in driver.actions
    assert len(driver.drain_interruptions().gave_up) == 1


def test_resolve_system_alert_answers_only_the_label_set_the_step_read() -> None:
    driver = _driver()
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    assert driver.resolve_system_alert(["Allow Paste"]).status == "changed"
    assert driver.resolve_system_alert(["Don't Allow", "Allow"]).status == "answered"
    assert driver.resolve_system_alert(["Allow"]).status == "absent"


def test_resolve_system_alert_reports_an_alert_no_rule_identifies() -> None:
    driver = _driver()
    driver.system_alert_buttons = [_button("Save"), _button("Cancel")]
    resolution = driver.resolve_system_alert(["Save", "Cancel"])
    assert resolution.status == "unidentified"
    assert resolution.drained.unidentified == [["Save", "Cancel"]]


def test_a_policy_push_clears_the_pending_records() -> None:
    driver = _driver()
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    driver.query_resolving("true")
    driver.set_interruption_policy([_NOTIFICATIONS], True)
    assert driver.drain_interruptions().tapped == []
