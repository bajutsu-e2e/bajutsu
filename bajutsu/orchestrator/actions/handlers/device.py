"""Device-environment handlers (simctl-backed): relaunch, setLocation, push, clearKeychain,
clearClipboard, setClipboard, background, foreground, overrideStatusBar, clearStatusBar. Each needs
the injected device control (or relauncher); without one — e.g. the fake driver — it fails clearly."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.orchestrator.actions._registry import _handler, _need_control
from bajutsu.orchestrator.types import DeviceControl, RelaunchFn
from bajutsu.scenario import Step


@_handler("relaunch")
def _do_relaunch(
    _d: object, step: Step, relaunch: RelaunchFn | None, _c: object, _b: object
) -> None:
    assert step.relaunch is not None
    if relaunch is None:
        raise base.UnsupportedAction(
            "relaunch requires a real device environment (not supported on fake driver)"
        )
    relaunch(step.relaunch)


@_handler("set_location")
def _do_set_location(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    assert step.set_location is not None
    _need_control(control, "setLocation").set_location(step.set_location.lat, step.set_location.lon)


@_handler("push")
def _do_push(_d: object, step: Step, _r: object, control: DeviceControl | None, _b: object) -> None:
    assert step.push is not None
    _need_control(control, "push").push(step.push.payload)


@_handler("clear_keychain")
def _do_clear_keychain(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    _need_control(control, "clearKeychain").clear_keychain()


@_handler("clear_clipboard")
def _do_clear_clipboard(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    _need_control(control, "clearClipboard").clear_clipboard()


@_handler("set_clipboard")
def _do_set_clipboard(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    assert step.set_clipboard is not None
    _need_control(control, "setClipboard").set_clipboard(step.set_clipboard.text)


@_handler("background")
def _do_background(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    _need_control(control, "background").home()


@_handler("foreground")
def _do_foreground(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    _need_control(control, "foreground").foreground()


@_handler("override_status_bar")
def _do_override_status_bar(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    assert step.override_status_bar is not None
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
    _need_control(control, "overrideStatusBar").override_status_bar(**kwargs)


@_handler("clear_status_bar")
def _do_clear_status_bar(
    _d: object, step: Step, _r: object, control: DeviceControl | None, _b: object
) -> None:
    _need_control(control, "clearStatusBar").clear_status_bar()
