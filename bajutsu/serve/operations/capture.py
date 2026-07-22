"""Capture-session serve operations (BE-0012, split out in BE-0127)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bajutsu.config import load_config, resolve
from bajutsu.evidence.redaction import Redactor
from bajutsu.serve.operations._common import (
    _default_driver_factory,
    _device_args,
    _resolve_org_or_forbid,
)
from bajutsu.serve.state import CaptureSession, ServeState

if TYPE_CHECKING:
    # Only for annotations — resolve_capture (and CaptureResult) are imported lazily at call sites
    # to keep this module's import light, so the type stays out of the runtime import graph.
    from bajutsu.record_capture import CaptureResult


def start_capture(
    state: ServeState,
    body: dict[str, Any],
    *,
    actor: str | None = None,
    driver_factory: Any | None = None,
    redactor: Redactor | None = None,
) -> tuple[Any, int]:
    """Open a capture session: boot a live driver, take the initial screenshot + query."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    if state.capture is not None:
        return {"error": "capture session already active"}, 409

    target = str(body["target"])
    _org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    backend, udid, err = _device_args(body)
    if err:
        return err
    # An explicit body `backend` is passed through as-is: a single actuator stays a hard pin, while
    # a platform token like `ios` is still cost-ordered by the selector; otherwise the target's full
    # backend list is used, cost-ordered the same way (BE-0267).
    backends_list = [backend] if backend else list(target_cfg.backend or config.defaults.backend)
    if not udid:
        udid = "booted"

    factory = driver_factory or _default_driver_factory
    driver, teardown = factory(resolve(config, target), backends_list, udid)
    elements = driver.query()

    from bajutsu.elements import screen_size_from_elements

    screen_size = screen_size_from_elements(elements)
    namespaces: list[str] = list(target_cfg.id_namespaces)

    # Deliberately outside the `ArtifactStore` seam (BE-0258): a capture session is a live,
    # in-process object whose driver and HTTP handler share the same process for its lifetime,
    # not a stored run, so `state.runs_dir` (always local to whichever host holds the live
    # driver) stays correct here even when `state.artifacts` is an object-storage-backed store.
    shot_dir = state.runs_dir / "_capture"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shot_path = shot_dir / "screen.png"
    driver.screenshot(str(shot_path))

    state.capture = CaptureSession(
        driver=driver,
        target=target,
        elements=elements,
        screen_size=screen_size,
        namespaces=namespaces,
        redactor=redactor,
        actor=actor,
        screenshot_path=shot_path,
        teardown=teardown,
    )
    return {"ok": True, "screenSize": list(screen_size)}, 200


def _active_session(
    state: ServeState, actor: str | None
) -> tuple[CaptureSession | None, tuple[Any, int] | None]:
    """The active session if *actor* owns it, else an error response (BE-0262).

    Shared by every operation that drives an open session (mark / live-resolve / close), so the
    single-session and per-actor-ownership guards stay identical across them.
    """
    session = state.capture
    if session is None:
        return None, ({"error": "no active capture session"}, 400)
    if session.actor is not None and actor != session.actor:
        return None, ({"error": "capture session belongs to another user"}, 403)
    return session, None


def _resolve_point(
    session: CaptureSession, body: dict[str, Any]
) -> tuple[CaptureResult | None, tuple[Any, int] | None]:
    """Resolve a normalized screen point against the session's live tree.

    Returns the resolution result, or a malformed-point error response — the shared resolution mark
    actuates on and the live Edit picker returns as-is (BE-0262).
    """
    point = body.get("point", [0.5, 0.5])
    if not isinstance(point, list) or len(point) != 2:
        return None, ({"error": "point must be [x, y] normalized"}, 400)
    try:
        nx, ny = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None, ({"error": "point values must be numeric"}, 400)

    from bajutsu.record_capture import resolve_capture

    sw, sh = session.screen_size
    result = resolve_capture(session.elements, (nx * sw, ny * sh), session.namespaces)
    return result, None


def _feedback_payload(result: CaptureResult) -> tuple[Any, int] | None:
    """The refused / ambiguity response shared by mark and the live picker; None on a clean match."""
    if result.refused:
        return {"refused": result.refused}, 200
    if result.ambiguity:
        return {
            "ambiguity": [
                {"identifier": e["identifier"], "label": e["label"]} for e in result.ambiguity
            ],
            "selector": result.selector.model_dump(exclude_none=True, by_alias=True),
            "rung": result.rung,
        }, 200
    return None


def mark_capture(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Resolve a point, proxy-actuate, and append the step."""
    session, err = _active_session(state, actor)
    if err:
        return err
    assert session is not None

    kind = str(body.get("kind", "tap"))
    result, err = _resolve_point(session, body)
    if err:
        return err
    assert result is not None  # a clean _resolve_point returns the result and no error
    if (feedback := _feedback_payload(result)) is not None:
        return feedback

    from bajutsu.record_capture import step_for_tap, step_for_type

    sel = result.selector
    raw = sel.as_selector()

    if kind == "tap":
        session.driver.tap(raw)
        step = step_for_tap(sel)
    elif kind == "type":
        text = str(body.get("text", ""))
        session.driver.tap(raw)
        session.driver.type_text(text)
        step = step_for_type(sel, text, session.redactor)
    else:
        return {"error": f"unsupported capture kind: {kind}"}, 400

    session.steps.append(step)
    session.elements = session.driver.query()
    session.driver.screenshot(str(session.screenshot_path))

    return {
        "selector": sel.model_dump(exclude_none=True, by_alias=True),
        "rung": result.rung,
    }, 200


def finish_capture(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Save the captured scenario and close the session."""
    session = state.capture
    if session is None:
        return {"error": "no active capture session"}, 400

    org, forbidden = _resolve_org_or_forbid(state, session.target, actor)
    if forbidden:
        session.teardown()  # release the driver's runner even on the forbidden path (BE-0290)
        state.capture = None
        return forbidden

    from bajutsu.scenario.models import Scenario
    from bajutsu.scenario.serialize import dump_scenario_file

    scenario = Scenario(name="captured", steps=list(session.steps))
    yaml_text = dump_scenario_file([scenario])

    scope = state.for_org(org).scenarios.scope(session.target)
    saved: str | None = None
    if scope is not None:
        authored = scope.authored("captured")
        ref = authored.save[1] if authored.save else authored.out
        saved = scope.save(ref, yaml_text)

    session.teardown()  # stop the driver's runner subprocess before dropping the session (BE-0290)
    state.capture = None
    return {"ok": True, "path": saved, "yaml": yaml_text}, 200


def resolve_capture_pick(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Resolve a screen click against the live session's tree and return the selector (BE-0262).

    The Edit editor's live picker: it mirrors mark's resolution but is pure — it neither actuates the
    driver nor appends a step. The human Applies the returned selector to the YAML, so this stays
    authoring assistance and never touches the deterministic verdict path.
    """
    session, err = _active_session(state, actor)
    if err:
        return err
    assert session is not None

    result, err = _resolve_point(session, body)
    if err:
        return err
    assert result is not None  # a clean _resolve_point returns the result and no error
    if (feedback := _feedback_payload(result)) is not None:
        return feedback

    return {
        "selector": result.selector.model_dump(exclude_none=True, by_alias=True),
        "rung": result.rung,
    }, 200


def close_capture(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """End a live session without saving a scenario — the live Edit picker's teardown (BE-0262).

    finish_capture is save-and-close; this is the close half, for a picking session that produced no
    scenario to persist. Teardown mirrors finish's: run the session's teardown (stopping the driver's
    runner, BE-0290) and drop it, so a live Edit session cannot leak a driver or its runner.
    """
    session, err = _active_session(state, actor)
    if err:
        return err
    if session is not None:
        session.teardown()
    state.capture = None
    return {"ok": True}, 200
