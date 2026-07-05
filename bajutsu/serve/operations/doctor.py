"""Doctor / preflight serve operations (BE-0024, split out in BE-0127)."""

from __future__ import annotations

from typing import Any

from bajutsu.backends import IMPLEMENTED, resolve_actuators
from bajutsu.config import ios_bundle_id, load_config, resolve, web_base_url, web_engine
from bajutsu.serve.jobs import ServeState


def doctor_check(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Run preflight environment checks for a target: config validation + tool runnability.

    Returns structured JSON so the web UI can show a health-check panel before a run —
    the same checks the CLI ``bajutsu doctor`` runs, minus the live screen score (which
    needs a device connection the web UI might not have yet). The ``ok`` top-level boolean
    is true only when every individual check passed.
    """
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    eff = resolve(config, target)

    # Resolve the *intended* actuator without requiring it to be installed — select_actuator
    # raises when the tool is absent, but doctor's purpose is to *report* what's missing, so we
    # resolve the first implemented actuator from the backends list and let the runnability
    # checks surface the absent tool.  Filtering against IMPLEMENTED (not KNOWN_ACTUATORS)
    # avoids picking a planned-but-unimplemented backend (e.g. adb) whose preflight would
    # fall through to generic checks.
    actuators = resolve_actuators(eff.backend)
    implemented = [a for a in actuators if a in IMPLEMENTED]
    if not implemented:
        return {"error": f"no implemented backend among {eff.backend}"}, 400
    actuator = implemented[0]

    from bajutsu import preflight

    cfg_checks = preflight.config_checks(
        actuator,
        target=target,
        bundle_id=ios_bundle_id(eff),
        base_url=web_base_url(eff),
    )
    env_checks = preflight.runnability(actuator, web_engine=web_engine(eff))
    all_checks = cfg_checks + env_checks

    serialized = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in all_checks]
    return {
        "ok": preflight.passed(all_checks),
        "checks": serialized,
        "target": target,
        "backend": actuator,
    }, 200
