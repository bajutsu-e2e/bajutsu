"""Codegen serve operation (BE-0137).

Surfaces `bajutsu codegen` in the serve Web UI: turn a scenario into a native test (XCUITest
or Playwright) for copy / download. A structural mapping from the scenario model to target
syntax — deterministic, no device, no AI, and it never touches a verdict."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bajutsu.codegen_emit import EMIT_TARGETS, CodegenError, generate_test
from bajutsu.config import load_config, resolve
from bajutsu.scenario import load_scenarios
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.operations._common import _resolve_org_or_forbid


def generate_codegen(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Generate a native test from a scenario and return its source and derived filename.

    Args:
        body: ``{target, scenario, emit}`` — the config target, the scenario name inside that
            target's scenarios dir, and the emit format (``xcuitest`` / ``playwright``).

    Returns:
        ``({"code", "filename"}, 200)`` on success, or ``({"error"}, status)`` — the emit
        options offered track the target's backend, so an iOS target rejects ``playwright``
        rather than emitting a broken test (honest about limits, mirroring ``--emit``).
    """
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    if not body.get("scenario"):
        return {"error": "scenario is required"}, 400
    emit = str(body.get("emit") or "xcuitest")
    if emit not in EMIT_TARGETS:
        return {"error": f"unsupported emit: {emit} (one of {', '.join(EMIT_TARGETS)})"}, 400

    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden

    config = load_config(cfg.read_text(encoding="utf-8"))
    if config.targets.get(target) is None:
        return {"error": f"unknown target: {target}"}, 400

    # Confine the scenario to the target's own scenarios dir: a serve client resolves through the
    # org-scoped store, never a raw host path (BE-0051 / BE-0015).
    scope = state.for_org(org).scenarios.scope(target)
    scenario_text = scope.read(str(body["scenario"])) if scope else None
    if scenario_text is None:
        return {"error": "scenario not found"}, 404

    # A stored scenario may not currently parse (Save does not run full validation), so surface a
    # clean 400 instead of letting the parse error crash the request, as lint does.
    try:
        scenarios = load_scenarios(scenario_text)
    except Exception as exc:
        return {"error": f"could not parse scenario: {exc}"}, 400
    if not scenarios:
        return {"error": "no scenarios in file"}, 400

    eff = resolve(config, target)
    stem = Path(str(body["scenario"])).stem
    try:
        code, filename = generate_test(emit, scenarios, stem, eff)
    except CodegenError as exc:
        return {"error": str(exc)}, 400

    return {"code": code, "filename": filename}, 200
