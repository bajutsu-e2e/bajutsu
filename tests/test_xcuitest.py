"""Tests for the XCUITest backend's Python channel client (BE-0019 Slice 2).

The driver actuates over a loopback HTTP channel to a resident XCTest runner. The runner itself is a
later, on-device slice; here the request/response logic is exercised against an injected fake
transport (mirroring how the adb driver injects a fake `run`), so nothing on the gate needs a
Simulator. Resolution stays Python-side, so the key property is that the driver acts on **exactly**
the element it resolved, addressed by that element's per-snapshot handle.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.xcuitest import (
    _ACTUATION_TIMEOUT_SECONDS,
    _MAX_ATTEMPTS,
    _RECOVERY_TIMEOUT_SECONDS,
    _SOCKET_TIMEOUT_SECONDS,
    _STALE_MAX_ATTEMPTS,
    TransportFn,
    XcuitestChannelError,
    XcuitestDriver,
    XcuitestRunnerCrashError,
    _await_health,
    _decode,
    _HealthWait,
    _is_retry_eligible,
    _raw_http_transport,
    _Reply,
    _timeout_for,
    _TransportFailure,
    _with_crash_recovery,
    _with_retry,
)


def _el_wire(
    handle: str,
    identifier: str | None = None,
    label: str | None = None,
    value: str | None = None,
    traits: list[str] | None = None,
    frame: tuple[float, float, float, float] = (0.0, 0.0, 10.0, 10.0),
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": traits or [],
        "frame": list(frame),
        "handle": handle,
    }


def _elements(*els: dict[str, Any]) -> _Reply:
    return _Reply(status="ok", elements=list(els))


def _driver(transport: TransportFn) -> XcuitestDriver:
    # No-op sleep so the BE-0289 stale re-resolution backoff adds no wall time on the gate.
    return XcuitestDriver(transport=transport, sleep=lambda _s: None)


def test_driver_satisfies_the_protocol() -> None:
    assert isinstance(_driver(lambda m, p, b: _Reply(status="ok")), base.Driver)


def test_query_parses_elements_and_does_not_leak_the_handle() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        assert (method, path) == ("GET", "/elements")
        return _elements(
            _el_wire("h-title", "home.title", "Home"),
            _el_wire("h-ok", "ok", "OK", traits=["button"]),
        )

    els = _driver(transport).query()
    assert els == [
        {
            "identifier": "home.title",
            "label": "Home",
            "value": None,
            "traits": [],
            "frame": (0.0, 0.0, 10.0, 10.0),
            "nativeZ": None,
        },
        {
            "identifier": "ok",
            "label": "OK",
            "value": None,
            "traits": ["button"],
            "frame": (0.0, 0.0, 10.0, 10.0),
            "nativeZ": None,
        },
    ]
    assert all("handle" not in el for el in els)  # the handle is not a selector/Element field


def test_last_raw_source_is_none_before_the_first_query() -> None:
    assert _driver(lambda m, p, b: _Reply(status="ok")).last_raw_source() is None


def test_query_records_the_raw_elements_body() -> None:
    body = json.dumps({"status": "ok", "elements": [_el_wire("h-ok", "ok", "OK")]}).encode()

    def transport(method: str, path: str, b: Mapping[str, Any] | None) -> _Reply:
        return _decode(path, 200, body)

    driver = _driver(transport)
    driver.query()
    raw = driver.last_raw_source()
    assert raw is not None
    assert raw.text == body.decode("utf-8")
    assert raw.parsed_input is None  # nothing narrows the runner's own reply
    assert raw.suffix == ".json"  # undecoded GET /elements body, not adb's XML dump


def test_tap_resolves_unique_then_sends_that_elements_snapshot_handle() -> None:
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _el_wire("h-title", "home.title", "Home"),
                _el_wire("h-ok", "ok", "OK", traits=["button"]),
            )
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).tap({"id": "ok"})
    assert sent == [
        ("POST", "/tap", {"handle": "h-ok"})
    ]  # the resolved element's handle, not coords


def test_the_actuation_record_states_the_handle_and_no_coordinate() -> None:
    # This backend never computes a touch point: the runner picks it on the far side of the handle. So
    # the record names the element it resolved and leaves `points` empty rather than writing the frame's
    # centre, which would present a guess as a measurement.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"], frame=(4, 8, 20, 12)))
        return _Reply(status="ok")

    driver = _driver(transport)
    driver.long_press({"id": "ok"}, 0.7)
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via, record.unit) == ("longPress", "handle", "point")
    assert record.points == ()
    assert (record.frame, record.target, record.duration_s) == ((4.0, 8.0, 20.0, 12.0), "ok", 0.7)


def test_a_stale_retry_records_both_attempts_and_ends_on_the_actuated_element() -> None:
    # A `stale` reply re-resolves and re-actuates (BE-0289). Both attempts really went out, so both are
    # recorded — and the last record names the element the successful attempt resolved, which is the one
    # that was actuated.
    frames = [(0.0, 0.0, 10.0, 10.0), (0.0, 40.0, 10.0, 10.0)]  # the row moved between snapshots
    replies = ["stale", "ok"]
    snapshots = 0

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        nonlocal snapshots
        if path == "/elements":
            frame = frames[min(snapshots, len(frames) - 1)]
            snapshots += 1
            return _elements(_el_wire("h-ok", "ok", "OK", frame=frame))
        return _Reply(status=replies.pop(0))

    driver = _driver(transport)
    driver.tap({"id": "ok"})
    first, second = driver.drain_actuations().records
    assert first.frame == (0.0, 0.0, 10.0, 10.0)
    assert second.frame == (0.0, 40.0, 10.0, 10.0)
    # Without the accepted stamp the two would be indistinguishable and the report would show one tap
    # as two — the refused attempt has to say it was refused.
    assert (first.accepted, second.accepted) == (False, True)


def test_a_coordinate_primitive_records_the_points_it_sent() -> None:
    # `swipe` / `scroll` / `tap_point` bypass the handle channel and send raw coordinates, so unlike
    # every other primitive here they do state a point — the one that crossed to the runner.
    driver = _driver(lambda m, p, b: _Reply(status="ok"))
    driver.swipe((10.0, 200.0), (10.0, 40.0))
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via) == ("swipe", "coordinate")
    assert record.points == ((10.0, 200.0), (10.0, 40.0))


def test_tap_resolves_through_a_content_identical_duplicate_registration() -> None:
    # A standard UIAlertController button sometimes registers twice on XCUITest — same identifier,
    # label, traits, value, and frame, on both entries. Without `resolve_unique`'s collapsing this
    # selector would raise AmbiguousSelector and force an `index` guess; with it, `tap` resolves
    # and actuates without one.
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _el_wire("h-ok-1", "alert.ok", "OK", traits=["button"]),
                _el_wire("h-ok-2", "alert.ok", "OK", traits=["button"]),
            )
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).tap({"id": "alert.ok"})
    assert sent == [("POST", "/tap", {"handle": "h-ok-1"})]  # the first duplicate's own handle


def test_back_taps_the_os_back_button() -> None:
    # iOS has no hardware back: `back` resolves and taps the OS navigation back button
    # (identifier "BackButton") — BE-0210.
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-back", "BackButton", "Back", traits=["button"]))
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).back()
    assert sent == [("POST", "/tap", {"handle": "h-back"})]


def test_scroll_posts_to_the_non_inertial_scroll_route() -> None:
    # A directional scroll on iOS is non-inertial (BE-0326): it posts to the resident runner's
    # dedicated `/scroll`, which holds the drag at its end before lifting so the scroll view settles
    # where the gesture left it — distinct from `/swipe`, whose drag lifts with residual velocity.
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        sent.append((method, path, body))
        return _Reply(status="ok")

    _driver(transport).scroll((10.0, 20.0), (30.0, 40.0))
    assert sent == [("POST", "/scroll", {"from": [10.0, 20.0], "to": [30.0, 40.0]})]


def test_viewport_reads_the_screen_route_and_caches() -> None:
    # The `scroll` viewport is the runner's real screen size (BE-0326), fetched once via GET /screen —
    # the flattened tree excludes the app window and buffers off-screen ScrollView children, so it
    # can't supply the viewport.
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        sent.append((method, path, body))
        return _Reply(status="ok", size=(390.0, 844.0))

    driver = _driver(transport)
    assert driver.viewport() == (390.0, 844.0)
    assert driver.viewport() == (390.0, 844.0)  # cached
    assert sent == [("GET", "/screen", None)]


def test_decode_parses_screen_size() -> None:
    # The wire decoder lifts width/height into `_Reply.size` for GET /screen, None elsewhere (BE-0326).
    assert _decode("/screen", 200, b'{"status":"ok","width":390,"height":844}').size == (
        390.0,
        844.0,
    )
    assert _decode("/elements", 200, b'{"status":"ok","elements":[]}').size is None


def test_pinch_and_rotate_emit_gesture_requests_with_the_handle() -> None:
    sent: list[tuple[str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-img", "photo", "Photo"))
        sent.append((path, body))
        return _Reply(status="ok")

    d = _driver(transport)
    d.pinch({"id": "photo"}, 2.0)
    d.rotate({"id": "photo"}, 1.57)
    assert sent == [
        ("/gesture", {"handle": "h-img", "kind": "pinch", "scale": 2.0}),
        ("/gesture", {"handle": "h-img", "kind": "rotate", "radians": 1.57}),
    ]


def test_text_editing_requests_carry_the_action_payload() -> None:
    # delete/select/copy each POST to their own endpoint; the runner types the native key or key
    # chord on the focused field (BE-0265). Focus is a prior tap the orchestrator issues.
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        sent.append((method, path, body))
        return _Reply(status="ok")

    d = _driver(transport)
    d.delete_text(3)
    d.select_all()
    d.copy_selection()
    assert sent == [
        ("POST", "/deleteText", {"count": 3}),
        ("POST", "/selectAll", {}),
        ("POST", "/copy", {}),
    ]


def test_text_editing_raises_on_a_non_ok_reply() -> None:
    # A failed actuation is loud on every text-editing endpoint, never a silent no-op (determinism
    # first) — the three share one guard shape, so pin all three.
    d = _driver(lambda m, p, b: _Reply(status="error"))
    with pytest.raises(XcuitestChannelError):
        d.delete_text(1)
    with pytest.raises(XcuitestChannelError):
        d.select_all()
    with pytest.raises(XcuitestChannelError):
        d.copy_selection()


def test_select_option_unsupported() -> None:
    # <select> is a web control with no iOS-native counterpart, so the backend refuses (BE-0191).
    d = _driver(lambda m, p, b: _Reply(status="ok"))
    with pytest.raises(base.UnsupportedAction):
        d.select_option({"id": "nav.theme-picker"}, "midnight")


def test_capabilities_add_semantic_tap_condition_wait_multi_touch_but_not_network() -> None:
    caps = _driver(lambda m, p, b: _Reply(status="ok")).capabilities()
    assert base.Capability.SEMANTIC_TAP in caps
    assert base.Capability.CONDITION_WAIT in caps
    assert base.Capability.MULTI_TOUCH in caps  # two-finger gestures
    assert base.Capability.TEXT_SELECTION in caps  # select/copy actuate (BE-0280)
    # Network evidence rides on the app-side collector, not the actuator (proposal: BE-0020 boundary).
    assert base.Capability.NETWORK not in caps


# --- BE-0289: a stale handle re-resolves before failing ---------------------------------------------
# A `stale` reply means the screen re-snapshotted between resolve and actuate, so the handle went
# stale while the element is still present. The driver re-queries and re-actuates while the selector
# still resolves uniquely, and fails loudly the moment it does not — tolerating a snapshot race
# without ever absorbing a real disappearance. The fake transport is scripted per call so each of the
# four cases pins one half of that honest gate; `_driver` injects a no-op sleep, so the backoff is free.


def test_stale_handle_re_resolves_and_recovers_when_the_selector_still_resolves() -> None:
    # `stale` once, then `ok`: the button is present the whole time (a launch-time snapshot race), so
    # the re-resolved unique match re-actuates and the tap succeeds.
    actuations: list[Mapping[str, Any] | None] = []
    replies = iter(["stale", "ok"])

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"]))
        actuations.append(body)
        return _Reply(status=next(replies))

    _driver(transport).tap({"id": "ok"})
    assert actuations == [{"handle": "h-ok"}, {"handle": "h-ok"}]  # re-actuated after re-resolving


def test_persistent_stale_exhausts_the_bound_then_fails_loudly() -> None:
    # The selector keeps resolving uniquely but every actuation is `stale`: the bound is spent and the
    # driver fails with the vanished-element error rather than retrying forever.
    actuations = 0

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        nonlocal actuations
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        actuations += 1
        return _Reply(status="stale")

    with pytest.raises(base.ElementNotFound, match="stale handle"):
        _driver(transport).tap({"id": "ok"})
    assert actuations == _STALE_MAX_ATTEMPTS  # bounded, not unbounded


def test_stale_then_gone_fails_immediately_as_element_not_found() -> None:
    # A `stale` whose re-query no longer resolves the selector is a genuine disappearance: fail at once
    # as ElementNotFound, spending no further actuation attempt (never absorb a real vanish, BE-0049).
    actuations = 0
    present = iter([True, False])  # resolves once (the first actuate), gone on the re-query

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        nonlocal actuations
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK")) if next(present) else _elements()
        actuations += 1
        return _Reply(status="stale")

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "ok"})
    assert actuations == 1  # the re-query found nothing, so no second actuation was issued


def test_stale_then_ambiguous_fails_immediately_and_never_re_actuates() -> None:
    # A `stale` whose re-query resolves to many elements fails as AmbiguousSelector — the gate never
    # taps whatever happens to match (determinism first).
    actuations = 0
    first = iter([True, False])  # unique on the first resolve, ambiguous on the re-query

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        nonlocal actuations
        if path == "/elements":
            if next(first):
                return _elements(_el_wire("h1", "dup", "A", traits=["button"]))
            return _elements(
                _el_wire("h1", "dup", "A", traits=["button"]),
                _el_wire("h2", "dup", "B", traits=["button"]),
            )
        actuations += 1
        return _Reply(status="stale")

    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).tap({"id": "dup"})
    assert actuations == 1  # ambiguity is loud; no second actuation was issued


def test_ambiguous_selector_fails_before_any_actuation_request() -> None:
    calls: list[str] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls.append(path)
        if path == "/elements":
            return _elements(
                _el_wire("h1", "dup", "A", traits=["button"]),
                _el_wire("h2", "dup", "B", traits=["button"]),
            )
        return _Reply(status="ok")

    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).tap({"id": "dup"})
    assert calls == ["/elements"]  # selection is Python-side; no /tap was ever sent


def test_missing_selector_fails_before_any_actuation_request() -> None:
    calls: list[str] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls.append(path)
        return _elements(_el_wire("h-ok", "ok", "OK"))

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "absent"})
    assert calls == ["/elements"]


def test_tap_raises_element_not_tappable_when_runner_reports_not_hittable() -> None:
    # Distinct from `stale`: the element resolved and the runner's own `isHittable` refused it
    # (covered by another element). No retry inside the driver — that is the orchestrator's job.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"]))
        return _Reply(status="not-hittable")

    with pytest.raises(base.ElementNotTappable, match="not hittable"):
        _driver(transport).tap({"id": "ok"})


# The measured iOS 18.6 shape around the showcase Stepper: the container's accessibility element is
# inflated to the whole form row and XCTest refuses it, while both of its named children are
# reachable. `log.count.label` sits *before* the container, so it is an ancestor/sibling, not an offer.
_STEPPER_ROW = (16.0, 268.0, 358.0, 44.0)


def _stepper_tree(*, decrement: str, increment: str) -> list[dict[str, Any]]:
    return [
        _el_wire("h-cell", None, None, frame=_STEPPER_ROW),
        _el_wire("h-label", "log.count.label", "Count: 1", frame=(32.0, 279.8, 62.7, 20.3)),
        _el_wire("h-count", "log.count", "Count: 1", traits=["other"], frame=_STEPPER_ROW),
        _el_wire("h-img", None, None, frame=(264.0, 274.0, 46.5, 32.0)),
        _el_wire(
            "h-dec", decrement, "Decrement", traits=["button"], frame=(264.0, 274.0, 46.5, 32.0)
        ),
        _el_wire(
            "h-inc", increment, "Increment", traits=["button"], frame=(311.5, 274.0, 46.5, 32.0)
        ),
    ]


def _stepper_transport(
    hittable: dict[str, str], sent: list[tuple[str, Mapping[str, Any] | None]]
) -> TransportFn:
    """A runner that refuses the container and answers `hittable` for each probed child handle."""

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                *_stepper_tree(decrement="log.count-Decrement", increment="log.count-Increment")
            )
        sent.append((path, body))
        handle = (body or {}).get("handle")
        if path == "/isHittable":
            return _Reply(status=hittable.get(str(handle), "not-hittable"))
        return _Reply(status="ok" if handle != "h-count" else "not-hittable")

    return transport


def test_a_refused_tap_goes_to_the_one_reachable_named_descendant() -> None:
    # The container is refused but exactly one child is reachable, so there is no choice to make.
    sent: list[tuple[str, Mapping[str, Any] | None]] = []
    driver = _driver(_stepper_transport({"h-inc": "ok"}, sent))

    driver.tap({"id": "log.count"})

    assert ("/tap", {"handle": "h-inc"}) in sent
    [record] = [a for a in driver.drain_actuations().records if a.target == "log.count-Increment"]
    assert record.substitution == "soleHittableDescendant"


def test_a_refused_tap_with_two_reachable_descendants_names_both_instead_of_choosing() -> None:
    # The real Stepper: both children are reachable, so `tap: {id: log.count}` has no single meaning.
    # Picking one would be the guess prime directive 2 forbids, so the failure names the pair.
    sent: list[tuple[str, Mapping[str, Any] | None]] = []
    driver = _driver(_stepper_transport({"h-inc": "ok", "h-dec": "ok"}, sent))

    with pytest.raises(base.ElementNotTappable) as exc:
        driver.tap({"id": "log.count"})

    assert "log.count-Increment" in str(exc.value) and "log.count-Decrement" in str(exc.value)
    assert not any(path == "/tap" and (b or {}).get("handle") != "h-count" for path, b in sent)


def test_a_refused_tap_with_no_reachable_descendant_keeps_the_original_failure() -> None:
    sent: list[tuple[str, Mapping[str, Any] | None]] = []
    driver = _driver(_stepper_transport({}, sent))

    with pytest.raises(base.ElementNotTappable, match="not hittable"):
        driver.tap({"id": "log.count"})


def test_a_refused_tap_on_a_container_with_no_named_descendant_re_raises_unchanged() -> None:
    # Nothing to offer, so the original message stands rather than growing a clause about candidates.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _el_wire("h-box", "box", "Box", traits=["other"], frame=(0.0, 0.0, 20.0, 20.0)),
                _el_wire("h-icon", None, None, frame=(5.0, 5.0, 5.0, 5.0)),
            )
        return _Reply(status="not-hittable")

    with pytest.raises(base.ElementNotTappable, match=r"^element resolved but not hittable"):
        _driver(transport).tap({"id": "box"})


def test_a_refused_tap_spends_no_probe_on_a_crowded_container() -> None:
    # Above the cap the container is a layout region, not a control with one actuatable child, and
    # each probe is a round trip — so it refuses without asking the runner about any of them.
    row = (0.0, 0.0, 100.0, 100.0)
    sent: list[str] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            kids = [
                _el_wire(
                    f"h-{i}",
                    f"kid.{i}",
                    f"K{i}",
                    traits=["button"],
                    frame=(float(i), 0.0, 5.0, 5.0),
                )
                for i in range(base.MAX_REDIRECT_CANDIDATES + 1)
            ]
            return _elements(_el_wire("h-box", "box", "Box", traits=["other"], frame=row), *kids)
        sent.append(path)
        return _Reply(status="not-hittable")

    with pytest.raises(base.ElementNotTappable):
        _driver(transport).tap({"id": "box"})

    assert "/isHittable" not in sent


def test_a_long_press_on_a_refused_container_is_not_redirected() -> None:
    # A long-press reaching a child is a different intent, not the same intent reaching its target.
    sent: list[tuple[str, Mapping[str, Any] | None]] = []
    driver = _driver(_stepper_transport({"h-inc": "ok"}, sent))

    with pytest.raises(base.ElementNotTappable, match=r"^element resolved but not hittable"):
        driver.long_press({"id": "log.count"}, 0.5)

    assert not any(path == "/isHittable" for path, _b in sent)


def test_is_tappable_returns_true_when_the_runner_reports_ok() -> None:
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"]))
        sent.append((method, path, body))
        return _Reply(status="ok")

    assert _driver(transport).is_tappable({"id": "ok"}) is True
    assert sent == [("POST", "/isHittable", {"handle": "h-ok"})]


def test_is_tappable_returns_false_when_the_runner_reports_not_hittable() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"]))
        return _Reply(status="not-hittable")

    assert _driver(transport).is_tappable({"id": "ok"}) is False


def test_is_tappable_returns_false_when_the_selector_does_not_resolve() -> None:
    calls: list[str] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls.append(path)
        return _elements(_el_wire("h-ok", "ok", "OK"))

    assert _driver(transport).is_tappable({"id": "absent"}) is False
    assert calls == ["/elements"]  # never reached /isHittable for an unresolved selector


def test_is_tappable_propagates_ambiguous_selector_rather_than_swallowing_it() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _el_wire("h1", "dup", "A", traits=["button"]),
                _el_wire("h2", "dup", "B", traits=["button"]),
            )
        return _Reply(status="ok")

    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).is_tappable({"id": "dup"})


def test_is_tappable_never_actuates() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK", traits=["button"]))
        assert method == "POST" and path == "/isHittable"  # never /tap
        return _Reply(status="ok")

    driver = _driver(transport)
    driver.is_tappable({"id": "ok"})
    assert driver.drain_actuations().records == []


def test_screenshot_writes_the_returned_png_bytes(tmp_path: Any) -> None:
    png = b"\x89PNG\r\n\x1a\nfake-bytes"
    out = tmp_path / "shot.png"
    _driver(lambda m, p, b: _Reply(status="ok", png=png)).screenshot(str(out))
    assert out.read_bytes() == png


def test_wait_until_polls_xcuitest_until_a_match_appears() -> None:
    # BE-0118: wait_for is single-shot; the shared wait_until owns the poll. It must keep
    # polling xcuitest past the empty snapshot until the element renders.
    snapshots = [[], [_el_wire("h-ok", "ok", "OK")]]  # first empty, then the element renders

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=snapshots.pop(0) if snapshots else [])

    assert base.wait_until(_driver(transport), {"id": "ok"}, timeout=1.0, poll=0) is True


def test_wait_for_is_single_shot() -> None:
    present = _driver(lambda m, p, b: _Reply(status="ok", elements=[_el_wire("h-ok", "ok", "OK")]))
    assert present.wait_for({"id": "ok"}) is True
    absent = _driver(lambda m, p, b: _Reply(status="ok", elements=[]))
    assert absent.wait_for({"id": "never"}) is False


def test_await_ready_returns_once_the_runner_health_is_ready() -> None:
    _driver(lambda m, p, b: _Reply(status="ready")).await_ready(timeout=1.0, poll=0.001)


def test_await_ready_times_out_loudly_when_the_runner_never_comes_up() -> None:
    with pytest.raises(XcuitestChannelError, match="did not come up"):
        _driver(lambda m, p, b: _Reply(status="starting")).await_ready(timeout=0.02, poll=0.001)


def test_health_ready_is_a_single_shot_probe_true_when_ready() -> None:
    # BE-0319 unit 3: one non-blocking probe (unlike await_ready's loop), so the cold-spawn liveness
    # wait owns the timing between probes.
    assert _driver(lambda m, p, b: _Reply(status="ready")).health_ready() is True


def test_health_ready_is_false_before_the_runner_is_up() -> None:
    assert _driver(lambda m, p, b: _Reply(status="starting")).health_ready() is False


def test_health_ready_swallows_a_transport_failure_as_not_ready() -> None:
    # A runner not yet accepting connections raises a transport failure; the single-shot probe reads
    # that as not-ready (never an error), so the caller keeps polling rather than aborting.
    def _refuse(m: str, p: str, b: Any) -> _Reply:
        raise _TransportFailure("refused", delivered=False)

    assert _driver(_refuse).health_ready() is False


def test_health_ready_probes_the_raw_transport_not_the_retried_one() -> None:
    # A regression this PR's own review caught: health_ready() must reuse the single-attempt probe
    # transport (`_probe_transport`, the same one the BE-0287 crash-recovery health poll uses), never
    # the BE-0207-retried `_transport` — routing a "single-shot" probe through the retry would silently
    # turn one call into up to _MAX_ATTEMPTS attempts with backoff (over a second) instead of one.
    driver = XcuitestDriver(host="127.0.0.1", port=1)  # a real transport, no injected fake
    assert driver._probe_transport is not driver._transport


def test_health_ready_returns_promptly_on_a_refused_connection() -> None:
    # The timing signature of the regression above: through the retried transport, a down runner
    # costs _BACKOFF_BASE_SECONDS * (1 + 2) = 1.5s of sleep before health_ready() returns; through the
    # raw single-attempt transport it returns as soon as the connection is refused. A generous bound
    # well under 1.5s catches a reintroduction of the bug without being timing-flaky.
    start = time.monotonic()
    # Port 1 is a privileged port nothing listens on locally — refused immediately, no hang.
    ready = XcuitestDriver(host="127.0.0.1", port=1).health_ready()
    elapsed = time.monotonic() - start
    assert ready is False
    assert elapsed < 1.0


def test_a_runner_crash_mid_action_fails_loudly_not_as_not_found() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        raise XcuitestChannelError("connection refused")  # runner exited mid-run

    with pytest.raises(XcuitestChannelError):
        _driver(transport).tap({"id": "ok"})


# --- the wire decode (pure; the only socket I/O, _http_transport, is the thin untested edge) --- #


def test_decode_elements_response_keeps_status_and_handles() -> None:
    body = json.dumps(
        {"status": "ok", "elements": [{"identifier": "ok", "handle": "h-ok"}]}
    ).encode()
    reply = _decode("/elements", 200, body)
    assert reply.status == "ok"
    assert reply.elements is not None and reply.elements[0]["handle"] == "h-ok"


def test_decode_keeps_the_undecoded_body_alongside_the_parsed_fields() -> None:
    body = json.dumps({"status": "ok", "elements": []}).encode()
    assert _decode("/elements", 200, body).raw == body


def test_decode_screenshot_returns_raw_png_bytes() -> None:
    reply = _decode("/screenshot", 200, b"\x89PNGraw")
    assert reply.png == b"\x89PNGraw"


def test_decode_non_200_carries_the_servers_status() -> None:
    reply = _decode("/tap", 404, json.dumps({"status": "not-found"}).encode())
    assert reply.status == "not-found"


def test_an_element_without_a_handle_is_a_loud_channel_error() -> None:
    # A malformed /elements item (no handle) must fail loudly, not be coerced to "" and sent back.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=[{"identifier": "ok", "frame": [0, 0, 1, 1]}])

    with pytest.raises(XcuitestChannelError, match="without a handle"):
        _driver(transport).query()


def test_a_runner_error_status_is_an_infra_failure_not_element_not_found() -> None:
    # A non-outcome status (e.g. an "error" decoded from a 500 / malformed response) is a runner
    # failure — it must surface as XcuitestChannelError, never be masked as element-not-found.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        return _Reply(status="error")

    with pytest.raises(XcuitestChannelError):
        _driver(transport).tap({"id": "ok"})


def test_a_runner_not_found_status_is_a_test_outcome() -> None:
    # `not-found` from the runner is a test outcome (the element could not be actuated), so it maps
    # to the shared ElementNotFound, distinct from an infrastructure error.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-ok", "ok", "OK"))
        return _Reply(status="not-found")

    with pytest.raises(base.ElementNotFound):
        _driver(transport).tap({"id": "ok"})


def test_screenshot_fails_loudly_on_a_runner_error(tmp_path: Any) -> None:
    out = tmp_path / "never.png"
    with pytest.raises(XcuitestChannelError):
        _driver(lambda m, p, b: _Reply(status="error", png=None)).screenshot(str(out))
    assert not out.exists()  # no bogus artifact written


def test_socket_timeout_is_bounded_after_the_single_snapshot_query() -> None:
    # BE-0105 replaced the ~10s+ per-attribute /elements walk with one app.snapshot(), so the
    # generous 60s stopgap is no longer needed: the timeout must stay bounded to a reasonable window
    # (it still covers a cold first snapshot) so a wedged runner fails loudly rather than hanging.
    assert 0 < _SOCKET_TIMEOUT_SECONDS <= 30


def test_actuation_write_gets_a_longer_bounded_timeout_than_reads() -> None:
    # A multi-touch gesture on a loaded CI host can take longer than a read, and BE-0207 must not
    # re-issue a write after delivery (double-actuation risk) — so a write gets ONE longer window
    # rather than the retry a read leans on. Reads stay tight; the write window stays bounded so a
    # genuinely wedged runner still fails loudly.
    assert _timeout_for("GET") == _SOCKET_TIMEOUT_SECONDS
    assert _timeout_for("POST") == _ACTUATION_TIMEOUT_SECONDS
    assert _ACTUATION_TIMEOUT_SECONDS > _SOCKET_TIMEOUT_SECONDS
    assert _ACTUATION_TIMEOUT_SECONDS <= 60  # still bounded


def test_raw_transport_applies_the_per_method_socket_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The single-attempt transport must open each connection with the timeout for that method's
    # idempotency class: reads tight, actuation writes longer. Faked at the http.client boundary
    # (allowed: it is a real network call) so the wiring is verified without a Simulator.
    seen: list[tuple[str, float | None]] = []

    class _FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"status":"ok"}'

    class _FakeConn:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            self._timeout = timeout

        def connect(self) -> None:
            pass

        def request(self, method: str, path: str, body: Any = None, headers: Any = None) -> None:
            seen.append((method, self._timeout))

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr("bajutsu.drivers.xcuitest.http.client.HTTPConnection", _FakeConn)
    transport = _raw_http_transport("127.0.0.1", 1234)
    transport("GET", "/elements", None)
    transport("POST", "/gesture", {"kind": "pinch"})
    assert seen == [("GET", _SOCKET_TIMEOUT_SECONDS), ("POST", _ACTUATION_TIMEOUT_SECONDS)]


def test_raw_transport_splits_delivery_on_connect_versus_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The `delivered` flag drives whether a failed write may be re-issued, so it must flip exactly at
    # the socket opening: a connect failure never reached the runner (re-issuable), but any failure
    # once the socket is open may have (a POST is then not re-issued — a double-actuation risk).
    class _Conn:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            self.connected = False

        def connect(self) -> None:
            if fail_at == "connect":
                raise OSError("connection refused")
            self.connected = True

        def request(self, method: str, path: str, body: Any = None, headers: Any = None) -> None:
            raise OSError("broken pipe mid-send")

        def close(self) -> None:
            pass

    monkeypatch.setattr("bajutsu.drivers.xcuitest.http.client.HTTPConnection", _Conn)
    transport = _raw_http_transport("127.0.0.1", 1234)

    fail_at = "connect"
    with pytest.raises(_TransportFailure) as connect_exc:
        transport("POST", "/gesture", {"kind": "pinch"})
    assert connect_exc.value.delivered is False  # never reached the runner → safe to re-issue

    fail_at = "send"
    with pytest.raises(_TransportFailure) as send_exc:
        transport("POST", "/gesture", {"kind": "pinch"})
    assert (
        send_exc.value.delivered is True
    )  # bytes may have started reaching the runner → do not re-issue


# --- transient-transport retry policy (BE-0207) --- #
#
# The retry lives behind the `TransportFn` seam: `_with_retry` wraps a single-attempt transport and
# re-issues only *transport* failures (`_TransportFailure`), never a decoded outcome. It is exercised
# here with a fake inner transport (no Simulator), passing a no-op `sleep` so backoff adds no wall time.


def _counting(replies: list[_Reply | Exception]) -> tuple[TransportFn, list[int]]:
    """A fake inner transport that yields *replies* in order; each item is either a `_Reply` to return
    or an `Exception` (e.g. `_TransportFailure`, `XcuitestRunnerCrashError`) to raise. `calls[0]` counts
    how many times it was invoked."""
    calls = [0]

    def inner(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls[0] += 1
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return inner, calls


def test_is_retry_eligible_splits_on_delivery_and_idempotency() -> None:
    # Not delivered → the runner never acted, safe to re-issue any method.
    assert _is_retry_eligible("POST", delivered=False) is True
    assert _is_retry_eligible("GET", delivered=False) is True
    # Delivered → only idempotent reads may be re-issued; a write could double-apply.
    assert _is_retry_eligible("GET", delivered=True) is True
    assert _is_retry_eligible("POST", delivered=True) is False


def test_transient_read_failure_retries_then_succeeds() -> None:
    inner, calls = _counting([_TransportFailure("timed out", delivered=True), _Reply(status="ok")])
    reply = _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert reply.status == "ok"
    assert calls[0] == 2  # first attempt failed, second succeeded


def test_write_that_times_out_after_delivery_is_not_re_sent() -> None:
    # A POST whose response timed out *after* the request was delivered must not be re-issued — that
    # could double-apply the gesture. It fails loudly on the first attempt instead.
    inner, calls = _counting([_TransportFailure("timed out", delivered=True), _Reply(status="ok")])
    with pytest.raises(XcuitestChannelError, match="POST /gesture"):
        _with_retry(inner, sleep=lambda _s: None)("POST", "/gesture", {"kind": "pinch"})
    assert calls[0] == 1  # no retry: the second (success) reply was never reached


def test_write_that_never_reached_the_runner_is_retried() -> None:
    # A connect/send failure means the runner never acted, so even a POST is safe to re-issue.
    inner, calls = _counting([_TransportFailure("refused", delivered=False), _Reply(status="ok")])
    reply = _with_retry(inner, sleep=lambda _s: None)("POST", "/gesture", {"kind": "pinch"})
    assert reply.status == "ok"
    assert calls[0] == 2


def test_persistent_failure_exhausts_attempts_and_fails_loudly() -> None:
    inner, calls = _counting([_TransportFailure("refused", delivered=False)] * (_MAX_ATTEMPTS + 2))
    with pytest.raises(XcuitestChannelError, match="GET /elements failed: refused"):
        _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert calls[0] == _MAX_ATTEMPTS  # exactly the bounded number of attempts, no more


def test_a_decoded_outcome_reply_is_never_retried() -> None:
    # `stale` / `not-found` are decoded outcomes (a `_Reply`, not a `_TransportFailure`), so the
    # retry seam returns them untouched — retrying an outcome is exactly the absorption BE-0049 rejects.
    inner, calls = _counting([_Reply(status="not-found")])
    reply = _with_retry(inner, sleep=lambda _s: None)("POST", "/tap", {"handle": "h"})
    assert reply.status == "not-found"
    assert calls[0] == 1


def test_each_retry_emits_a_diagnostic(caplog: Any) -> None:
    inner, _calls = _counting([_TransportFailure("timed out", delivered=True), _Reply(status="ok")])
    with caplog.at_level("WARNING"):
        _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert any("GET /elements" in r.message and "1/" in r.message for r in caplog.records)


def test_retry_knobs_are_bounded() -> None:
    # A small fixed attempt count, per BE-0207: enough to ride out a brief stall, not so many that a
    # wedged runner is retried for an unbounded stretch.
    assert 1 < _MAX_ATTEMPTS <= 5


# --- mid-run crash recovery (BE-0287) --- #
#
# A crash outlives the BE-0207 transient budget: `_with_retry` exhausts and raises
# `XcuitestRunnerCrashError`, and `_with_crash_recovery` decides — by the same `delivered` split — to
# wait out the crash and re-issue an idempotent call, or to fail loudly on a write it must not re-send.
# Health-polling is faked (`health=lambda _t: ...`) so no wall time is spent waiting for a recovery.


def _crash(method: str, *, delivered: bool) -> XcuitestRunnerCrashError:
    return XcuitestRunnerCrashError(
        f"runner channel {method} /x failed: refused", method=method, delivered=delivered
    )


def test_exhausted_transient_retries_raise_a_crash_error_carrying_delivery_info() -> None:
    # The BE-0207 seam now signals exhaustion with the crash error, tagged so the recovery layer can
    # tell a safe-to-re-issue read from a write that must not be re-applied.
    inner, _calls = _counting([_TransportFailure("refused", delivered=False)] * _MAX_ATTEMPTS)
    with pytest.raises(XcuitestRunnerCrashError) as exc:
        _with_retry(inner, sleep=lambda _s: None)("GET", "/elements", None)
    assert exc.value.method == "GET"
    assert exc.value.delivered is False
    # It is still an XcuitestChannelError, so callers that catch the broader type are unaffected.
    assert isinstance(exc.value, XcuitestChannelError)


def test_a_read_crash_waits_for_the_runner_then_re_issues() -> None:
    # A read is idempotent, so once the runner is back it is safe to re-issue and continue the run.
    inner, calls = _counting([_crash("GET", delivered=True), _Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)(
        "GET", "/elements", None
    )
    assert reply.status == "ok"
    assert calls[0] == 2  # crashed, then re-issued after the runner recovered


def test_an_undelivered_write_crash_re_issues_after_recovery() -> None:
    # A write that never reached the runner never applied, so re-issuing it after recovery is safe.
    inner, calls = _counting([_crash("POST", delivered=False), _Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)(
        "POST", "/gesture", {"kind": "pinch"}
    )
    assert reply.status == "ok"
    assert calls[0] == 2


def test_a_delivered_write_crash_is_never_re_issued_and_fails_distinctly() -> None:
    # A delivered write may already have applied; re-sending could double-actuate. Even with the runner
    # recovered (health True), it must fail with a distinct crash diagnostic rather than re-issue.
    inner, calls = _counting([_crash("POST", delivered=True), _Reply(status="ok")])
    with pytest.raises(XcuitestRunnerCrashError, match="POST /gesture"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)(
            "POST", "/gesture", {"kind": "pinch"}
        )
    assert calls[0] == 1  # never re-issued


def test_a_declared_crash_offers_the_diagnostics_hook_the_moment_it_happens() -> None:
    # BE-0361 unit 2: the capture fires on the crash *declaration*, before recovery decides anything,
    # because that is while the Simulator state explaining the crash still exists. Once per crash —
    # the per-run cap lives in the capture, not here.
    seen: list[bool] = []
    inner, _calls = _counting(
        [_crash("GET", delivered=True), _crash("GET", delivered=True), _Reply(status="ok")]
    )
    reply = _with_crash_recovery(
        inner, health=lambda _t: _HealthWait.READY, on_stall=lambda: seen.append(True)
    )("GET", "/elements", None)
    assert reply.status == "ok"
    # Once per crash, so a flapping runner's second and third stalls are offered too — the per-run cap
    # lives in the capture, not here.
    assert seen == [True, True]


def test_a_broken_diagnostics_hook_never_changes_the_crash_verdict() -> None:
    # The hook is an observer on a failure path. If it raises, the run must still fail with the crash
    # diagnostic the caller acts on — never with the diagnostics' own error standing in for it.
    def _explode() -> None:
        raise RuntimeError("the probe itself broke")

    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="did not recover"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.TIMED_OUT, on_stall=_explode)(
            "GET", "/elements", None
        )
    assert calls[0] == 1


def test_a_read_crash_that_never_recovers_fails_loudly() -> None:
    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="did not recover"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.TIMED_OUT)(
            "GET", "/elements", None
        )
    assert calls[0] == 1  # health never came back, so the read was not re-issued


def test_a_reissue_that_crashes_again_is_recovered_within_budget() -> None:
    # BE-0287: the observed flake crashes on back-to-back /screenshot calls — the runner comes back,
    # the re-issue crashes again, it comes back once more, then succeeds. The recovery must ride out
    # more than one consecutive crash (single-shot recovery would fail the run on the second crash).
    inner, calls = _counting(
        [_crash("GET", delivered=True), _crash("GET", delivered=True), _Reply(status="ok")]
    )
    reply = _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)(
        "GET", "/screenshot", None
    )
    assert reply.status == "ok"
    assert calls[0] == 3  # crashed, recovered+re-issued, crashed again, recovered, then succeeded


def test_a_runner_that_never_stabilizes_fails_past_the_recovery_budget() -> None:
    # A runner that crashes on every re-issue is not a single flake; after `max_recoveries` consecutive
    # crashes the run fails loudly (distinct from the health-never-came-back "did not recover" path),
    # rather than looping forever.
    inner, calls = _counting([_crash("GET", delivered=False)] * 3)
    with pytest.raises(XcuitestRunnerCrashError, match="past the 2-recovery budget"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY, max_recoveries=2)(
            "GET", "/elements", None
        )
    assert calls[0] == 3  # the initial call plus max_recoveries re-issues, all crashing


# A *wedged automation session* (BE-0354): the runner's HTTP server answers /health while the same
# read keeps reaching it and never being answered. No re-issue can clear that, so the channel raises
# at once and lets the pipeline's device-level retry take over. A connection-level crash — refused, or
# reset mid-response — is the genuinely crashing runner the loop above was built for and keeps riding.


def _hang(method: str) -> XcuitestRunnerCrashError:
    return XcuitestRunnerCrashError(
        f"runner channel {method} /x failed: timed out", method=method, delivered=True, hung=True
    )


def test_a_read_that_keeps_hanging_after_recovery_is_diagnosed_as_a_wedged_session() -> None:
    # The measured signature: /health answers every time, and the re-issued read times out again. The
    # third hang is where a single long-running operation holding the runner's lock is ruled out — its
    # own call would have failed its own retry ladder first — so the session is wedged.
    inner, calls = _counting([_hang("GET")] * 4)
    with pytest.raises(XcuitestRunnerCrashError, match="wedged automation session") as exc:
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)("GET", "/screenshot", None)
    assert calls[0] == 3  # the original call plus two re-issues, then the hand-over
    assert exc.value.hung is True


def test_a_hang_that_clears_on_re_issue_is_still_ridden_out() -> None:
    # One hang is not a wedge: the runner answered the very next call, which is exactly the transient
    # the recovery loop exists for.
    inner, calls = _counting([_hang("GET"), _Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)(
        "GET", "/screenshot", None
    )
    assert reply.status == "ok"
    assert calls[0] == 2


def test_hangs_broken_by_a_connection_crash_never_accrue_to_a_wedge() -> None:
    # The count is over *consecutive* hangs: a runner that hangs, then goes away, then hangs again is
    # flapping, not wedged, so it keeps riding out recoveries to the ordinary budget.
    inner, calls = _counting(
        [_hang("GET"), _crash("GET", delivered=False), _hang("GET"), _Reply(status="ok")]
    )
    reply = _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)(
        "GET", "/screenshot", None
    )
    assert reply.status == "ok"
    assert calls[0] == 4


def test_a_connection_level_crash_never_reads_as_a_wedged_session() -> None:
    # `delivered` alone cannot select the wedge: BE-0207 tags a mid-response reset delivered too, and
    # only a call that hung says the runner accepted the work and never finished it.
    inner, _calls = _counting([_crash("GET", delivered=True)] * 4)
    with pytest.raises(XcuitestRunnerCrashError, match="past the 3-recovery budget"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)("GET", "/screenshot", None)


def test_the_retry_seam_tags_a_hung_call_apart_from_a_refused_one() -> None:
    # The tag has to survive the BE-0207 seam, since that is where a transport failure becomes the
    # crash error the recovery layer classifies.
    inner, _calls = _counting([_TransportFailure("timed out", delivered=True, hung=True)] * 3)
    with pytest.raises(XcuitestRunnerCrashError) as hung:
        _with_retry(inner, sleep=lambda _s: None)("GET", "/screenshot", None)
    assert hung.value.hung is True

    inner, _calls = _counting([_TransportFailure("refused", delivered=False)] * 3)
    with pytest.raises(XcuitestRunnerCrashError) as refused:
        _with_retry(inner, sleep=lambda _s: None)("GET", "/screenshot", None)
    assert refused.value.hung is False


def test_a_normal_reply_passes_through_without_probing_health() -> None:
    def _boom(_t: float) -> _HealthWait:  # health must not be consulted on the happy path
        raise AssertionError("health probed on a non-crash call")

    inner, calls = _counting([_Reply(status="ok")])
    reply = _with_crash_recovery(inner, health=_boom)("GET", "/elements", None)
    assert reply.status == "ok"
    assert calls[0] == 1


# Crash-recovery splits on the runner *process*. A process that has exited will never answer /health
# again on its port, so recovery fails fast rather than polling the dead port for the whole window; a
# process still alive stays BE-0287's recoverable case and waits out `health`.


def test_a_crash_with_a_dead_runner_process_fails_fast_without_polling_health() -> None:
    # The runner's `xcodebuild` process has exited (runner_alive False): nothing respawns it on this
    # port mid-recovery, so recovery must fail fast with a distinct diagnostic — never consulting
    # `health`, which would only wait out an inevitable failure (the readiness-time crash this fixes).
    def _health_must_not_run(_t: float) -> _HealthWait:
        raise AssertionError("health polled despite the runner process having exited")

    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="process exited"):
        _with_crash_recovery(inner, health=_health_must_not_run, runner_alive=lambda: False)(
            "GET", "/elements", None
        )
    assert calls[0] == 1  # crashed once, then failed fast — never re-issued


def test_a_crash_with_a_live_runner_process_still_waits_out_health() -> None:
    # The process is alive but momentarily unreachable (runner_alive True): this is BE-0287's
    # recoverable case, unchanged — recovery waits out `health` and re-issues the idempotent read.
    inner, calls = _counting([_crash("GET", delivered=True), _Reply(status="ok")])
    reply = _with_crash_recovery(
        inner, health=lambda _t: _HealthWait.READY, runner_alive=lambda: True
    )("GET", "/elements", None)
    assert reply.status == "ok"
    assert calls[0] == 2  # waited out health, then re-issued — the alive-process path is unchanged


def test_absent_runner_alive_keeps_the_be0287_recovery_unchanged() -> None:
    # With no liveness predicate (a test fake, or a caller that supplies none), the fast path is off:
    # a crash that never recovers waits out `health` and fails with the BE-0287 "did not recover"
    # diagnostic exactly as before — the default is byte-for-byte the prior behavior.
    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="did not recover"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.TIMED_OUT)(
            "GET", "/elements", None
        )
    assert calls[0] == 1


def test_a_recovery_logs_the_crash_as_visibly_as_a_retried_blip(caplog: Any) -> None:
    # BE-0287 Unit 4: a crashed-and-recovered run must never be indistinguishable from one that never
    # crashed — both the crash and the recovery are logged.
    inner, _calls = _counting([_crash("GET", delivered=True), _Reply(status="ok")])
    with caplog.at_level("WARNING"):
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.READY)("GET", "/elements", None)
    joined = " ".join(r.message for r in caplog.records).lower()
    assert "crash" in joined
    assert "recovered" in joined


def test_health_is_the_recovery_probe_so_it_never_recurses_into_recovery() -> None:
    # `/health` is how the layer detects recovery, so a crashed health probe must pass straight through
    # rather than trigger a nested recovery (which would block for the whole recovery timeout).
    def _boom(_t: float) -> _HealthWait:
        raise AssertionError("health probe triggered a nested recovery")

    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError):
        _with_crash_recovery(inner, health=_boom)("GET", "/health", None)
    assert calls[0] == 1


def test_recovery_timeout_is_bounded() -> None:
    # Long enough to outlast the ~30s outage the flake showed, short enough to fail loudly rather than
    # wait forever on a runner that is truly gone.
    assert 30 <= _RECOVERY_TIMEOUT_SECONDS <= 300


def test_await_health_reports_ready_once_the_runner_is_ready() -> None:
    outcome = _await_health(
        lambda m, p, b: _Reply(status="ready"), timeout=1.0, sleep=lambda _s: None
    )
    assert outcome is _HealthWait.READY


def test_await_health_reports_timed_out_when_the_runner_never_becomes_ready() -> None:
    ticks = iter([0.0, 0.0, 0.5, 1.0, 1.0])  # deadline = 0.0 + 0.3; the third read is past it
    outcome = _await_health(
        lambda m, p, b: _Reply(status="starting"),
        timeout=0.3,
        poll=0.0,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert outcome is _HealthWait.TIMED_OUT


def test_await_health_treats_a_transport_failure_as_not_ready_then_recovers() -> None:
    inner, _calls = _counting(
        [_TransportFailure("refused", delivered=False), _Reply(status="ready")]
    )
    assert _await_health(inner, timeout=1.0, poll=0.0, sleep=lambda _s: None) is _HealthWait.READY


# The wait re-asks the runner's liveness while it runs (BE-0360). A crashing runner's death becomes
# observable *during* the window — its `xcodebuild` exit and its suite's result line both follow the
# crash — so a verdict sampled only before the wait waits out the very failure the fast-fail exists
# for.


def test_await_health_ends_early_once_the_runner_is_found_gone_mid_wait() -> None:
    # The liveness verdict flips partway through the window: the wait must end there rather than at
    # the deadline, and say which end it reached so the caller can diagnose it as a dead runner.
    ticks = iter([0.0, 1.0, 2.0])  # deadline = 0.0 + 60.0; both reads are far short of it
    alive = iter([True, False])
    outcome = _await_health(
        lambda m, p, b: _Reply(status="starting"),
        timeout=60.0,
        poll=0.0,
        runner_alive=lambda: next(alive),
        liveness_poll=1.0,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert outcome is _HealthWait.GONE


def test_await_health_asks_the_liveness_verdict_once_a_second_not_once_a_poll() -> None:
    # The `/health` probe runs every 100ms while the liveness check reads the runner's capture from a
    # private offset, so asking it at the probe interval would cost hundreds of file reads per window
    # to learn of the death barely sooner. Ten polls across one second must ask it once.
    ticks = iter([n / 10 for n in range(12)])  # 0.0, 0.1, … — the poll grain, one read per pass
    asked = [0]

    def _alive() -> bool:
        asked[0] += 1
        return True

    outcome = _await_health(
        lambda m, p, b: _Reply(status="starting"),
        timeout=1.0,
        poll=0.0,
        runner_alive=_alive,
        liveness_poll=1.0,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert outcome is _HealthWait.TIMED_OUT
    assert asked[0] == 1


def test_await_health_prefers_a_ready_runner_over_a_negative_liveness_verdict() -> None:
    # The verdict is asked *after* the probe, so a runner that answers `ready` on the very poll where
    # its capture first shows the run-ended marker still counts as recovered: a runner that is serving
    # is serving, whatever its log says.
    def _alive() -> bool:
        raise AssertionError("liveness asked despite the runner answering ready")

    outcome = _await_health(
        lambda m, p, b: _Reply(status="ready"),
        timeout=60.0,
        poll=0.0,
        runner_alive=_alive,
        liveness_poll=0.0,  # due on the first pass, so only the probe-first ordering keeps it unasked
        sleep=lambda _s: None,
    )
    assert outcome is _HealthWait.READY


def test_await_health_without_a_liveness_check_never_reports_gone() -> None:
    # The startup caller passes none — its spawn retry owns its own liveness check — so the wait is
    # exactly what it was: it ends on `ready` or on the deadline, and nothing else.
    ticks = iter([0.0, 0.0, 0.5, 1.0, 1.0])
    outcome = _await_health(
        lambda m, p, b: _Reply(status="starting"),
        timeout=0.3,
        poll=0.0,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert outcome is _HealthWait.TIMED_OUT


def test_a_runner_found_gone_during_the_wait_gets_the_gone_diagnostic() -> None:
    # Unit 3: the same fact observed a moment later than the pre-wait check reads it, so it earns the
    # same diagnosis — a runner that will not come back, not a window that was waited out. The
    # misdirection this replaces is the point: "did not recover within 60s" describes a runner that
    # was given a fair chance.
    inner, calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="is gone mid-run") as exc:
        _with_crash_recovery(inner, health=lambda _t: _HealthWait.GONE, runner_alive=lambda: True)(
            "GET", "/elements", None
        )
    assert "did not recover" not in str(exc.value)
    assert calls[0] == 1  # never re-issued: nothing will answer on this port again


def test_a_wait_that_reaches_its_deadline_keeps_the_did_not_recover_diagnostic() -> None:
    # The other end of the wait is unchanged: a runner still plausibly alive that never came back is
    # the case the 60-second window exists for, and its diagnosis stays BE-0287's.
    inner, _calls = _counting([_crash("GET", delivered=False)])
    with pytest.raises(XcuitestRunnerCrashError, match="did not recover") as exc:
        _with_crash_recovery(
            inner, health=lambda _t: _HealthWait.TIMED_OUT, runner_alive=lambda: True
        )("GET", "/elements", None)
    assert "is gone mid-run" not in str(exc.value)


# --- handle_system_alert (BE-0316) ---------------------------------------------------------------
# The SpringBoard permission-prompt tap. Resolution stays Python-side over the buttons the runner
# returns from `/systemAlert/query`, so the same zero / ambiguous / index discipline every selector
# follows decides which button is tapped — proven here against a fake transport, no Simulator.


def _alert_transport(
    *button_batches: list[dict[str, Any]],
) -> tuple[TransportFn, list[tuple[str, str, Mapping[str, Any] | None]]]:
    """A transport whose `/systemAlert/query` yields each batch in turn (last repeats), recording taps.

    Successive batches let a test model the prompt appearing only on a later poll; a single batch
    (the common case) just answers every query with it.
    """
    sent: list[tuple[str, str, Mapping[str, Any] | None]] = []
    batches = iter(button_batches)
    current = next(batches, [])

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        nonlocal current
        if path == "/systemAlert/query":
            batch = current
            current = next(batches, current)  # advance; the last batch repeats
            return _elements(*batch)
        sent.append((method, path, body))
        return _Reply(status="ok")

    return transport, sent


def test_handle_system_alert_resolves_the_labelled_button_and_taps_its_handle() -> None:
    transport, sent = _alert_transport(
        [
            _el_wire("h-allow", label="Allow", traits=["button"]),
            _el_wire("h-deny", label="Don't Allow", traits=["button"]),
        ]
    )
    _driver(transport).handle_system_alert({"label": "Allow"}, timeout=5.0)
    assert sent == [("POST", "/systemAlert/tap", {"handle": "h-allow"})]


def test_handle_system_alert_ambiguous_label_fails_without_index() -> None:
    # Distinct frames: two genuinely different buttons sharing a label, not a content-identical
    # duplicate registration — the latter no longer counts as ambiguous (resolve_unique collapses it).
    transport, sent = _alert_transport(
        [
            _el_wire("h-a", label="OK", traits=["button"], frame=(0.0, 0.0, 10.0, 10.0)),
            _el_wire("h-b", label="OK", traits=["button"], frame=(20.0, 0.0, 10.0, 10.0)),
        ]
    )
    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).handle_system_alert({"label": "OK"}, timeout=5.0)
    assert sent == []  # no tap on an ambiguous match


def test_handle_system_alert_index_disambiguates_multiple_matches() -> None:
    transport, sent = _alert_transport(
        [
            _el_wire("h-a", label="OK", traits=["button"], frame=(0.0, 0.0, 10.0, 10.0)),
            _el_wire("h-b", label="OK", traits=["button"], frame=(20.0, 0.0, 10.0, 10.0)),
        ]
    )
    _driver(transport).handle_system_alert({"label": "OK", "index": 1}, timeout=5.0)
    assert sent == [("POST", "/systemAlert/tap", {"handle": "h-b"})]


def test_handle_system_alert_no_alert_within_timeout_fails() -> None:
    transport, sent = _alert_transport([])  # the prompt never appears
    with pytest.raises(base.ElementNotFound, match="no system alert appeared"):
        _driver(transport).handle_system_alert({"label": "Allow"}, timeout=0.0)
    assert sent == []


def test_handle_system_alert_waits_for_a_prompt_that_appears_on_a_later_poll() -> None:
    # Empty first, then the prompt — the condition wait rides the interval (no-op sleep) rather than
    # failing on the first empty read.
    transport, sent = _alert_transport([], [_el_wire("h-allow", label="Allow", traits=["button"])])
    _driver(transport).handle_system_alert({"label": "Allow"}, timeout=5.0)
    assert sent == [("POST", "/systemAlert/tap", {"handle": "h-allow"})]


def test_handle_system_alert_present_but_no_label_match_fails() -> None:
    transport, sent = _alert_transport([_el_wire("h-deny", label="Don't Allow", traits=["button"])])
    with pytest.raises(base.ElementNotFound):
        _driver(transport).handle_system_alert({"label": "Allow"}, timeout=0.0)
    assert sent == []


def test_handle_system_alert_reports_a_vanished_button_as_not_found() -> None:
    # The alert dismissed itself between query and tap: the tap reply is not "ok".
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/systemAlert/query":
            return _elements(_el_wire("h-allow", label="Allow", traits=["button"]))
        return _Reply(status="stale")

    with pytest.raises(base.ElementNotFound, match="vanished"):
        _driver(transport).handle_system_alert({"label": "Allow"}, timeout=5.0)


# --- system_alert_labels (BE-0315): the reactive guard's non-blocking presence read ----------------


def test_system_alert_labels_reads_button_labels_from_the_query() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        assert (method, path) == ("POST", "/systemAlert/query")
        return _elements(
            _el_wire("h-deny", label="Don't Allow", traits=["button"]),
            _el_wire("h-allow", label="Allow", traits=["button"]),
        )

    assert _driver(transport).system_alert_labels() == ["Don't Allow", "Allow"]


def test_system_alert_labels_returns_empty_when_no_alert_is_up() -> None:
    # An empty `/systemAlert/query` (no alert, or a caught proxy-in-flux query) reads as no labels,
    # which the reactive guard treats as "no alert this poll" and re-checks next interval (BE-0315).
    assert _driver(lambda m, p, b: _elements()).system_alert_labels() == []


# --- dismiss_blocking_tip: the TipKit guard's driver half ---
# The TipKit-internal identifier lives only in this driver, so these tests are what pin it: the
# orchestrator's guards see a boolean and never name a node.


def _tip_wire(handle: str, identifier: str) -> dict[str, Any]:
    # The tip's dismiss scrim covers the whole screen, which is how it blocks the tap underneath.
    return _el_wire(handle, identifier, frame=(0.0, 0.0, 402.0, 874.0))


def _tip_container_wire(handle: str = "h-tipview") -> dict[str, Any]:
    # The tip's own container, anchored to its element rather than full-screen. The guard requires it
    # alongside the scrim, so every tree standing for a showing tip carries both — a scrim on its own
    # is what an app's `confirmationDialog` looks like, and must be left alone.
    return _el_wire(handle, "TipView", frame=(72.0, 101.0, 320.0, 95.0))


def _tip_element(identifier: str, frame: tuple[float, float, float, float]) -> base.Element:
    # A caller's-tree node: the mid-wait gate hands the guard a tree it already holds, so these
    # stand in for what a poll tick saw rather than for a wire reply.
    return base.Element(
        identifier=identifier,
        label=None,
        value=None,
        traits=["other"],
        frame=frame,
        nativeZ=None,
    )


def test_dismiss_blocking_tip_taps_the_dismiss_region_and_reports_it() -> None:
    sent: list[tuple[str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _tip_wire("h-scrim", "PopoverDismissRegion"),
                _tip_container_wire(),
                _el_wire("h-refresh", "stable.refresh", "Refresh", traits=["button"]),
            )
        sent.append((path, body))
        return _Reply(status="ok")

    assert _driver(transport).dismiss_blocking_tip() is True
    assert sent == [("/tap", {"handle": "h-scrim"})]


def test_dismiss_blocking_tip_reports_false_and_taps_nothing_when_no_tip_is_up() -> None:
    # The common case: both guards ask on every poll, so absence is a plain False, not an exception.
    sent: list[tuple[str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-refresh", "stable.refresh", "Refresh", traits=["button"]))
        sent.append((path, body))
        return _Reply(status="ok")

    assert _driver(transport).dismiss_blocking_tip() is False
    assert sent == []


def test_dismiss_blocking_tip_reports_false_when_the_tip_closes_itself_mid_dismiss() -> None:
    # TipKit dismisses on its own rules, so the window between the snapshot that minted the handle and
    # the tap is a live race, not a defect. The runner answers `not-found` (or exhausts `stale`), which
    # `_actuate` raises as `ElementNotFound` — a `SelectorError`. Left to propagate it would surface
    # `PopoverDismissRegion`, an identifier no author wrote, as a wait's failure reason, and would
    # overwrite the real reason on the post-failure path. "The tip is gone" is what the caller asked
    # about, so it reads as no dismissal.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_tip_wire("h-scrim", "PopoverDismissRegion"), _tip_container_wire())
        return _Reply(status="not-found")  # the scrim went between the query and the tap

    assert _driver(transport).dismiss_blocking_tip() is False


def test_dismiss_blocking_tip_fails_loudly_on_two_dismiss_regions() -> None:
    # A shape TipKit should never produce. Distinct frames so `resolve_unique` cannot collapse them
    # as a content-identical duplicate: with a real choice to make, it must refuse to guess.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        assert path == "/elements", f"no actuation may be attempted, got {path}"
        return _elements(
            _el_wire("h-a", "PopoverDismissRegion", frame=(0.0, 0.0, 402.0, 874.0)),
            _el_wire("h-b", "PopoverDismissRegion", frame=(0.0, 0.0, 100.0, 100.0)),
            _tip_container_wire(),
        )

    with pytest.raises(base.AmbiguousSelector):
        _driver(transport).dismiss_blocking_tip()


def test_dismiss_blocking_tip_rules_a_tip_out_from_the_callers_tree_without_querying() -> None:
    # The mid-wait gate asks on every poll tick, so the common "no tip" answer must cost no query —
    # otherwise a guarded wait polls at half its usual rate and a tight timeout starts to flake.
    calls: list[str] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls.append(path)
        return _elements()

    tree = [
        base.Element(
            identifier="stable.refresh",
            label="Refresh",
            value=None,
            traits=["button"],
            frame=(0.0, 0.0, 10.0, 10.0),
            nativeZ=None,
        )
    ]
    assert _driver(transport).dismiss_blocking_tip(tree) is False
    assert calls == []


def test_dismiss_blocking_tip_still_queries_when_the_hint_shows_a_tip() -> None:
    # A handle is only valid from the snapshot that minted it, so a tip seen in the caller's tree is
    # re-resolved against this driver's own fresh query before the tap.
    sent: list[tuple[str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_tip_wire("h-scrim", "PopoverDismissRegion"), _tip_container_wire())
        sent.append((path, body))
        return _Reply(status="ok")

    hint = [
        _tip_element("PopoverDismissRegion", (0.0, 0.0, 402.0, 874.0)),
        _tip_element("TipView", (72.0, 101.0, 320.0, 95.0)),
    ]
    assert _driver(transport).dismiss_blocking_tip(hint) is True
    assert sent == [("/tap", {"handle": "h-scrim"})]


def test_dismiss_blocking_tip_leaves_an_app_popover_alone() -> None:
    # The scrim without the tip container is what a SwiftUI `confirmationDialog` looks like, measured
    # on-device: same identifier, same label, same full-screen frame. Tapping it would close the
    # app's own dialog, and the scenario would then fail on a missing button with no mention of the
    # guard, so the pair is what the guard requires before it acts.
    sent: list[tuple[str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(
                _tip_wire("h-scrim", "PopoverDismissRegion"),
                _el_wire("h-delete", "log.dialog.delete", "Delete", traits=["button"]),
            )
        sent.append((path, body))
        return _Reply(status="ok")

    assert _driver(transport).dismiss_blocking_tip() is False
    assert sent == []


def test_dismiss_blocking_tip_rules_an_app_popover_out_from_the_callers_tree_without_querying() -> (
    None
):
    # The short-circuit applies the pair too, not only the scrim: a wait polling while the app's own
    # dialog is up must not pay a query per tick for a dismiss that will never fire.
    calls: list[str] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls.append(path)
        return _elements()

    tree = [_tip_element("PopoverDismissRegion", (0.0, 0.0, 402.0, 874.0))]
    assert _driver(transport).dismiss_blocking_tip(tree) is False
    assert calls == []


def test_xcuitest_advertises_the_tipkit_capability() -> None:
    assert base.Capability.HANDLE_TIPKIT_TIP in XcuitestDriver.CAPABILITIES


# --- setPickerValue: the value-not-found status branch (BE-0356) ---


def test_set_picker_value_posts_the_handle_and_the_value() -> None:
    sent: list[tuple[str, Mapping[str, Any] | None]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        sent.append((path, body))
        if path == "/elements":
            return _elements(_el_wire("h-wheel", "form.school", None, "高校", ["pickerWheel"]))
        return _Reply(status="ok")

    _driver(transport).set_picker_value({"id": "form.school"}, "大学")
    assert ("/setPickerValue", {"handle": "h-wheel", "value": "大学"}) in sent


def test_set_picker_value_raises_element_not_found_naming_the_absent_value() -> None:
    # The runner resolved and adjusted a live wheel that never showed the value, so it answers with
    # its own status rather than `not-found` — whose "no actuatable element" message names the
    # selector and would misreport a perfectly resolved wheel. A SelectorError, so the run loop's
    # existing selector-failure handling covers it (the `select_option` precedent).
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-wheel", "form.school", None, "高校", ["pickerWheel"]))
        return _Reply(status="value-not-found")

    with pytest.raises(base.ElementNotFound, match="picker wheel has no value '大学院'"):
        _driver(transport).set_picker_value({"id": "form.school"}, "大学院")


def test_set_picker_value_reports_an_unknown_status_as_a_channel_error() -> None:
    # The catch-all still stands behind the new branch: an unrecognized status is a runner failure,
    # not a test outcome, so it must not be masked as a selector failure.
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/elements":
            return _elements(_el_wire("h-wheel", "form.school", None, "高校", ["pickerWheel"]))
        return _Reply(status="error")

    with pytest.raises(XcuitestChannelError):
        _driver(transport).set_picker_value({"id": "form.school"}, "大学")
