"""Capture the real-alert fixture set the guard's real-model verification replays (BE-0308).

`test_real_model_alerts.py` asks whether a real vision call lands on the correct dismiss control.
That question is only as real as the dialogs it asks it about, so the fixtures are captured here
from genuine dialogs on a real device: the showcase app's own OS prompts, and — as the case that
distinguishes "found *a* button" from "found the *correct* button" — its own destructive dialog,
where every button is the wrong answer and one of them deletes.

Ground truth is read from the device, never measured by eye: each button's frame comes from the same
accessibility query the deterministic `handleSystemAlert` step resolves against (BE-0316), and the
app-owned dialog's from the app's own element tree. The screenshot is taken through
`screenshot_bytes` — the guard's own capture path, downscale included — so what the fixture replays
is byte-identical to what the guard would have seen.

Run by hand against a booted Simulator with the showcase SwiftUI app and the runner built
(`make -C demos/showcase swiftui-build runner-build`), then review and commit what it wrote:

    BAJUTSU_ALERT_FIXTURE_UDID=<udid> uv run pytest tests/test_alert_fixtures_ondevice.py -m ondevice -n0

Deliberately *not* wired into `ios-e2e.yml`: capturing writes committed artifacts, which is a human
decision, and the fixtures it produces are replayed on every gate without it (the same posture as
BE-0295's key-gated capture). It is therefore absent from `scripts/e2e_changes.py`'s iOS lane too —
no CI job runs this file, so triggering the lane on it would signal work nothing performs.

Order matters and the cases share one lease: a notification or location prompt appears only while
its authorization is `notDetermined`, so each is captured before anything answers it. The paste
consent is asked every time (BE-0369), and the app's own dialog depends on no permission state.

No LLM anywhere: this module only captures pixels and frames. The verdict on those fixtures is a
frame containment check (prime directive 1), and every wait here is a condition wait (directive 2).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alert_fixture_support import Control, save_fixture
from xcuitest_lease import xcuitest_lease_launch

from bajutsu import simctl
from bajutsu.common.drivers import base
from bajutsu.common.drivers.xcuitest import XcuitestDriver
from bajutsu.common.scenario.system_alerts import (
    SystemAlertChoice,
    SystemAlertPrompt,
    system_alert_label,
)
from bajutsu.config import Effective, load_config, resolve
from bajutsu.screenshots import screenshot_bytes

pytestmark = pytest.mark.ondevice

_udid = os.environ.get("BAJUTSU_ALERT_FIXTURE_UDID")
if not _udid:
    pytest.skip(
        "capturing the BE-0308 alert fixtures needs BAJUTSU_ALERT_FIXTURE_UDID (a booted Simulator "
        "with the showcase SwiftUI app and the runner built) — it is a manual, local step",
        allow_module_level=True,
    )
UDID: str = _udid  # narrowed by the skip above

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-swiftui"  # the a11y app: its identifiers surface for XCUITest
_LAUNCH_ENV = {"SHOWCASE_UITEST": "1"}

# Generous but bounded: a cold Simulator can take a while to draw a tab, and a SpringBoard prompt
# appears only once the app's request reaches the OS. Every wait below returns the instant its
# condition holds, so these are ceilings, not delays.
_TIMEOUT = 20.0
_ALERT_TIMEOUT = 20.0

# How many bounded scrolls may be spent bringing a below-the-fold control into reach before the
# capture gives up and says so, rather than tapping at a coordinate nothing is under.
_MAX_REVEALS = 6


def _effective() -> Effective:
    # Rebase against the config's own directory (as `test_driver_conformance_ondevice.py` does), so
    # the relative appPath / testRunner resolve from where they point rather than from the cwd.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def driver() -> Iterator[base.Driver]:
    """One cold-launched XCUITest lease shared by every capture, torn down at the end."""
    eff = _effective()
    launched, teardown = xcuitest_lease_launch(UDID, eff, extra_env=_LAUNCH_ENV)()
    try:
        yield launched
    finally:
        teardown()


# --- Reading the device's own ground truth --------------------------------------------------------


def _alert_controls(driver: base.Driver) -> list[Control]:
    """The SpringBoard alert's buttons with their frames, or [] when no prompt is up.

    `Driver.system_alert_labels` reports the labels but not the frames, and the frames are the whole
    point here — the guard's answer is a coordinate. Rather than push a capture-only read onto all
    six `Driver` implementations, this reaches BE-0316's own `/systemAlert/query` route through the
    XCUITest driver directly; it is the same single read `handle_system_alert` polls, and this module
    never runs anywhere but a local capture.
    """
    assert isinstance(driver, XcuitestDriver), "the alert fixtures are captured on XCUITest only"
    buttons, _handles = driver._parse_elements(driver._transport("POST", "/systemAlert/query", {}))
    return [
        Control(label=label, frame=_frame_of(el)) for el in buttons if (label := el["label"] or "")
    ]


def _frame_of(element: base.Element) -> tuple[float, float, float, float]:
    x, y, w, h = element["frame"]
    return (float(x), float(y), float(w), float(h))


def _screen(driver: base.Driver) -> tuple[float, float]:
    """The device's real screen bounds — the space the captured frames are in.

    `screen_size_from_elements` is the wrong source and measurably so: on this app it reports
    (418, 2456) against a real 402x874 screen, because a SwiftUI `Form` keeps buffered off-screen
    rows in the tree. BE-0326 established `ViewportProvider` as the answer for exactly that reason,
    so the fixture records it, and a backend that cannot report one is refused rather than guessed at.
    """
    assert isinstance(driver, base.ViewportProvider), (
        "capturing needs a backend that reports its true viewport (BE-0326)"
    )
    width, height = driver.viewport()
    return (float(width), float(height))


def _await_alert(driver: base.Driver) -> list[Control]:
    """Condition-wait for a SpringBoard prompt, returning its buttons."""
    for _ in base.deadline_ticks(_ALERT_TIMEOUT, 0.2):
        controls = _alert_controls(driver)
        if controls:
            return controls
    raise AssertionError(f"no system alert appeared within {_ALERT_TIMEOUT}s")


def _await(driver: base.Driver, sel: base.Selector) -> None:
    assert base.wait_until(driver, sel, _TIMEOUT), f"never appeared within {_TIMEOUT}s: {sel!r}"


def _reveal(driver: base.Driver, sel: base.Selector) -> None:
    """Bring `sel` into reach with bounded scrolls, stopping the moment it is tappable.

    The scenario DSL's `scroll` step owns this loop for a run; a capture harness drives the driver
    directly, so it re-derives the same bounded, re-querying shape here rather than swiping a tuned
    fixed distance.
    """
    _await(driver, sel)
    width, height = _screen(driver)
    for _ in range(_MAX_REVEALS):
        if driver.is_tappable(sel):
            return
        driver.scroll((width / 2, height * 0.7), (width / 2, height * 0.3))
    # Check once more after the last allowed scroll: a control that scroll brought into reach is
    # reachable, and reporting it as unreachable would send the operator hunting a device fault.
    if driver.is_tappable(sel):
        return
    raise AssertionError(f"{sel!r} never came within reach after {_MAX_REVEALS} scrolls")


_DENY: SystemAlertChoice = "deny"


def _dismiss_label(prompt: SystemAlertPrompt, locale: str) -> str:
    """The label SpringBoard renders for *prompt*'s refusal under *locale* (BE-0320's table)."""
    return system_alert_label(prompt, _DENY, locale)


# --- Capture ------------------------------------------------------------------------------------


def _capture(
    name: str,
    driver: base.Driver,
    *,
    controls: list[Control],
    dismiss_label: str | None,
    note: str,
) -> None:
    """Save one dialog with its ground truth, after checking the dialog is the one expected.

    `dismiss_label` names the control the guard is required to tap, or None for a dialog it must
    leave alone. A label absent from what the device actually reported is a capture failure, not a
    fixture: it means the prompt, the locale, or the OS moved, and committing it would pin the
    verification to a button that is not there.
    """
    labels = [c.label for c in controls]
    if dismiss_label is not None and dismiss_label not in labels:
        raise AssertionError(
            f"{name}: expected a {dismiss_label!r} button on this dialog, but the device reported "
            f"{labels!r} — re-read the prompt's labels rather than committing this capture"
        )
    png = screenshot_bytes(driver)
    assert png, f"{name}: the screenshot came back empty"
    dismiss = next((c for c in controls if c.label == dismiss_label), None)
    path = save_fixture(
        name,
        png,
        screen=_screen(driver),
        dismiss=dismiss,
        others=tuple(c for c in controls if c is not dismiss),
        note=note,
    )
    print(f"captured {name} -> {path} (buttons: {labels})")  # the capture's whole visible output


def _open_permissions(driver: base.Driver) -> None:
    driver.tap({"label": "Permissions", "traits": ["button"]})
    # The tab transition is asynchronous, so wait for the tab's own first control before anything
    # else touches the screen (the same wait permission_system_alert.yaml holds, for the same reason).
    _await(driver, {"id": "perm.requestNotif"})


def test_capture_notification_permission(driver: base.Driver) -> None:
    locale = _effective().locale
    _open_permissions(driver)
    driver.tap({"id": "perm.requestNotif"})
    controls = _await_alert(driver)
    deny = _dismiss_label("notifications", locale)
    _capture(
        "notif_permission",
        driver,
        controls=controls,
        dismiss_label=deny,
        note=(
            "the OS notification-authorization prompt; the grant button sits beside the refusal, so "
            "a wrong answer grants a permission the run never asked for"
        ),
    )
    driver.handle_system_alert({"label": deny}, _ALERT_TIMEOUT)


def test_capture_location_permission(driver: base.Driver) -> None:
    _open_permissions(driver)
    driver.tap({"id": "perm.requestLocation"})
    controls = _await_alert(driver)
    # BE-0320's table deliberately covers only the prompts a `permissions` preset cannot pre-answer,
    # and location is one it can (BE-0276) — so this prompt has no entry and the label is named here.
    # `_capture` refuses the fixture unless the device really reported it, so a locale or OS change
    # fails the capture rather than mislabeling the ground truth.
    deny = "Don’t Allow"
    _capture(
        "location_permission",
        driver,
        controls=controls,
        dismiss_label=deny,
        note=(
            "the OS location prompt: three stacked choices, of which only the bottom one refuses — "
            "the sharpest test of whether a real answer lands on the correct button or merely a button"
        ),
    )
    driver.handle_system_alert({"label": deny}, _ALERT_TIMEOUT)


def test_capture_paste_consent(driver: base.Driver) -> None:
    locale = _effective().locale
    # Seeded from outside the app, so the app's own read raises iOS's cross-process consent prompt
    # (BE-0369); a value the app wrote itself reads back silently and raises nothing.
    simctl.Env(UDID).set_clipboard("bajutsu-be0308-clip")
    _open_permissions(driver)
    _reveal(driver, {"id": "sys.paste"})
    driver.tap({"id": "sys.paste"})
    controls = _await_alert(driver)
    deny = _dismiss_label("paste", locale)
    _capture(
        "paste_consent",
        driver,
        controls=controls,
        dismiss_label=deny,
        note=(
            "iOS's cross-process paste-consent prompt; its refusal spells a typographic apostrophe, "
            "so the answer must come from looking at the dialog rather than from transcribing it"
        ),
    )
    driver.handle_system_alert({"label": deny}, _ALERT_TIMEOUT)


def test_capture_app_destructive_dialog(driver: base.Driver) -> None:
    driver.tap({"label": "Log", "traits": ["button"]})
    _reveal(driver, {"id": "log.openDelete"})
    driver.tap({"id": "log.openDelete"})
    _await(driver, {"id": "log.dialog.delete"})
    tree = {el["identifier"]: el for el in driver.query() if el["identifier"]}
    controls = [
        Control(label=label, frame=_frame_of(tree[identifier]))
        for identifier, label in (
            ("log.dialog.archive", "Archive"),
            ("log.dialog.delete", "Delete"),
            ("log.dialog.cancel", "Cancel"),
        )
        if identifier in tree
    ]
    assert len(controls) == 3, f"expected the dialog's three buttons, found {controls!r}"
    _capture(
        "app_destructive_dialog",
        driver,
        controls=controls,
        dismiss_label=None,
        note=(
            "the app's own delete-confirmation dialog, not an OS prompt: the guard must report it "
            "absent and leave it alone, because the button it would reach for deletes"
        ),
    )
    driver.tap({"id": "log.dialog.cancel"})
