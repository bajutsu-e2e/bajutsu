"""Action execution: tap / type / swipe / gestures / relaunch / device control / http.

`wait` and `assert` are conditions handled by the run loop; everything here is a one-shot
effect on the device.
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.orchestrator.types import DeviceControl, RelaunchFn
from bajutsu.scenario import HttpRequest, Step

_SWIPE_DIST = 100.0


def _action_of(step: Step) -> str:
    for a in (
        "tap",
        "double_tap",
        "long_press",
        "type",
        "swipe",
        "pinch",
        "rotate",
        "wait",
        "assert_",
        "relaunch",
        "set_location",
        "push",
        "http",
        "clear_keychain",
        "clear_clipboard",
        "background",
        "override_status_bar",
        "clear_status_bar",
        "if_",
        "for_each",
    ):
        if getattr(step, a) is not None:
            return a
    raise AssertionError("no valid action on step (guaranteed by scenario validation)")


def _selector_hint(obj: object) -> str:
    """A short target string for a progress label — the first id/label found on an action object
    or its nested selector (e.g. `type`'s `into`, `swipe`'s `on`). Empty when nothing identifies
    it. Never returns typed text (kept out of progress so secrets don't leak)."""
    for attr in ("id", "label", "id_matches", "label_matches"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    for attr in ("into", "on", "sel", "of", "within"):
        nested = getattr(obj, attr, None)
        if nested is not None:
            hint = _selector_hint(nested)
            if hint:
                return hint
    return ""


def _step_label(step: Step, kind: str) -> str:
    """A concise description of a step for progress output: the step's own `name` if set,
    otherwise the action kind plus its target id/label (e.g. "tap home.title")."""
    if step.name:
        return step.name
    hint = _selector_hint(getattr(step, kind))
    pretty = kind.rstrip("_").replace("_", " ")
    return f"{pretty} {hint}".strip()


def _center(frame: base.Frame) -> base.Point:
    x, y, w, h = frame
    return (x + w / 2, y + h / 2)


def _target(center: base.Point, direction: str) -> base.Point:
    cx, cy = center
    if direction == "up":
        return (cx, cy - _SWIPE_DIST)
    if direction == "down":
        return (cx, cy + _SWIPE_DIST)
    if direction == "left":
        return (cx - _SWIPE_DIST, cy)
    return (cx + _SWIPE_DIST, cy)  # right


def _require_multi_touch(driver: base.Driver, action: str) -> None:
    """Fail clearly before a two-finger gesture if the actuator can't do multi-touch
    (e.g. idb), rather than emitting a single-touch approximation that silently passes."""
    if base.Capability.MULTI_TOUCH not in driver.capabilities():
        raise base.UnsupportedAction(
            f"{action} requires a multi-touch capable backend (idb supports single touch only; use codegen→XCUITest instead)"
        )


def _do_http(http: HttpRequest, bindings: dict[str, str] | None) -> None:
    """Execute an HTTP request and optionally save the response body to vars.*."""
    import urllib.error
    import urllib.request

    if not http.url.startswith(("http://", "https://")):
        raise base.SelectorError(f"http: only http/https URLs are allowed, got {http.url!r}")

    req = urllib.request.Request(
        http.url,
        data=http.body.encode("utf-8") if http.body else None,
        headers=dict(http.headers or {}),
        method=http.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        status = e.code
    except urllib.error.URLError as e:
        raise base.SelectorError(f"http: request failed: {e.reason}") from e
    if http.status is not None and status != http.status:
        raise base.SelectorError(f"http: expected status {http.status}, got {status}")
    if http.save_body is not None and bindings is not None:
        bindings[f"vars.{http.save_body}"] = body


def _do_action(
    driver: base.Driver,
    step: Step,
    relaunch: RelaunchFn | None = None,
    control: DeviceControl | None = None,
    bindings: dict[str, str] | None = None,
) -> None:
    """Run tap / longPress / type / swipe / relaunch / device control / http (wait and
    assert live in the run loop)."""
    if step.tap is not None:
        driver.tap(step.tap.as_selector())
        return
    if step.double_tap is not None:
        driver.double_tap(step.double_tap.as_selector())
        return
    if step.long_press is not None:
        driver.long_press(step.long_press.sel.as_selector(), step.long_press.duration)
        return
    if step.type is not None:
        if step.type.into is not None:
            driver.tap(step.type.into.as_selector())
        driver.type_text(step.type.text)
        return
    if step.swipe is not None:
        sw = step.swipe
        if sw.from_ is not None and sw.to is not None:
            driver.swipe(sw.from_, sw.to)
        elif sw.on is not None and sw.direction is not None:
            el = base.resolve_unique(driver.query(), sw.on.as_selector())
            center = _center(el["frame"])
            driver.swipe(center, _target(center, sw.direction))
        return
    if step.pinch is not None:
        _require_multi_touch(driver, "pinch")
        driver.pinch(step.pinch.sel.as_selector(), step.pinch.scale)
        return
    if step.rotate is not None:
        _require_multi_touch(driver, "rotate")
        driver.rotate(step.rotate.sel.as_selector(), step.rotate.radians)
        return
    if step.relaunch is not None:
        if relaunch is None:
            raise base.UnsupportedAction(
                "relaunch requires a real device environment (not supported on fake driver)"
            )
        relaunch(step.relaunch)
        return
    if step.set_location is not None:
        if control is None:
            raise base.UnsupportedAction(
                "setLocation requires a real device environment (not supported on fake driver)"
            )
        control.set_location(step.set_location.lat, step.set_location.lon)
        return
    if step.push is not None:
        if control is None:
            raise base.UnsupportedAction(
                "push requires a real device environment (not supported on fake driver)"
            )
        control.push(step.push.payload)
        return
    if step.http is not None:
        _do_http(step.http, bindings)
        return
    if step.clear_keychain is not None:
        if control is None:
            raise base.UnsupportedAction(
                "clearKeychain requires a real device environment (not supported on fake driver)"
            )
        control.clear_keychain()
        return
    if step.clear_clipboard is not None:
        if control is None:
            raise base.UnsupportedAction(
                "clearClipboard requires a real device environment (not supported on fake driver)"
            )
        control.clear_clipboard()
        return
    if step.background is not None:
        if control is None:
            raise base.UnsupportedAction(
                "background requires a real device environment (not supported on fake driver)"
            )
        control.home()
        return
    if step.override_status_bar is not None:
        if control is None:
            raise base.UnsupportedAction(
                "overrideStatusBar requires a real device environment (not supported on fake driver)"
            )
        osb = step.override_status_bar
        kwargs: dict[str, str | int] = {}
        if osb.time is not None:
            kwargs["time"] = osb.time
        if osb.battery_level is not None:
            kwargs["battery_level"] = osb.battery_level
        if osb.battery_state is not None:
            kwargs["battery_state"] = osb.battery_state
        if osb.cellular_bars is not None:
            kwargs["cellular_bars"] = osb.cellular_bars
        if osb.wifi_bars is not None:
            kwargs["wifi_bars"] = osb.wifi_bars
        control.override_status_bar(**kwargs)
        return
    if step.clear_status_bar is not None:
        if control is None:
            raise base.UnsupportedAction(
                "clearStatusBar requires a real device environment (not supported on fake driver)"
            )
        control.clear_status_bar()
        return
    raise AssertionError("unhandled action")
