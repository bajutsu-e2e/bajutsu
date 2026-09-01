"""Run / record / crawl dispatch serve operations (BE-0127)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from bajutsu.cloud.devicefarm import Platform
from bajutsu.report.manifest import MAX_LABEL_LENGTH
from bajutsu.run_id import new_run_id
from bajutsu.serve import oplog
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.batch_provider import BatchRequest
from bajutsu.serve.commands import _float, _int, crawl_command, record_command, run_command
from bajutsu.serve.helpers import (
    target_batch_info,
    target_build_info,
    target_capabilities,
    valid_relative_key,
    valid_run_id,
)
from bajutsu.serve.operations._common import _device_args, _resolve_org_or_forbid
from bajutsu.serve.operations.config import launch_label, resolve_provider_env
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


def _run_label(
    state: ServeState, body: dict[str, Any]
) -> tuple[str | None, tuple[Any, int] | None]:
    """The run-history label to stamp, and the 400 to return when the request's override is invalid.

    Defaults to the label the launcher derives from the bound config, which is what partitions two
    configs a deployment is restarted between (BE-0404 unit 2). The label is opaque: never parsed,
    never matched against config, never consulted by authorization — only its length is checked.
    """
    raw = body.get("label")
    if raw is not None:
        if not isinstance(raw, str):
            return None, ({"error": "label must be a string"}, 400)
        override = raw.strip()
        if len(override) > MAX_LABEL_LENGTH:
            return None, ({"error": f"label must be at most {MAX_LABEL_LENGTH} characters"}, 400)
        if override:
            return override, None
    if state.config is None:
        return None, None
    # Bounded, unlike the override above: this default is spawned as `run --label`, whose own guard
    # would refuse an over-long value and fail every run from that deployment with a usage error for
    # a label the operator never typed. "Refuse, never truncate" protects an operator's own input —
    # a value the tool derived from a long file stem or a deep in-repo path is the tool's to trim.
    return launch_label(state.config, state.config_provenance)[:MAX_LABEL_LENGTH], None


def _boot_targets(udid: str) -> list[str]:
    """The concrete devices to boot before a run/record/crawl. Picked devices are booted (and
    waited on) first; the "booted" alias names whatever is already up, so it's not a boot target."""
    return [u.strip() for u in udid.split(",") if u.strip() and u.strip() != "booted"]


def _escapes(rel_path: str) -> bool:
    """Whether a `relpath`-computed package path climbs out of the run directory (a leading ``..``).

    The batch provider packages the run directory at the package root, so a path outside it can't be
    packaged; the fan-out rejects it loudly rather than shipping a `../…` the cloud host can't find.
    """
    return rel_path == os.pardir or rel_path.startswith(os.pardir + os.sep)


def _bool_flag(body: dict[str, Any], key: str) -> bool | None:
    """A tri-state flag from the request body: True/False when explicitly set, else None (so the
    spawned CLI applies the scenario/CLI default rather than being forced either way)."""
    value = body.get(key)
    return value if isinstance(value, bool) else None


def _system_alert_handling_flag(
    body: dict[str, Any],
) -> tuple[bool | None, tuple[Any, int] | None]:
    """The `systemAlertHandling` request flag.

    The `alertHandling` (originally BE-0317) and `dismissAlerts` aliases this once also accepted were
    deleted with the schema aliases they mirrored (BE-0401). A request still naming one is rejected
    loudly, the same as every other layer's removed spellings, rather than silently dropped: `run`'s
    unset behaviour is per-scenario "on", so dropping a caller's `{removed: false}` would arm the
    guard on a request that asked to disable it. Returns ``(value, None)`` or ``(None, (error, 400))``.

    Shared by `start_run`, `start_record`, and `start_crawl`, so it holds only what all three
    reject. `alertVisionInstruction` is `run`'s alone (`_reject_run_vision_instruction` below):
    `record` and `crawl` keep the vision guard that reads it.
    """
    for removed in ("alertHandling", "dismissAlerts"):
        if removed in body:
            return None, ({"error": f"'{removed}' was removed; use 'systemAlertHandling'"}, 400)
    return _bool_flag(body, "systemAlertHandling"), None


def _run_alert_flags(
    body: dict[str, Any],
) -> tuple[bool | None, tuple[Any, int] | None]:
    """`run`'s alert-key checks: the shared `systemAlertHandling` flag, then its own refusal.

    One entry point so `start_run` spends a single early return on the pair — `record` / `crawl` call
    `_system_alert_handling_flag` alone, since the refusal below is `run`'s.
    """
    value, err = _system_alert_handling_flag(body)
    if err:
        return None, err
    return value, _reject_run_vision_instruction(body)


def _reject_run_vision_instruction(body: dict[str, Any]) -> tuple[Any, int] | None:
    """Refuse an `alertVisionInstruction` on a `run` request, or None when the body carries none.

    BE-0402 retired the flag this key rendered. Dropping it silently would leave the HTTP API the one
    entry point that inverts a caller's intent without saying so: a request sending "tap Allow" to
    *grant* a permission would fall through to the built-in dismissive labels and deny it, which is
    exactly the outcome the scenario and target-config layers now exit 2 to prevent. `run` only —
    `record` and `crawl` still take the free-text form on their own `--alert-vision-instruction`
    flag, so a body naming this key must not fail their jobs (serve does not surface it to them yet,
    so it is simply unused there).
    """
    if "alertVisionInstruction" not in body:
        return None
    return {
        "error": "'alertVisionInstruction' is not supported by run (BE-0402); "
        "name the buttons with 'alertLabels', or write the scenario's own "
        "systemAlertHandling.rules"
    }, 400


def _request_device_budget(
    body: dict[str, Any],
) -> tuple[int | None, tuple[Any, int] | None]:
    """The per-request ``deviceBudget`` override, validated (BE-0336 Unit 4). None when absent (no
    override). A present value must be a positive integer: a resource cap the caller asked for must
    fail loudly with a 400 rather than silently evaporate into "unbounded" (determinism first), so a
    non-int, a float, a bool, or a non-positive value is rejected. Returns ``(budget, None)`` or
    ``(None, (error, 400))``."""
    raw = body.get("deviceBudget")
    if raw is None:
        return None, None
    # bool is an int subclass, so guard it out first — a boolean device budget is a client error, not
    # the integer 1. A float ("1.9") is likewise rejected rather than silently truncated.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None, ({"error": "deviceBudget must be a positive integer"}, 400)
    return raw, None


def _device_budget(config_budget: int | None, request_budget: int | None) -> int:
    """The effective cloud-batch device budget K for a fan-out: the more restrictive of the target's
    configured budget and a validated per-request override (BE-0336 Unit 4). A request may only
    *lower* the budget, never raise it — so this is the minimum of the two, treating None on either
    side as "no bound from that side". Both are positive when set (config via schema `gt=0`, request
    via `_request_device_budget`). Returns 0 (unbounded, the registry's convention) when neither
    side sets a cap."""
    candidates = [b for b in (config_budget, request_budget) if b is not None]
    return min(candidates) if candidates else 0


def _register_and_dispatch(
    state: ServeState, job: Job, *, device_budget: int = 0
) -> tuple[Job | None, tuple[Any, int] | None]:
    """Register *job* under the concurrency cap and dispatch it, the tail shared by every start_*
    endpoint. Returns ``(job, None)`` once dispatched, or ``(None, (error, 429))`` when the cap is
    hit. The atomic count+create in `try_register` is what keeps concurrent dispatches under the cap.
    *device_budget* is the per-target cloud-batch device cap (BE-0336 Unit 4), 0 for the local run
    paths that reserve no cloud device."""
    # Resolve the requesting org's AI provider selection into this job's env overlay (BE-0229), so
    # the spawn uses that org's provider/model/effort without the serve process mutating its shared
    # os.environ. Empty when no provider is selected (the zero-config path). Travels in the job spec,
    # so a remote worker needs no settings of its own. Done here — the single tail every start_*
    # endpoint (run / record / crawl / triage) funnels through — so every AI-capable job is covered.
    job.env_overlay = resolve_provider_env(state, job.org)
    registered = state.try_register(job, device_budget=device_budget)
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
    system_alert_handling, alert_err = _run_alert_flags(body)
    if alert_err:
        return alert_err
    label, label_err = _run_label(state, body)
    if label_err:
        return label_err
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
        label=label or "",
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        erase=_bool_flag(body, "erase"),
        system_alert_handling=system_alert_handling,
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
        alert_labels=str(body.get("alertLabels") or ""),
        alert_poll_interval=_float(body.get("alertPollInterval")),
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
            # Resolve the label once, at enqueue, and carry it on the job (BE-0404 unit 2): the run
            # keeps the partition it started under even if `serve` is rebound before it finishes, and
            # a remote worker's `_persist_run` stamps it without consulting anything.
            label=label,
            # A cancelled `run` is finished cooperatively so it still lands in the history as a failed
            # run (BE-0370). Only a `run` job declares this: it is the only kind with a pass/fail
            # verdict and a manifest to preserve.
            graceful_cancel=True,
        ),
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(
        state, actor, org, "run", f"{target}/{body['scenario']}", {"backend": backend or None}
    )
    return {"jobId": job.id}, 200


# Each return is a distinct HTTP status from a validation guard — the early-return shape RET505
# itself asks for (BE-0386) — and each branch is one more such guard, not tangled logic.
def start_run_set(  # noqa: PLR0911, PLR0912
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Fan out a scenario-set request into one cloud-batch job per scenario (BE-0336 Unit 3).

    A neutral dispatch surface: the request names a `target` and, optionally, a `scenarios` subset
    (omitted = every scenario in the target's dir). The target's config declares which batch provider
    its cloud runs use (`cloudBatch`); the request never names a provider, so the executor kind is a
    config decision, not a client one. Each scenario becomes its own Job carrying a per-scenario
    `BatchRequest`, registered and dispatched through the same concurrency-capped tail as every other
    run. Returns the dispatched job ids. The device budget that bounds how many of these reserve a
    device at once arrives in a later unit; here the fan-out simply enumerates and registers.
    """
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    if not body.get("target"):
        return {"error": "target is required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    scope = state.for_org(org).scenarios.scope(target)
    if scope is None:
        return {"error": f"target '{target}' has no scenarios dir"}, 400
    provider, platform, app_path, config_budget = target_batch_info(cfg, target)
    if not provider:
        return {
            "error": f"target '{target}' is not configured for cloud-batch runs (set cloudBatch)"
        }, 400
    if platform not in ("android", "ios"):
        return {"error": f"cloud-batch runs support android or ios, not {platform!r}"}, 400
    batch_platform: Platform = "android" if platform == "android" else "ios"
    if not app_path:
        return {"error": f"target '{target}' has no appPath to install on the cloud device"}, 400
    # Which scenarios to fan out: the requested subset, or every scenario in the target's dir.
    requested = body.get("scenarios")
    if requested is None:
        names = [entry["file"] for entry in scope.list()]
    elif isinstance(requested, list) and all(isinstance(s, str) for s in requested):
        names = requested
    else:
        return {"error": "scenarios must be a list of scenario file names"}, 400
    if not names:
        return {"error": f"target '{target}' has no scenarios to run"}, 400
    # Resolve every scenario to its trusted runnable *before* dispatching any, so an unknown name
    # fails the whole request closed rather than leaving a partial fan-out behind (BE-0051 confines
    # each to the target's own dir).
    # The provider packages work_dir at the zip root, so the config and scenarios below it travel as
    # package-relative paths; devicefarm_package_root roots that at the source tree (see its field),
    # falling back to state.cwd for the in-process tests.
    work_dir = state.devicefarm_package_root or state.cwd
    # A relative appPath resolves against the config's own directory (state.cwd) like every other
    # config path (BE-0242) — not against the package root or serve's process cwd. The batch provider
    # reads the APK/IPA in-process, against the process cwd, so resolve it to an absolute path here —
    # otherwise a relative appPath is read from serve's launch dir and the upload fails opaquely on
    # the cloud host with "No such file". An absolute appPath is left as the operator wrote it.
    app_path = app_path if Path(app_path).is_absolute() else str(state.cwd / app_path)
    config_arg = os.path.relpath(cfg, work_dir)
    if _escapes(config_arg):
        # The provider packages work_dir at the package root, so a config outside it would travel as
        # a `../…` path that won't exist in the package and would fail opaquely on the cloud host.
        # Fail loud here instead (determinism first): this target's config isn't under work_dir.
        return {
            "error": (
                f"config is not under the cloud-batch package root {work_dir}; "
                "the config and its scenarios must live inside that directory"
            )
        }, 400
    requests: list[BatchRequest] = []
    for name in names:
        runnable = scope.runnable(name)
        if runnable is None:
            return {
                "error": f"scenario '{name}' must be an existing .yaml inside the target's scenarios dir"
            }, 400
        if runnable.materials:
            # The batch provider packages work_dir at the zip root; a scenario whose text travels as
            # out-of-band materials (the server-backed store) can't be packaged from an on-disk path.
            return {
                "error": (
                    f"scenario '{name}' is not an on-disk file — cloud-batch fan-out requires "
                    "scenarios to be on the local filesystem (materials not supported)"
                )
            }, 400
        scenario_arg = os.path.relpath(runnable.arg, work_dir)
        if _escapes(scenario_arg):
            return {"error": f"scenario '{name}' is not under the run directory"}, 400
        requests.append(
            BatchRequest(
                provider=provider,
                scenario=scenario_arg,
                target=target,
                config=config_arg,
                platform=batch_platform,
                app_path=app_path,
            )
        )
    label, label_err = _run_label(state, body)
    if label_err:
        return label_err
    # The device budget K bounds how many of this target's runs reserve a device at once (BE-0336
    # Unit 4): the target's configured `cloudBatchBudget`, which a per-request `deviceBudget` may
    # lower. Keyed on the batch provider (the device pool) inside `try_register`, so the (K+1)th job
    # is rejected and the fan-out stops there.
    request_budget, budget_err = _request_device_budget(body)
    if budget_err:
        return budget_err
    device_budget = _device_budget(config_budget, request_budget)
    dispatched: list[str] = []
    for request in requests:
        job, capped = _register_and_dispatch(
            state,
            Job(batch=request, actor=actor, org=org, label=label),
            device_budget=device_budget,
        )
        if capped:
            # The concurrency cap (global/per-user/per-org) or the device budget stops the fan-out
            # here; a partially-dispatched set is the routine outcome of the device budget rather than
            # an error (BE-0336 Unit 4). But if the cap rejected the *first* job, nothing was
            # dispatched — surface the 429 rather than returning a silent 200 with an empty jobIds
            # list (like the other start_* endpoints).
            if not dispatched:
                return capped
            break
        assert job is not None
        dispatched.append(job.id)
    _record_audit(state, actor, org, "run-set", target, {"count": len(dispatched)})
    return {"jobIds": dispatched}, 200


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
    system_alert_handling, alert_err = _system_alert_handling_flag(body)
    if alert_err:
        return alert_err
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
        system_alert_handling=system_alert_handling,
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
    system_alert_handling, alert_err = _system_alert_handling_flag(body)
    if alert_err:
        return alert_err
    cmd = crawl_command(
        target,
        out=str(state.runs_dir / run_id),
        backend=backend,
        udid=udid,
        workers=_int(body.get("workers"), 1),
        max_screens=_int(body.get("maxScreens"), 50),
        max_steps=_int(body.get("maxSteps"), 200),
        erase=_bool_flag(body, "erase"),
        system_alert_handling=system_alert_handling,
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
