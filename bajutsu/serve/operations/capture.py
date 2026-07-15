"""Capture-session serve operations (BE-0012, split out in BE-0127)."""

from __future__ import annotations

from typing import Any

from bajutsu.config import load_config
from bajutsu.redaction import Redactor
from bajutsu.serve.operations._common import (
    _default_driver_factory,
    _device_args,
    _resolve_org_or_forbid,
)
from bajutsu.serve.state import CaptureSession, ServeState


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
    # An explicit request is a hard pin; otherwise hand the full backend list to the cost-ordered
    # selector so `[ios]` resolves to idb rather than the alias head (BE-0267).
    backends_list = [backend] if backend else list(target_cfg.backend or config.defaults.backend)
    if not udid:
        udid = "booted"

    factory = driver_factory or _default_driver_factory
    driver = factory(target, backends_list, udid)
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
    )
    return {"ok": True, "screenSize": list(screen_size)}, 200


def mark_capture(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Resolve a point, proxy-actuate, and append the step."""
    session = state.capture
    if session is None:
        return {"error": "no active capture session"}, 400
    if session.actor is not None and actor != session.actor:
        return {"error": "capture session belongs to another user"}, 403

    kind = str(body.get("kind", "tap"))
    point = body.get("point", [0.5, 0.5])
    if not isinstance(point, list) or len(point) != 2:
        return {"error": "point must be [x, y] normalized"}, 400
    try:
        nx, ny = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return {"error": "point values must be numeric"}, 400

    from bajutsu.record_capture import resolve_capture, step_for_tap, step_for_type

    sw, sh = session.screen_size
    px, py = nx * sw, ny * sh
    result = resolve_capture(session.elements, (px, py), session.namespaces)

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

    state.capture = None
    return {"ok": True, "path": saved, "yaml": yaml_text}, 200
