"""Enrichment serve operations (BE-0014, split out in BE-0127)."""

from __future__ import annotations

from typing import Any

from bajutsu.common.scenario import load_scenario_file
from bajutsu.config import load_config, resolve
from bajutsu.serve.operations._common import (
    _default_driver_factory,
    _device_args,
    _resolve_org_or_forbid,
    _session_effective,
)
from bajutsu.serve.state import ServeState


def start_enrich(
    state: ServeState,
    body: dict[str, Any],
    *,
    actor: str | None = None,
    session: str | None = None,
    driver_factory: Any | None = None,
    agent_factory: Any | None = None,
) -> tuple[Any, int]:
    """Replay a scenario's steps and propose assertions via an enrichment agent."""
    cfg = state.binding_for(session, state.org_of(actor)).config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    if not body.get("scenario"):
        return {"error": "scenario is required"}, 400

    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor, session)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    scope = state.for_org(org).scenarios.scope(target, session=session, org=org)
    scenario_text = scope.read(str(body["scenario"])) if scope else None
    if scenario_text is None:
        return {"error": "scenario not found"}, 404

    scenarios = load_scenario_file(scenario_text).scenarios
    if not scenarios:
        return {"error": "no scenarios in file"}, 400

    name = str(body["name"]) if body.get("name") else None
    matched = next((s for s in scenarios if s.name == name), None) if name else scenarios[0]
    if matched is None:
        return {"error": f"scenario '{name}' not found in file"}, 404

    if agent_factory is None:
        from bajutsu.common.agents import availability as ai_availability
        from bajutsu.common.agents.factory import make_enrichment_agent
        from bajutsu.common.ai import credential_gap

        eff = resolve(config, target)
        gap = credential_gap(eff.ai)
        if gap:
            # The phrased message, not the raw token: `ai.provider: none` (BE-0394) is a deliberate
            # setting, so "requires a credential" would send the reader to Settings to save a key
            # that can never lift it. The same mapping `claudeHint` renders.
            return {"error": f"enrichment cannot run: {ai_availability.message(gap, eff.ai)}"}, 400
        agent = make_enrichment_agent(ai=eff.ai)
    else:
        agent = agent_factory()

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
    effective = _session_effective(state, config, target, session, state.org_of(actor))
    driver, teardown = factory(effective, backends_list, udid)

    from bajutsu.common.agents.enrich import enrich

    try:
        proposal = enrich(driver, matched, agent, with_screenshot=False)
    finally:
        # Tears down whatever backs the driver — for XCUITest the `xcodebuild` runner subprocess,
        # which a plain `close()` would leave running (BE-0290).
        teardown()

    return {
        "ok": True,
        "expect": [a.model_dump(exclude_none=True, by_alias=True) for a in proposal.expect],
        "settle": (
            proposal.settle.model_dump(exclude_none=True, by_alias=True)
            if proposal.settle
            else None
        ),
        "note": proposal.note,
    }, 200
