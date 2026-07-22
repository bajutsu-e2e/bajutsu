"""Tests for the capture operations layer (BE-0012, PR 2).

Operations-level tests with a FakeDriver injected — no HTTP, no Simulator.
"""

from __future__ import annotations

from pathlib import Path

from _shared import project

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence.redaction import Redactor
from bajutsu.scenario import Redact
from bajutsu.serve import operations as ops
from bajutsu.serve.state import ServeState


def _screen() -> list[base.Element]:
    """A small fake screen with distinct elements for testing."""
    return [
        {
            "identifier": None,
            "label": None,
            "traits": ["window"],
            "value": None,
            "frame": (0.0, 0.0, 320.0, 568.0),
        },
        {
            "identifier": "auth.email",
            "label": "Email",
            "traits": ["textField"],
            "value": None,
            "frame": (20.0, 100.0, 280.0, 30.0),
        },
        {
            "identifier": "auth.password",
            "label": "Password",
            "traits": ["textField"],
            "value": None,
            "frame": (20.0, 150.0, 280.0, 30.0),
        },
        {
            "identifier": "auth.submit",
            "label": "Login",
            "traits": ["button"],
            "value": None,
            "frame": (100.0, 220.0, 120.0, 44.0),
        },
    ]


def _state_with_config(tmp_path: Path) -> ServeState:
    """Build a ServeState with a bound config (needed for capture operations)."""
    scn_dir, cfg, runs = project(tmp_path)
    state = ServeState(runs_dir=runs, config=cfg, scenarios_dir=scn_dir, cwd=tmp_path)
    return state


# ---------------------------------------------------------------------------
# start_capture
# ---------------------------------------------------------------------------


def test_start_capture_opens_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    screen = _screen()
    driver = FakeDriver(screen)

    payload, status = ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )
    assert status == 200
    assert payload["ok"] is True
    assert state.capture is not None
    assert state.capture.target == "demo"
    assert len(state.capture.elements) == len(screen)


def test_start_capture_rejects_second_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())

    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )
    payload, status = ops.start_capture(
        state,
        {"target": "demo"},
        driver_factory=lambda _e, _b, _u: (FakeDriver(_screen()), lambda: None),
    )
    assert status == 409
    assert "already active" in payload["error"]


def test_start_capture_requires_config(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", config=None)
    payload, status = ops.start_capture(state, {"target": "demo"})
    assert status == 400
    assert "config" in payload["error"]


def _ios_state(tmp_path: Path) -> ServeState:
    """A ServeState whose `ios` target declares `backend: [ios]` (BE-0267)."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        f"  ios: {{ bundleId: com.example.ios, backend: [ios], scenarios: {scn_dir} }}\n"
        f"  xcuitest_only: {{ bundleId: com.example.x, backend: [xcuitest], scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    return ServeState(runs_dir=runs, config=cfg, scenarios_dir=scn_dir, cwd=tmp_path)


def _tuple_factory(seen: list[str]) -> object:
    """A recording factory that resolves the backends list the way the default factory does."""
    from bajutsu import backends

    def factory(_eff: object, backends_list: list[str], _udid: str) -> tuple[FakeDriver, object]:
        seen.append(backends.select_actuator_cost_first(backends_list, available=lambda a: True))
        return FakeDriver(_screen()), (lambda: None)

    return factory


def test_start_capture_ios_target_resolves_to_xcuitest(tmp_path: Path) -> None:
    # A `[ios]` target selects XCUITest — the sole iOS actuator (BE-0290).
    seen: list[str] = []
    state = _ios_state(tmp_path)
    _payload, status = ops.start_capture(
        state, {"target": "ios"}, driver_factory=_tuple_factory(seen)
    )
    assert status == 200
    assert seen == ["xcuitest"]


def test_start_capture_single_actuator_target_unchanged(tmp_path: Path) -> None:
    # A single-actuator target is a hard pin: capture hands the factory exactly its backend list.
    seen: list[list[str]] = []

    def factory(_eff: object, backends_list: list[str], _udid: str) -> tuple[FakeDriver, object]:
        seen.append(backends_list)
        return FakeDriver(_screen()), (lambda: None)

    state = _ios_state(tmp_path)
    _payload, status = ops.start_capture(state, {"target": "xcuitest_only"}, driver_factory=factory)
    assert status == 200
    assert seen == [["xcuitest"]]


def test_start_capture_explicit_body_backend_wins(tmp_path: Path) -> None:
    # An explicit `backend` in the request body is passed through as a single-element list,
    # overriding the target's own `[ios]` config — the `[backend] if backend else ...` TRUE
    # branch (BE-0267) that target-driven tests never reach.
    seen: list[list[str]] = []

    def factory(_eff: object, backends_list: list[str], _udid: str) -> tuple[FakeDriver, object]:
        seen.append(backends_list)
        return FakeDriver(_screen()), (lambda: None)

    state = _ios_state(tmp_path)
    _payload, status = ops.start_capture(
        state, {"target": "ios", "backend": "xcuitest"}, driver_factory=factory
    )
    assert status == 200
    assert seen == [["xcuitest"]]


def test_start_capture_explicit_alias_backend_resolves(tmp_path: Path) -> None:
    # An explicit alias like `backend: "ios"` still goes through the selector, not a raw passthrough:
    # the override branch hands the factory `["ios"]`, which resolves to XCUITest (BE-0267, BE-0290).
    seen: list[str] = []
    state = _ios_state(tmp_path)
    _payload, status = ops.start_capture(
        state, {"target": "xcuitest_only", "backend": "ios"}, driver_factory=_tuple_factory(seen)
    )
    assert status == 200
    assert seen == ["xcuitest"]


# ---------------------------------------------------------------------------
# mark_capture — tap
# ---------------------------------------------------------------------------


def test_mark_tap_resolves_and_actuates(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    screen = _screen()
    driver = FakeDriver(screen)
    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )

    payload, status = ops.mark_capture(
        state,
        {
            "kind": "tap",
            "point": [0.5, 0.41],  # inside auth.submit (100-220, 220-264 on 320x568)
        },
    )
    assert status == 200
    assert payload.get("refused") is None
    assert payload["selector"]["id"] == "auth.submit"
    assert payload["rung"] == "id"
    assert len(state.capture.steps) == 1
    assert ("tap", {"id": "auth.submit"}) in driver.actions


def test_mark_tap_ambiguous_returns_feedback(tmp_path: Path) -> None:
    dup_screen = [
        {
            "identifier": "dup",
            "label": "A",
            "traits": ["button"],
            "value": None,
            "frame": (10.0, 10.0, 80.0, 44.0),
        },
        {
            "identifier": "dup",
            "label": "B",
            "traits": ["button"],
            "value": None,
            "frame": (10.0, 60.0, 80.0, 44.0),
        },
    ]
    state = _state_with_config(tmp_path)
    driver = FakeDriver(dup_screen)
    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )

    payload, status = ops.mark_capture(state, {"kind": "tap", "point": [0.5, 0.3]})
    assert status == 200
    assert payload.get("ambiguity") is not None
    assert len(state.capture.steps) == 0


def test_mark_capture_no_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    payload, status = ops.mark_capture(state, {"kind": "tap", "point": [0.5, 0.5]})
    assert status == 400
    assert "no active" in payload["error"]


# ---------------------------------------------------------------------------
# mark_capture — type with redaction
# ---------------------------------------------------------------------------


def test_mark_type_with_redaction(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    screen = _screen()
    driver = FakeDriver(screen)
    ops.start_capture(
        state,
        {"target": "demo"},
        driver_factory=lambda _e, _b, _u: (driver, lambda: None),
        redactor=Redactor(Redact(fields=["password"]), values=["s3cret"]),
    )

    _payload, status = ops.mark_capture(
        state,
        {
            "kind": "type",
            "point": [0.5, 0.29],  # inside auth.password (20-300, 150-180 on 320x568)
            "text": "s3cret",
        },
    )
    assert status == 200
    assert len(state.capture.steps) >= 1
    type_step = state.capture.steps[-1]
    assert type_step.type is not None
    assert type_step.type.text == "[REDACTED]"


# ---------------------------------------------------------------------------
# finish_capture
# ---------------------------------------------------------------------------


def test_finish_saves_yaml(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    screen = _screen()
    driver = FakeDriver(screen)
    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )

    # Mark two taps
    ops.mark_capture(state, {"kind": "tap", "point": [0.3, 0.2]})  # auth.email
    ops.mark_capture(state, {"kind": "tap", "point": [0.5, 0.41]})  # auth.submit

    payload, status = ops.finish_capture(state, {"target": "demo"})
    assert status == 200
    assert payload["ok"] is True
    assert payload.get("path") is not None
    assert state.capture is None  # session cleared


def test_finish_capture_no_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    payload, status = ops.finish_capture(state, {})
    assert status == 400
    assert "no active" in payload["error"]


# ---------------------------------------------------------------------------
# resolve_capture_pick — live step-picking for the Edit editor (BE-0262)
# ---------------------------------------------------------------------------


def test_resolve_pick_returns_selector_without_side_effects(tmp_path: Path) -> None:
    # The Edit picker resolves a screen click against the live tree, mirroring mark's resolution —
    # but it is pure: it neither actuates the driver nor appends a step (the human Applies the
    # returned selector to the YAML). This is what keeps the live path off the verdict path.
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )
    before = list(driver.actions)  # start already took the initial screenshot

    payload, status = ops.resolve_capture_pick(
        state,
        {"point": [0.5, 0.41]},  # inside auth.submit
    )
    assert status == 200
    assert payload["selector"]["id"] == "auth.submit"
    assert payload["rung"] == "id"
    assert state.capture.steps == []  # no step appended
    assert driver.actions == before  # resolve drove nothing — no tap, no re-screenshot


def test_resolve_pick_ambiguous_returns_feedback(tmp_path: Path) -> None:
    dup_screen = [
        {
            "identifier": "dup",
            "label": "A",
            "traits": ["button"],
            "value": None,
            "frame": (10.0, 10.0, 80.0, 44.0),
        },
        {
            "identifier": "dup",
            "label": "B",
            "traits": ["button"],
            "value": None,
            "frame": (10.0, 60.0, 80.0, 44.0),
        },
    ]
    state = _state_with_config(tmp_path)
    driver = FakeDriver(dup_screen)
    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )

    payload, status = ops.resolve_capture_pick(state, {"point": [0.5, 0.3]})
    assert status == 200
    assert payload.get("ambiguity") is not None
    assert state.capture.steps == []


def test_resolve_pick_no_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    payload, status = ops.resolve_capture_pick(state, {"point": [0.5, 0.5]})
    assert status == 400
    assert "no active" in payload["error"]


def test_resolve_pick_rejects_another_users_session(tmp_path: Path) -> None:
    # Per-actor ownership (BE-0012) reused for the live Edit session (BE-0262 Unit 4): one user's
    # live session cannot be driven by another.
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    ops.start_capture(
        state,
        {"target": "demo"},
        actor="alice",
        driver_factory=lambda _e, _b, _u: (driver, lambda: None),
    )
    payload, status = ops.resolve_capture_pick(state, {"point": [0.5, 0.41]}, actor="bob")
    assert status == 403
    assert "another user" in payload["error"]


# ---------------------------------------------------------------------------
# close_capture — end a live session without saving a scenario (BE-0262)
# ---------------------------------------------------------------------------


def test_close_capture_tears_down_without_saving(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    ops.start_capture(
        state, {"target": "demo"}, driver_factory=lambda _e, _b, _u: (driver, lambda: None)
    )

    payload, status = ops.close_capture(state, {})
    assert status == 200
    assert payload["ok"] is True
    assert state.capture is None


def test_close_capture_no_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    payload, status = ops.close_capture(state, {})
    assert status == 400
    assert "no active" in payload["error"]


def test_close_capture_rejects_another_users_session(tmp_path: Path) -> None:
    state = _state_with_config(tmp_path)
    driver = FakeDriver(_screen())
    ops.start_capture(
        state,
        {"target": "demo"},
        actor="alice",
        driver_factory=lambda _e, _b, _u: (driver, lambda: None),
    )
    payload, status = ops.close_capture(state, {}, actor="bob")
    assert status == 403
    assert "another user" in payload["error"]
    assert state.capture is not None  # ownership check keeps the other user's session intact
