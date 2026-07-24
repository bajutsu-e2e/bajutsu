"""Run / record / crawl dispatch serve operations (BE-0127)."""

from __future__ import annotations

import logging
from typing import Any

from bajutsu.run_id import new_run_id
from bajutsu.serve import oplog
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.commands import _int, crawl_command, record_command, run_command
from bajutsu.serve.helpers import (
    target_build_info,
    target_capabilities,
    valid_relative_key,
    valid_run_id,
)
from bajutsu.serve.operations._common import _device_args, _resolve_org_or_forbid
from bajutsu.serve.operations.config import resolve_provider_env
from bajutsu.serve.state import Job, ServeState

_logger = logging.getLogger("bajutsu.serve.operations")


def _governed_build(state: ServeState, build: str | None) -> str | None:
    """The `build:` command serve may run for the active config, or None when it's untrusted.

    An uploaded bundle ships a prebuilt binary, so its build never runs on the host — DESIGN §1
    "Bajutsu does not build the app" (BE-0073). A Git config bound at runtime via the API is equally
    untrusted (a cross-origin request could have bound it), so its build is nulled too unless the
    operator opted in with --allow-remote-build (BE-0121). A local or startup-bound config is
    operator-trusted and keeps its build.
    """
    if state.upload is not None:
        return None
    if state.git_config_from_api and not state.allow_remote_build:
        return None
    return build


def _active_project_id(state: ServeState, org: str) -> str | None:
    """The org's active project id, resolved at enqueue so it travels with the job (BE-0225 unit 3).

    None when no hub is wired or no project is active. Guarded like `_persist_run`: resolving reaches
    the registry backend (a database for `SqlProjectRegistry`), so a flaky backend leaves the run
    unlabeled rather than failing the dispatch it never used to touch.
    """
    registry = state.project_registry
    if registry is None:
        return None
    try:
        active = registry.resolve_active(org_id=org)
    except Exception:
        _logger.warning("failed to resolve the active project at enqueue", exc_info=True)
        return None
    return active.id if active is not None else None


def _boot_targets(udid: str) -> list[str]:
    """The concrete devices to boot before a run/record/crawl. Picked devices are booted (and
    waited on) first; the "booted" alias names whatever is already up, so it's not a boot target."""
    return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]


def _bool_flag(body: dict[str, Any], key: str) -> bool | None:
    """A tri-state flag from the request body: True/False when explicitly set, else None (so the
    spawned CLI applies the scenario/CLI default rather than being forced either way)."""
    value = body.get(key)
    return value if isinstance(value, bool) else None


def _alert_handling_flag(body: dict[str, Any]) -> bool | None:
    """The `alertHandling` request flag, accepting the deprecated `dismissAlerts` key (BE-0317)."""
    canonical = _bool_flag(body, "alertHandling")
    return canonical if canonical is not None else _bool_flag(body, "dismissAlerts")


def _register_and_dispatch(
    state: ServeState, job: Job
) -> tuple[Job | None, tuple[Any, int] | None]:
    """Register *job* under the concurrency cap and dispatch it, the tail shared by every start_*
    endpoint. Returns ``(job, None)`` once dispatched, or ``(None, (error, 429))`` when the cap is
    hit. The atomic count+create in `try_register` is what keeps concurrent dispatches under the cap."""
    # Resolve the requesting org's AI provider selection into this job's env overlay (BE-0229), so
    # the spawn uses that org's provider/model/effort without the serve process mutating its shared
    # os.environ. Empty when no provider is selected (the zero-config path). Travels in the job spec,
    # so a remote worker needs no settings of its own. Done here — the single tail every start_*
    # endpoint (run / record / crawl / triage) funnels through — so every AI-capable job is covered.
    job.env_overlay = resolve_provider_env(state, job.org)
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
    # Always point --runs-dir at serve's own store (`state.runs_dir`, absolutized at launch in
    # ServeState), so the run writes exactly where the store, `jobs`, and `triage` read — never the
    # run's cwd-relative default. That default diverges from the store whenever the run's cwd isn't
    # the launch dir: a Git checkout / uploaded bundle (BE-0063/BE-0073), or a subdir config that
    # repoints `cwd` to the config's dir (BE-0242) and would otherwise strand the report as
    # not-found. Local path only — a server-backend run materializes into a worker workspace instead.
    runs_dir = "" if on_worker else str(state.runs_dir)
    # On the worker, baselines are downloaded into a workspace-relative dir before the run (the
    # control plane's baselines live in object storage); locally the real dir is used directly.
    cmd = run_command(
        runnable.arg,
        target,
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        erase=_bool_flag(body, "erase"),
        alert_handling=_alert_handling_flag(body),
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
        # The rest of `run`'s flag surface, now reachable from the request body (BE-0134). These are
        # safe to take from the client: tag selectors, the web engine axis (the CLI validates the
        # engine names), the network toggle, the post-verdict --zip, and the alert/log knobs.
        tag=str(body.get("tag") or ""),
        exclude=str(body.get("exclude") or ""),
        browser=str(body.get("browser") or ""),
        browsers=str(body.get("browsers") or ""),
        network=_bool_flag(body, "network"),
        zip_run=_bool_flag(body, "zip"),
        alert_instruction=str(body.get("alertInstruction") or ""),
        log_predicate=str(body.get("logPredicate") or ""),
        log_subsystem=str(body.get("logSubsystem") or ""),
        # Deliberately NOT sourced from the client body: --schemas / --goldens are host directory
        # paths, and taking them from a serve request is the arbitrary-path hole BE-0051 closes
        # (baselines is serve-computed above for the same reason); --config-offline /
        # --require-pinned-config govern how the operator-opened Git config is fetched. run_command
        # can emit all four (the flag surface stays complete), but they stay config-driven here.
    )
    app_path, build = target_build_info(cfg, target)
    build = _governed_build(state, build)
    # Per-run evidence-upload prefix (BE-0110): CI passes it to select the cloud lifecycle policy. It
    # becomes a storage key segment, so reject a non-string, a leading `/`, or `..` traversal here —
    # the same guard the upload-urls endpoint re-applies to the worker-relayed value.
    raw_prefix = body.get("evidence_prefix")
    if raw_prefix is not None and not isinstance(raw_prefix, str):
        return {"error": "evidence_prefix must be a string"}, 400
    evidence_prefix = raw_prefix or ""
    if not valid_relative_key(evidence_prefix, allow_empty=True):
        return {"error": "invalid evidence_prefix"}, 400
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
            evidence_prefix=evidence_prefix,
            capabilities=target_capabilities(cfg, target),
            # Resolve the active project once, at enqueue, and carry it on the job (BE-0225 unit 3):
            # this fixes the finish-time race and lets a remote worker's `_persist_run` stamp the run
            # without a registry. None when no hub is wired, leaving the run unlabeled as before.
            project_id=_active_project_id(state, org),
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
        backend=backend,
        udid=udid,
        erase=_bool_flag(body, "erase"),
        alert_handling=_alert_handling_flag(body),
        headed=_bool_flag(body, "headed"),
        config=config_arg,
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, body["target"])
    build = _governed_build(state, build)
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
            capabilities=target_capabilities(cfg, str(body["target"])),
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
    # Two ways to warm-start an existing run (both take its runId from the UI): resume one pruned
    # branch tapped in the graph, or continue the whole remaining frontier (BE-0181). They're
    # mutually exclusive — one names a single branch, the other means "everything left". Anything
    # else is a fresh run under a new timestamp id.
    resume_src = str(body.get("resumeSrc", "") or "")
    resume_key = str(body.get("resumeKey", "") or "")
    resuming = bool(resume_src and resume_key and body.get("runId"))
    # Parse `continue` as a strict boolean (only a literal JSON `true` counts), like the other
    # tri-state flags, so a stray string such as "false" can't read as truthy.
    wants_continue = _bool_flag(body, "continue") is True
    # `continue` names an existing run to pick up, so it's meaningless without a runId — reject that
    # rather than silently reinterpreting it as a fresh crawl (which would leave the user's target run
    # untouched with no error).
    if wants_continue and not body.get("runId"):
        return {"error": "continue requires the runId of the crawl to continue"}, 400
    continuing = wants_continue and bool(body.get("runId"))
    if resuming and continuing:
        return {"error": "resume and continue are mutually exclusive"}, 400
    reuse_run = resuming or continuing
    run_id = str(body["runId"]) if reuse_run else new_run_id()
    # A reused run takes runId from the client; reject anything but a safe path segment so
    # `runs_dir / run_id` (the crawl's --out) can't escape runs_dir (BE-0051).
    if reuse_run and not valid_run_id(run_id):
        return {"error": "invalid runId"}, 400
    backend, udid, err = _device_args(body)
    if err:
        return err
    cmd = crawl_command(
        target,
        out=str(state.runs_dir / run_id),
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        max_screens=_int(body.get("maxScreens"), 50),
        max_steps=_int(body.get("maxSteps"), 200),
        erase=_bool_flag(body, "erase"),
        alert_handling=_alert_handling_flag(body),
        headed=_bool_flag(body, "headed"),
        config=str(cfg),
        resume_src=resume_src if resuming else "",
        resume_key=resume_key if resuming else "",
        continue_crawl=continuing,
        upload_exec=state.upload_exec if state.upload is not None else "",
    )
    app_path, build = target_build_info(cfg, target)
    build = _governed_build(state, build)
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
            capabilities=target_capabilities(cfg, target),
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(state, actor, org, "crawl", target, {"runId": run_id})
    return {"jobId": job.id, "runId": run_id}, 200
