"""One job the control plane is running or has run."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bajutsu.serve.logbus import LogBus

from ._shared import _DEFAULT_ORG

if TYPE_CHECKING:
    from bajutsu.serve.batch_provider import BatchRequest
    from bajutsu.serve.upload_artifacts import ArtifactOverrides


@dataclass
class Job:
    # `id` is assigned by `ServeState.register`/`try_register` (from the job sequence); a caller
    # builds a Job without one. The worker rebuilds a Job with the control-plane id passed in.
    id: str = ""
    cmd: list[str] = field(default_factory=list)
    udids: list[str] = field(default_factory=list)  # devices to boot before the run
    app_path: str | None = None  # built .app the run needs; built on demand if missing
    build: str | None = None  # shell command that builds app_path (None = no on-demand build)
    status: str = "running"  # running | done
    # Whether this job has given up its concurrency-cap slot. Separate from `status` because a job
    # that ran on a remote worker keeps `status == "running"` on the control plane by design
    # (BE-0015 W2) — its terminal state lives in the jobs table, and `job_view` falls back to the
    # bus for it — so without a second signal the cap counts a finished job until serve restarts.
    # Set by `JobRegistry._release_finished` from the jobs table; an in-process job's `status`
    # already answers this, so nothing sets it on the local path.
    released: bool = False
    exit_code: int | None = None
    run_id: str | None = None  # the runs/<id> a `run` job produced, parsed from its output
    out_path: str | None = None  # the scenario a `record` job authored (so the UI can load it)
    cancelled: bool = False  # a /cancel request stopped this job (vs. a real pass/fail)
    actor: str | None = None  # the GitHub login that started it, for per-user quota (BE-0015 7c-3)
    # The org the run belongs to (BE-0015 multi-tenancy). Travels in the job spec so a remote worker
    # reads/writes this org's object-store prefix. The single `default` org for local / single-tenant.
    org: str = _DEFAULT_ORG
    proc: Any = None  # the live subprocess (build or run), so a cancel can terminate it
    # Whether the process this job spawns answers a cancel cooperatively (BE-0370): a `run` job's
    # `bajutsu run` finishes the scenario it is on, fails the rest, and writes its manifest before
    # exiting. Declared by the dispatcher (it alone knows what it is starting) and carried in the job
    # spec, so a worker registers its spawn the same way. False for `record` / `crawl` / triage,
    # which have no verdict to preserve and keep today's immediate group-wide kill.
    graceful_cancel: bool = False
    # Live state beside `proc`: whether the *currently registered* subprocess is that cooperative run.
    # A `run` job's own on-demand build phase registers here too, and it keeps today's kill, so the
    # declaration above is not enough on its own — the cancel path needs to know which phase is live.
    # Written and cleared with `proc` at every site that touches it, so `proc is None` always implies
    # False here rather than a stale value the reader has to reason is harmless.
    proc_graceful: bool = False
    lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    bus: LogBus | None = None  # live-log channel; set from state.logbus at creation (BE-0015)
    # Files a remote worker must write into its workspace before running (workspace-relative path ->
    # content): the scenario + config a server-backend run materializes. Empty for local (the files
    # are already on disk). Travels in the job spec; never carries a client-controlled path (BE-0015).
    materials: dict[str, str] = field(default_factory=dict)
    # For a server-backend `record`: (app, ref) the worker persists the authored scenario to after
    # the run (it wrote it to `out_path` in its workspace). None for local / non-record jobs.
    record_save: tuple[str, str] | None = None
    # For a server-backend `run`: download the visual baselines into the workspace before running
    # (the cmd points `--baselines` at a workspace dir). False for local (the real dir is used).
    materialize_baselines: bool = False
    # The working directory the spawned run/build gets, captured when the job is accepted so a rebind
    # between registration and spawn cannot repoint an already-accepted run (BE-0393 unit 2). A
    # dispatcher that resolved a session's binding stamps it in `_register_and_dispatch`;
    # `ServeState._freeze_binding` fills in the deployment's for a caller that resolved none. None
    # only on a worker-rebuilt job, which resolves its own workspace at spawn.
    cwd: Path | None = None
    # Provenance to record into the produced run's manifest.json after it finishes (the bound bundle's
    # filename + zip sha256 + size). None for a normal run. Set for a run off an uploaded bundle (BE-0073).
    provenance: dict[str, str] | None = None
    # The uploaded bundle this job runs off (`Upload.worker_ref`): its identity, and — for a composed
    # triple (BE-0268) — the per-leg shas to fetch. Travels in the job spec so a remote worker, which
    # has no project on disk, rebuilds the tree its config's relative `appPath` resolves against.
    # None for a local-file or Git-sourced config, whose tree the worker resolves for itself.
    bundle: dict[str, Any] | None = None
    # The standalone artifacts this one job resolves against instead of its bound tree (BE-0431).
    # Independent of `bundle`, which stays whatever the binding resolved to. None for a job naming
    # no override, which keeps every path exactly as before.
    overrides: ArtifactOverrides | None = None
    # Per-run key prefix for evidence upload, under the server's --evidence-store base (BE-0110). CI
    # sets it via the /api/run body to pick the cloud lifecycle policy; travels in the job spec so the
    # worker relays it back when requesting presigned PUT URLs. Empty = key directly under the base.
    evidence_prefix: str = ""
    # A `record` job that paused for a human is in an explicit, resumable "awaiting human" state
    # (BE-0179): set when the spawned record emits a handoff request, cleared when the response is
    # written back to its stdin. Surfaced to the UI so the paused job is visible, not a silent block.
    awaiting_human: bool = False
    # Capability tokens the worker running this job must advertise (BE-0166): its platform axis
    # (`platform:ios` / `platform:web`) plus the target's operator-declared `requires` (`ios18`,
    # `ipad`). Travels in the job spec so the hosted router leases it only to a capable worker; empty
    # for a local run (one worker, no routing).
    capabilities: list[str] = field(default_factory=list)
    # The run-history partition this run belongs to, resolved once when the run is enqueued
    # (BE-0404 unit 2): the label derived from the bound config, or the operator's `--label`.
    # Travels in the job spec so a remote worker's `_persist_run` stamps `runs.label` without
    # consulting any registry — a run keeps the label it was enqueued under even if `serve` is
    # rebound before it finishes. None when no config is bound to derive one from.
    label: str | None = None
    # The requesting org's resolved AI provider env (BE-0229): provider/model/effort/language, merged
    # onto the spawn's inherited env by `_spawn_env` so the job uses *this* org's selection without
    # the serve process ever mutating its shared `os.environ` — the tenant-isolation guarantee.
    # Resolved at enqueue on the control plane (from the org's settings) and carried in the job spec,
    # so a remote worker needs no settings of its own. Empty when no provider is selected (the
    # zero-config path, BE-0101, then falls back to the job's inherited env unchanged).
    env_overlay: dict[str, str] = field(default_factory=dict)
    # A cloud-batch run request (BE-0336): when set, this job runs one scenario on a batch device
    # cloud (its `provider` names the concrete backend) instead of spawning `cmd` locally. `run_job`
    # branches on it; None is the ordinary local/worker run. The verdict still comes from the run's
    # own manifest, so the batch path stays off the `run`/CI verdict path.
    batch: BatchRequest | None = None

    def view(self, *, include_lines: bool = True) -> dict[str, Any]:
        """The job's state for the UI. `include_lines=False` omits the log buffer — used for the
        terminal-status payload stored on the LogBus, where the lines already live in the log
        stream and would needlessly duplicate the whole log (BE-0015 W2)."""
        with self.lock:
            v: dict[str, Any] = {
                "id": self.id,
                "status": self.status,
                "exitCode": self.exit_code,
                "runId": self.run_id,
                "outPath": self.out_path,
                "cancelled": self.cancelled,
                "ok": (self.exit_code == 0 and not self.cancelled)
                if self.status == "done"
                else None,
                "awaitingHuman": self.awaiting_human,
            }
            if include_lines:
                v["lines"] = list(self.lines)
            return v
