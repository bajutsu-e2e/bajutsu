"""Run / record / crawl dispatch serve operations (BE-0127)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bajutsu.serve import oplog
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.helpers import (
    _int,
    crawl_command,
    record_command,
    run_command,
    target_build_info,
    valid_run_id,
)
from bajutsu.serve.jobs import Job, ServeState
from bajutsu.serve.operations._common import _device_args, _resolve_org_or_forbid

_logger = logging.getLogger("bajutsu.serve.operations")


def _boot_targets(udid: str) -> list[str]:
    """The concrete devices to boot before a run/record/crawl. Picked devices are booted (and
    waited on) first; the "booted" alias names whatever is already up, so it's not a boot target."""
    return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]


def _bool_flag(body: dict[str, Any], key: str) -> bool | None:
    """A tri-state flag from the request body: True/False when explicitly set, else None (so the
    spawned CLI applies the scenario/CLI default rather than being forced either way)."""
    value = body.get(key)
    return value if isinstance(value, bool) else None


def _register_and_dispatch(
    state: ServeState, job: Job
) -> tuple[Job | None, tuple[Any, int] | None]:
    """Register *job* under the concurrency cap and dispatch it, the tail shared by every start_*
    endpoint. Returns ``(job, None)`` once dispatched, or ``(None, (error, 429))`` when the cap is
    hit. The atomic count+create in `try_register` is what keeps concurrent dispatches under the cap."""
    registered = state.try_register(job)
    if registered is None:
        oplog.log_event(
            _logger,
            "quota.rejected",
            "concurrency cap hit; job rejected",
            org=job.org,
            actor=job.actor,
        )
        return None, ({"error": "too many concurrent jobs; try again shortly"}, 429)
    state.executor.dispatch(state, registered)
    oplog.log_event(
        _logger,
        "run.dispatched",
        "job dispatched",
        job_id=registered.id,
        org=registered.org,
        actor=registered.actor,
    )
    return registered, None


def start_run(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("scenario") or not body.get("target"):
        return {"error": "scenario and target are required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    # Confine the scenario to the target's own scenarios dir: a serve client must not be able to run an
    # arbitrary file path on the host (BE-0051 / BE-0015 / BE-0016 prerequisite). The scenario store
    # is scoped to the actor's org so the run reads that org's scenarios.
    scope = state.for_org(org).scenarios.scope(target)
    if scope is None:
        return {"error": f"target '{target}' has no scenarios dir"}, 400
    # The store resolves the client value to a trusted runnable — never the client string — so no
    # client-controlled value reaches a filesystem path (BE-0051 arbitrary-path guard). On the
    # server backend it also carries the scenario text as `materials` for a remote worker.
    runnable = scope.runnable(str(body["scenario"]))
    if runnable is None:
        return {
            "error": "scenario must be an existing .yaml inside the target's scenarios dir"
        }, 400
    backend, udid, err = _device_args(body)
    if err:
        return err
    # When the scenario ships as materials (server backend), the worker has no project on disk, so
    # the config travels too and the run uses workspace-relative paths; locally nothing materializes
    # and the run uses the real config / baselines paths.
    materials = dict(runnable.materials)
    on_worker = bool(materials)
    config_arg = "bajutsu.config.yaml" if on_worker else str(cfg)
    if on_worker:
        materials[config_arg] = cfg.read_text(encoding="utf-8")
    # A config bound from a different cwd — a Git checkout (BE-0063) or an uploaded bundle (BE-0073) —
    # runs from that tree, so a run would otherwise write its output under the transient checkout/
    # bundle. Point --runs-dir at serve's own store (an absolute path under base_cwd) so the run lands
    # in history and survives the bundle being replaced. Local path only — a server-backend run
    # materializes into a worker workspace instead (on_worker).
    runs_dir = (
        str((state.base_cwd / state.runs_dir).resolve())
        if not on_worker and state.cwd != state.base_cwd
        else ""
    )
    # On the worker, baselines are downloaded into a workspace-relative dir before the run (the
    # control plane's baselines live in object storage); locally the real dir is used directly.
    cmd = run_command(
        runnable.arg,
        target,
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        erase=_bool_flag(body, "erase"),
        dismiss_alerts=_bool_flag(body, "dismissAlerts"),
        config=config_arg,
        # An uploaded bundle is self-contained: omit --baselines so its config's `baselines` drives
        # (resolved against the bundle cwd), like the rest of its relative paths (BE-0073).
        baselines=""
        if state.upload is not None
        else ("baselines" if on_worker else str(state.baselines_dir)),
        headed=_bool_flag(body, "headed"),
        runs_dir=runs_dir,
        # Govern the uploaded bundle's launchServer command (BE-0090); a local/Git config is
        # operator-trusted and ungoverned, so it gets no flag.
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, target)
    if state.upload is not None:
        # An uploaded bundle ships a prebuilt binary; never run its (untrusted) `build` command on the
        # host — DESIGN §1 "Bajutsu does not build the app" (BE-0073). appPath was confined to the
        # bundle at bind. The bundle's provenance is stamped into the run's manifest after it finishes.
        build = None
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            materials=materials,
            materialize_baselines=on_worker,
            provenance=state.upload.provenance if state.upload is not None else None,
            actor=actor,
            org=org,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(
        state, actor, org, "run", f"{target}/{body['scenario']}", {"backend": backend or None}
    )
    return {"jobId": job.id}, 200


def start_record(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Author a scenario from a natural-language goal (the Record tab).  The authored file lands in
    the selected target's configured scenarios dir."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("goal") or not body.get("target"):
        return {"error": "goal and target are required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    scope = state.for_org(org).scenarios.scope(target)
    if scope is None:
        return {"error": f"target '{body['target']}' has no scenarios dir"}, 400
    authored = scope.authored(str(body.get("name") or "generated"))
    # Validate the device args the same way start_run does (BE-0051): no free-text backend or udid
    # reaches the spawned `bajutsu record` argv. The output path is confined by `authored` above.
    backend, udid, err = _device_args(body)
    if err:
        return err
    # On the server backend (authored.save set) the worker has no project on disk: ship the config
    # and use workspace-relative --out / --config; the worker persists the authored file afterward.
    on_worker = authored.save is not None
    materials: dict[str, str] = {}
    config_arg = str(cfg)
    if on_worker:
        config_arg = "bajutsu.config.yaml"
        materials[config_arg] = cfg.read_text(encoding="utf-8")
    cmd = record_command(
        authored.out,
        body["target"],
        str(body["goal"]),
        agent=body.get("agent", ""),
        backend=backend,
        udid=udid,
        erase=_bool_flag(body, "erase"),
        dismiss_alerts=_bool_flag(body, "dismissAlerts"),
        headed=_bool_flag(body, "headed"),
        config=config_arg,
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, body["target"])
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            out_path=authored.out,
            materials=materials,
            record_save=authored.save,
            actor=actor,
            org=org,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(state, actor, org, "record", str(body["target"]), {"goal": str(body["goal"])})
    # Report the saved ref on the server (what the UI loads), else the on-disk path.
    return {"jobId": job.id, "path": authored.save[1] if authored.save else authored.out}, 200


def start_crawl(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Explore a target breadth-first and build a screen map (the Crawl tab).  The screen map is
    streamed into ``runs/<runId>/screenmap.json``; the returned ``runId`` lets the UI poll it."""
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    # Resume continues an existing run (a pruned branch tapped in the UI); otherwise a new run.
    resume_src = str(body.get("resumeSrc", "") or "")
    resume_key = str(body.get("resumeKey", "") or "")
    resuming = bool(resume_src and resume_key and body.get("runId"))
    run_id = str(body["runId"]) if resuming else datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    # A resumed crawl takes runId from the client; reject anything but a safe path segment so
    # `runs_dir / run_id` (the crawl's --out) can't escape runs_dir (BE-0051).
    if resuming and not valid_run_id(run_id):
        return {"error": "invalid runId"}, 400
    backend, udid, err = _device_args(body)
    if err:
        return err
    cmd = crawl_command(
        target,
        out=str(state.runs_dir / run_id),
        agent=body.get("agent", ""),
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        max_screens=_int(body.get("maxScreens"), 50),
        max_steps=_int(body.get("maxSteps"), 200),
        erase=_bool_flag(body, "erase"),
        dismiss_alerts=_bool_flag(body, "dismissAlerts"),
        headed=_bool_flag(body, "headed"),
        config=str(cfg),
        resume_src=resume_src if resuming else "",
        resume_key=resume_key if resuming else "",
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, target)
    # Cap concurrency like run/record: crawl is long and device-heavy (BE-0051 slice 5).
    job, capped = _register_and_dispatch(
        state,
        Job(
            cmd=cmd,
            udids=_boot_targets(udid),
            app_path=app_path,
            build=build,
            actor=actor,
            org=org,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(state, actor, org, "crawl", target, {"runId": run_id})
    return {"jobId": job.id, "runId": run_id}, 200
