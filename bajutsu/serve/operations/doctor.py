"""Doctor / preflight serve operations (BE-0024; convention score added in BE-0148)."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from bajutsu import adb, device_errors, doctor, simctl
from bajutsu.backends import IMPLEMENTED, resolve_actuators
from bajutsu.config import (
    Effective,
    android_package,
    ios_bundle_id,
    load_config,
    resolve,
    web_base_url,
    web_engine,
)
from bajutsu.drivers import base
from bajutsu.serve.operations._common import _device_args
from bajutsu.serve.state import ServeState

# (actuator, udid, effective config) -> the current screen's elements. Injectable so the score
# path is testable without a device — the device query is the one external dependency here.
ScreenQuery = Callable[[str, str, Effective], list[base.Element]]


def doctor_check(
    state: ServeState,
    body: dict[str, Any],
    *,
    actor: str | None = None,
    screen_query: ScreenQuery | None = None,
) -> tuple[Any, int]:
    """Run doctor for a target: config validation, tool runnability, and the screen's convention score.

    Returns structured JSON so the web UI can show a readiness panel before a run — the same
    checks the CLI ``bajutsu doctor`` runs. The ``ok`` top-level boolean is true only when every
    runnability/config check passed. The ``score`` (Ready / Partial / Blocked, with the per-id
    gaps ``doctor.score`` computes) is present only when the environment is runnable — mirroring
    the CLI, which never queries a screen it can't reach; otherwise it is ``None``. Deterministic
    and AI-free: it inspects the environment and scores a screen, never a run verdict.

    Args:
        body: ``{target, udid?, backend?}``. ``udid`` / ``backend`` are validated against the
            device allow-lists (BE-0051) before selecting the actuator or reaching a driver.
        screen_query: overrides the live screen query for testing; defaults to the real one.
    """
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])

    backend, udid, err = _device_args(body)
    if err is not None:
        return err

    config = load_config(cfg.read_text(encoding="utf-8"))
    target_cfg = config.targets.get(target)
    if target_cfg is None:
        return {"error": f"unknown target: {target}"}, 400

    eff = resolve(config, target)

    # Resolve the *intended* actuator without requiring it to be installed — select_actuator
    # raises when the tool is absent, but doctor's purpose is to *report* what's missing, so we
    # resolve the first implemented actuator and let the runnability checks surface the absent
    # tool. A request-level `backend` overrides the config's list (the CLI's `--backend`), so a
    # user can diagnose a target against a specific backend. Filtering against IMPLEMENTED (not
    # KNOWN_ACTUATORS) avoids picking a planned-but-unimplemented backend whose preflight would
    # fall through to generic checks.
    # `backend` may be a comma-list (valid_backend accepts "idb,fake"); split it as the CLI does so
    # resolve_actuators expands individual tokens rather than treating the whole string as one name.
    backends_list = [b.strip() for b in backend.split(",") if b.strip()] if backend else eff.backend
    implemented = [a for a in resolve_actuators(backends_list) if a in IMPLEMENTED]
    if not implemented:
        return {"error": f"no implemented backend among {backends_list}"}, 400
    actuator = implemented[0]

    from bajutsu import preflight

    cfg_checks = preflight.config_checks(
        actuator,
        target=target,
        bundle_id=ios_bundle_id(eff),
        base_url=web_base_url(eff),
        package=android_package(eff),
    )

    # Report the device probe too (Android counts attached adb devices, the iOS backends booted
    # Simulators), so a target with no device/emulator is not reported `ok: true` — the same
    # `booted_count` the CLI doctor passes. Web / fake ignore it inside `runnability`.
    def booted_count() -> int:
        if actuator == "adb":
            return len(adb.booted_serials())
        # Reads booted state through state.simctl so this never shells out on a host without
        # Xcode (and so tests can inject the device list). Only the iOS family uses it.
        return len(simctl.booted_udids(run=state.simctl))

    # The shared assembly gives the panel the same env checks the CLI reports (BE-0199).
    env_checks = preflight.doctor_environment_checks(
        actuator,
        booted_count=booted_count,
        web_engine=web_engine(eff),
    )
    all_checks = cfg_checks + env_checks
    ok = preflight.passed(all_checks)

    # The convention score needs a live screen, so it is attempted only once the environment is
    # runnable — querying a device the runnability gate already failed would only crash. When the
    # gate fails, the score is None and the panel shows the runnability remedy instead (BE-0148).
    #
    # Runnability proves the *tools* are present, not that the screen is actually reachable: a web
    # target passes it whenever Playwright + the browser are installed, even with its app server
    # down, so navigating the baseUrl can still fault (ERR_CONNECTION_REFUSED). Report that as a
    # failed check rather than letting the probe crash doctor — doctor's job is to *report* what is
    # wrong, and a stack trace reports nothing. score stays None and ok flips false, so the panel
    # shows the reason instead of a 500.
    score = None
    if ok:
        query = screen_query or functools.partial(_current_screen, state)
        try:
            elements = query(actuator, udid, eff)
        except device_errors.DeviceError as e:
            all_checks = [*all_checks, preflight.Check("screen readable", False, str(e))]
            ok = False
        else:
            score = _serialize_score(
                doctor.score(
                    elements,
                    eff.id_namespaces,
                    ok_coverage=eff.doctor_thresholds.ok_coverage,
                    fail_coverage=eff.doctor_thresholds.fail_coverage,
                )
            )

    return {
        "ok": ok,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in all_checks],
        "score": score,
        "target": target,
        "backend": actuator,
    }, 200


def _serialize_score(s: doctor.Score) -> dict[str, Any]:
    """The convention score as JSON — the per-id gaps drive the UI's "what to fix" list."""
    return {
        "grade": s.grade,
        "idCoverage": s.id_coverage,
        "actionable": s.actionable,
        "withId": s.with_id,
        "namespaceConformance": s.namespace_conformance,
        "duplicateIds": s.duplicate_ids,
        "noActionable": s.no_actionable,
        "missingId": [{"label": e["label"] or "", "traits": e["traits"]} for e in s.missing_id],
        "offNamespace": s.off_namespace,
        "duplicates": s.duplicates,
    }


def _current_screen(
    state: ServeState, actuator: str, udid: str, eff: Effective
) -> list[base.Element]:
    """The elements of the screen to score — the shared probe, routed through ``state.simctl``.

    Wraps ``doctor.probe_screen`` (BE-0199) so simctl never shells out on a host without Xcode,
    and maps its config error to serve's existing ``ValueError`` surface.
    """
    try:
        return doctor.probe_screen(actuator, udid, eff, simctl_run=state.simctl)
    except doctor.DoctorProbeError as e:
        raise ValueError(str(e)) from e
