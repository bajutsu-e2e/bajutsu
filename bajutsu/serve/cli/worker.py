"""`bajutsu worker` — lease queued runs from the control plane and execute them (BE-0106).

The control plane (`bajutsu serve`) inserts a job row per run; this command polls
the `/api/worker/lease` endpoint over HTTP, executes the unchanged `run_job`, uploads the run tree
(including `console.log`), and posts the result back to `/api/worker/result`. No Redis or RQ, and
**no cloud credentials** (BE-0160): every object-store touch — downloading baselines before a run,
uploading the run tree and a `record` job's authored scenario after — goes through presigned URLs
the control plane signs, so the worker needs only an HTTP client.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer

from bajutsu.common.backend_cli import simctl
from bajutsu.common.backends import PLATFORMS
from bajutsu.common.capability.worker_capability import (
    APPIUM,
    LOCAL,
    RESERVED_ROUTING_REASON,
    WorkerCapability,
)
from bajutsu.common.cloud.devicefarm import RunInterrupted
from bajutsu.common.evidence.redaction import LineCredentialMasker, Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.handoff import REQUEST_LINE_PREFIX
from bajutsu.common.run_meta.files import DEFAULT_RUNS_DIR
from bajutsu.common.run_meta.object_store import content_type_for
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.capabilities import (
    MACHINE_ID_ENV,
    WORKER_CAPABILITIES_ENV,
    environment_capability,
    host_capability,
    machine_capability,
    parse_capabilities,
    reserved_tokens,
    worker_capabilities,
)
from bajutsu.serve.cli.worker_relay import ArtifactStreamer, HandoffRelay
from bajutsu.serve.helpers import unsafe_env_name, valid_sha256, warn_unsafe_env_name
from bajutsu.serve.jobs import _runs_dir_of
from bajutsu.serve.operations.composition import materialize_composition, place_overrides
from bajutsu.serve.operations.worker import (
    WORKER_LOG_BATCH_BYTES,
    WORKER_LOG_MAX_LINE_CHARS,
    WORKER_LOG_MAX_LINES,
)
from bajutsu.serve.server.db import cancelled_job_result
from bajutsu.serve.server.one_shot import environment_refusal
from bajutsu.serve.server.worker_job import CancelRelay as _CancelRelay
from bajutsu.serve.server.worker_job import WorkerIO, _materialize, execute_job_spec
from bajutsu.serve.state import Job
from bajutsu.serve.upload_artifacts import ARTIFACT_KINDS, OVERRIDE_KINDS, ArtifactOverrides
from bajutsu.serve.uploads import find_bundle_config, materialize_bundle, validate_bundle_config

_logger = logging.getLogger("bajutsu.worker")

# Heartbeat well under the control plane's default lease timeout (DEFAULT_LEASE_TIMEOUT_SECONDS) so
# a legitimately long run is never mistaken for a dead worker and reclaimed (BE-0016).
DEFAULT_HEARTBEAT_INTERVAL = 30.0

# How often a leased job's new log lines are posted to the control plane (BE-0449): short enough
# that the browser's console reads as live, long enough that a chatty run sends a batch, not a
# request per line.
DEFAULT_LOG_FLUSH_INTERVAL = 1.0

# Per-request timeout for one log batch, so a stalled control plane delays the live console and
# never the job; the final flush before the result post waits at most this long per batch.
_LOG_POST_TIMEOUT = 10.0

# The most time the last flush after a job may spend, so a slow control plane delays the result
# post by seconds rather than by one timeout per pending batch.
_LOG_FINAL_FLUSH_BUDGET = 5.0

# Lines that may wait to post before the oldest are dropped, so a run that prints faster than the
# control plane accepts cannot grow the worker's memory without bound. Twenty full batches.
_LOG_MAX_PENDING = 10_000

# Statuses that mean the control plane predates the log route, so no later post can succeed.
_LOG_ROUTE_ABSENT = frozenset({401, 404, 405})

# Per-request timeout for the presigned upload/download paths (BE-0110/BE-0160), so a stalled
# connection can't hang the worker on a single file (evidence upload runs after heartbeats stop).
_UPLOAD_HTTP_TIMEOUT = 60.0

# Name the client instead of leaving urllib's default `Python-urllib/<x.y>`: a control plane behind
# Cloudflare answers that signature with a 403 `error code: 1010` (Browser Integrity Check) before
# the request ever reaches the auth gate, so a correctly-tokened worker leases nothing forever.
_USER_AGENT = "bajutsu-worker"

# Where `_bundle_workspace` keeps one rebuilt tree per uploaded bundle, under the worker's own
# working directory. Dot-prefixed so it is never mistaken for a run's own output, nor picked up by a
# glob over the workspace. Trees nest one level deeper, per org (see `_bundle_workspace`).
_BUNDLE_CACHE_DIR = ".bundles"
# Where a job carrying per-job artifact overrides (BE-0431) gets a tree of its own, beside the bundle
# cache rather than inside it, so a bundle's tree is still fetched once whatever overrides reuse it.
_OVERRIDE_CACHE_DIR = ".overrides"
# Where a cloud-batch job that ships no bundle gets a workspace of its own (BE-0448): the provider
# packages that directory, so it must hold this job's config and scenarios and no other job's, nor
# the bundle cache beside it. Removed once the job's evidence is uploaded.
_BATCH_JOBS_DIR = ".batch"
# How long `_LeaseCheckpoint` waits between attempts to store a run's ARN, doubling up to the cap:
# the run is already holding a device, so the first retries come quickly (BE-0448).
_CHECKPOINT_RETRY_INITIAL = 1.0
_CHECKPOINT_RETRY_MAX = 30.0
# How long `_LeaseCheckpoint` keeps retrying before it gives the run up: three of the server's
# default lease timeouts (120 seconds, `DEFAULT_LEASE_TIMEOUT_SECONDS`). Past that, the lease has
# lapsed anyway, and an unrecorded run that keeps polling would only hold a device nobody can resume.
_CHECKPOINT_DEADLINE = 360.0
# A job id the worker turns into a directory name: one plain path segment.
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# The parts a lease may sign for one bundle: the whole tree as a zip (a single-zip bind, BE-0073), or
# one object per artifact kind (a composed triple, BE-0268). A name outside this set is a broken or
# hostile lease response, never a path segment to fetch into.
_BUNDLE_PART_NAMES = frozenset({"bundle", *ARTIFACT_KINDS})

# Read the fetched bytes back in blocks to hash them, so verifying a part never loads an app binary
# into memory (the same reason `_get_file` streams to disk in the first place).
_HASH_CHUNK = 1024 * 1024

# HTTP statuses on a presigned GET that mean the object is not there and never will be, so no retry
# can help. 403 is deliberately absent: S3 answers an expired signature with 403 too, so that one is
# genuinely ambiguous and is better retried than reported.
_GONE_STATUSES = frozenset({404, 410})


class _TransientFetch(Exception):
    """A bundle part could not be *downloaded*, for a reason another attempt may well get past.

    Raised only around the transfer itself (`_get_file` plus the digest check that proves it
    arrived whole), never around building the tree from bytes already on disk. That boundary is the
    classification: a download is at the mercy of the network, while extracting, placing, and
    validating are deterministic over verified bytes, so a failure there is permanent and belongs in
    a reported result. Keying off the exception *type* instead would misfile both directions — an
    `HTTPError` is an `OSError`, and so is the `FileExistsError` a bad tree raises.
    """


def _post_json(
    url: str, body: dict[str, Any], *, token: str | None = None, timeout: float | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode()
    headers: dict[str, str] = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=data, headers=headers)  # noqa: S310
    try:
        with urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read()
            if not raw:
                return r.status, {}
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError as e:
                # A 2xx that isn't JSON came from in front of the control plane, not from it — a proxy
                # interstitial, or an SSO login page reached through the redirect `urlopen` follows.
                # Raise it as the transport error every caller already handles: returning the text
                # would hand a `str` to callers that read the body as a mapping.
                raise URLError(f"non-JSON response from {url}: {raw[:200]!r}") from e
    except HTTPError as e:
        raw = e.read() if e.fp else b""
        if not raw:
            return e.code, {}
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            # An error page from something in front of the control plane (a proxy, Cloudflare) isn't
            # JSON. Hand back its text with the status rather than raising a decode error that buries
            # the status the caller needs to report.
            return e.code, raw.decode(errors="replace")


def _advertised_capabilities(
    platform: str,
    capabilities: str,
    worker_capability: WorkerCapability | None = None,
    host: str | None = None,
) -> list[str]:
    """The sorted capability set this worker advertises (BE-0166).

    Combines its ``--platform`` axes, ``host:<os>`` for the machine it runs on (BE-0450), the
    deprecated operator override (``--capabilities`` or `WORKER_CAPABILITIES_ENV`), and, for an iOS
    worker, the installed Simulator inventory. The Simulator probe is gated on ``ios`` so a web-only
    worker (the Linux container) never shells out to an absent ``xcrun``.

    A worker whose environment is a device cloud (BE-0448) drives no device of its own: it
    advertises ``environment:<name>`` in place of any ``platform:*`` token, and refuses an explicit
    ``--platform``. An empty *platform* means ``ios`` for a local worker, which refuses a platform
    whose driver its worker.yaml omits or its host cannot run, rather than advertising a platform it
    cannot serve (BE-0450). The worker fills the ``environment:`` and ``host:`` prefixes itself, so the
    override may not carry them.

    Args:
        platform: The ``--platform`` value, a comma list.
        capabilities: The ``--capabilities`` value; `WORKER_CAPABILITIES_ENV` when empty.
        worker_capability: The worker.yaml in force; the shipped default when None.
        host: The host operating system; `backends.current_host_os()` when None (for tests).

    Raises:
        typer.BadParameter: The override carries a reserved token, or ``--platform`` names a
            platform this worker cannot serve.
    """
    from bajutsu.common.backends import current_host_os
    from bajutsu.common.capability.capability_preflight import filter_actuators
    from bajutsu.common.capability.worker_capability import load_worker_capability
    from bajutsu.common.deprecations import warn_worker_capabilities

    cap = load_worker_capability() if worker_capability is None else worker_capability
    machine = current_host_os() if host is None else host
    if capabilities:
        warn_worker_capabilities("--capabilities")
        override: str | None = capabilities
    else:
        override = os.environ.get(WORKER_CAPABILITIES_ENV)
        if override:
            warn_worker_capabilities(f"${WORKER_CAPABILITIES_ENV}")
    if reserved := reserved_tokens(parse_capabilities(override)):
        raise typer.BadParameter(
            f"{', '.join(reserved)}: {RESERVED_ROUTING_REASON}",
            param_hint="--capabilities",
        )
    host_token = host_capability(machine)
    if cap.environment == APPIUM:
        if platform:
            raise typer.BadParameter(
                "an appium worker takes no --platform: its grid's platform is in worker.yaml",
                param_hint="--platform",
            )
        # The grid owns the device and its host, so a job routed here requires the environment
        # alone (BE-0452); advertising more would only let a stray local job match.
        return [environment_capability(APPIUM)]
    if not cap.is_local:
        if platform:
            raise typer.BadParameter(
                f"a worker whose environment is '{cap.environment}' drives no device of its own, so "
                "it takes no --platform; the phone belongs to the provider's host",
                param_hint="--platform",
            )
        return sorted(
            {environment_capability(cap.environment), host_token, *parse_capabilities(override)}
        )
    platforms = [p.strip() for p in (platform or "ios").split(",") if p.strip()]
    # Fail loudly on a typo'd platform (e.g. `--platform iso`) rather than silently advertising a
    # `platform:iso` token that matches no job — the worker would otherwise poll forever leasing
    # nothing (BE-0166, "determinism first"). The same known set the config schema registry validates.
    if unknown := [p for p in platforms if p not in PLATFORMS]:
        raise typer.BadParameter(
            f"invalid --platform {', '.join(unknown)}: use one of {', '.join(PLATFORMS)}"
        )
    # The same rule as `run`'s actuator selection: a Linux machine started with the default `ios`
    # stops here with the reason, rather than idling on jobs it could never start (BE-0450).
    for p in platforms:
        kept, reasons = filter_actuators(PLATFORMS[p], cap, machine)
        if not kept:
            raise typer.BadParameter(f"{p}: {'; '.join(reasons)}", param_hint="--platform")
    return sorted(
        worker_capabilities(
            platforms,
            override=override,
            run=simctl.real_run if "ios" in platforms else None,
        )
        | {host_token}
    )


def _machine_token(
    machine_id: str, server_state_dir: Path | None, capability: WorkerCapability
) -> str | None:
    """The ``machine:<id>`` token this worker advertises, or None when it names no server machine.

    The identity comes from ``--machine-id``, or is read from the server's state directory; never
    both, so the two cannot disagree. `MACHINE_ID_ENV` stands in for the flag when neither is given. A device-cloud worker takes neither: it
    serves only its environment's jobs (`can_serve`), so the token could route it nothing.

    Raises:
        typer.BadParameter: both sources are given, the identity is malformed or unreadable, or the
            worker's environment is a device cloud.
    """
    from bajutsu.serve.server.local_keys import read_machine_id

    if machine_id and server_state_dir is not None:
        raise typer.BadParameter(
            "give the machine id or the state directory to read it from, not both",
            param_hint="--machine-id",
        )
    # The variable is a fallback for `--machine-id` alone, so an exported one never fights the
    # `--server-state-dir` that `make serve` passes.
    given = machine_id or (
        "" if server_state_dir is not None else os.environ.get(MACHINE_ID_ENV, "")
    )
    if not given and server_state_dir is None:
        return None
    if not capability.is_local:
        raise typer.BadParameter(
            f"a worker whose environment is '{capability.environment}' runs no job off the "
            "server's files, so it takes no machine id",
            param_hint="--machine-id",
        )
    try:
        return machine_capability(given or read_machine_id(server_state_dir or Path()))
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--machine-id") from None


def worker(
    server_url: str = typer.Option(
        "",
        "--server-url",
        help="Control-plane URL (default: $BAJUTSU_SERVER_URL / http://localhost:8765)",
    ),
    token: str = typer.Option("", "--token", help="Operator token for auth"),
    poll_interval: float = typer.Option(
        2.0, "--poll-interval", help="Seconds between lease attempts when idle"
    ),
    heartbeat_interval: float = typer.Option(
        DEFAULT_HEARTBEAT_INTERVAL,
        "--heartbeat-interval",
        help="Seconds between lease heartbeats during a run (keep it under the server lease timeout)",
    ),
    worker_id: str = typer.Option("", "--worker-id", help="Worker identifier"),
    platform: str = typer.Option(
        "",
        "--platform",
        help="Comma-list of platforms this worker can drive (ios / web / android) — the backend "
        "axis it advertises for capability routing (BE-0166). A Mac iOS worker is 'ios' (the "
        "default); the Playwright container is 'web'. A local worker refuses a platform whose "
        "driver its worker.yaml omits or its host cannot run (BE-0450). A worker whose worker.yaml "
        "environment is a device cloud takes none (BE-0448).",
    ),
    capabilities: str = typer.Option(
        "",
        "--capabilities",
        help="Deprecated (BE-0450): extra capability tokens to advertise beyond the platform + "
        "Simulator inventory (comma/space separated, e.g. 'ios18,ipad'); also read from "
        "$BAJUTSU_WORKER_CAPABILITIES. Still honored, with a notice, for this release.",
    ),
    worker_config: Annotated[
        Path | None,
        typer.Option(
            "--worker-config",
            help="The worker capability file (worker.yaml) this worker runs under, used in place "
            "of the shipped default (BE-0450) and handed to every `run` it executes.",
        ),
    ] = None,
    workdir: Annotated[
        Path | None,
        typer.Option(
            "--workdir",
            help="The directory leased jobs run in: their materials, fetched bundles, and run "
            "trees before upload. Default: a fresh temporary directory, removed when the worker "
            "stops, so a worker started in a project never writes into it (BE-0449).",
        ),
    ] = None,
    machine_id: Annotated[
        str,
        typer.Option(
            "--machine-id",
            help="The server machine's identity (its .bajutsu/machine-id), advertised as "
            "machine:<id> so this worker takes the jobs that name files by their paths on that "
            "machine — a local start's run, triage, record, and crawl (BE-0449). Give it only to a "
            "worker that sees those paths. Also read from $BAJUTSU_WORKER_MACHINE_ID.",
        ),
    ] = "",
    server_state_dir: Annotated[
        Path | None,
        typer.Option(
            "--server-state-dir",
            help="The server's state directory (.bajutsu beside its runs directory) to read "
            "--machine-id from, for a worker on the server's own machine; `make serve` passes it.",
        ),
    ] = None,
    once: Annotated[
        Path | None,
        typer.Option(
            "--once",
            help="Execute the one job spec in this JSON file on this machine, with no server, and "
            "exit with the job's exit code (BE-0449). The run tree stays local; a spec that names "
            "an uploaded bundle or artifact overrides is refused, since those arrive by signed URL. "
            "The server options are ignored.",
        ),
    ] = None,
) -> None:
    """Run a worker that leases queued `bajutsu run` jobs from the control plane over HTTP.

    Polls POST /api/worker/lease; on a job, runs execute_job_spec, uploads the run tree, and
    posts the result to POST /api/worker/result. With `--once`, executes one job spec from a file
    instead and exits with its exit code.
    """
    worker_file, capability = _worker_file_or_exit(worker_config)
    if once is not None:
        _register_environment_or_exit(capability.environment)
        raise typer.Exit(_run_once(once, worker_file, capability.environment))
    # Both drive sleeps/timeouts; a non-positive value would spin the poll or heartbeat loop hot.
    if poll_interval <= 0:
        raise typer.BadParameter("--poll-interval must be positive")
    if heartbeat_interval <= 0:
        raise typer.BadParameter("--heartbeat-interval must be positive")

    url = server_url or os.environ.get("BAJUTSU_SERVER_URL") or "http://localhost:8765"
    auth_token = token or os.environ.get("BAJUTSU_TOKEN") or None
    wid = worker_id or f"worker-{os.getpid()}"
    work, temporary = _workspace(workdir)
    # The capability set this worker advertises on every lease (BE-0166), computed once at startup
    # (the pool is assumed stable per worker).
    caps = _advertised_capabilities(platform, capabilities, capability)
    if machine := _machine_token(machine_id, server_state_dir, capability):
        caps = sorted({*caps, machine})
    # After the flags are checked, so a typo'd flag is reported before any AWS client is built.
    _register_environment_or_exit(capability.environment)

    typer.echo(f"bajutsu worker → polling {url}  (Ctrl-C to stop)")
    typer.echo(f"  advertising capabilities: {', '.join(caps) or '(none)'}")
    typer.echo(f"  workspace: {work}")
    from bajutsu.serve.server.one_shot import cancel_on_signals

    # `WorkerCapability` already refuses a local file above one, so only a device cloud gets here.
    if capability.max_job_concurrency > 1:
        typer.echo(f"  job slots: {capability.max_job_concurrency} (maxJobConcurrency)")
        shutdown = _SlotShutdown()
        try:
            with cancel_on_signals(shutdown) as signalled:
                _serve_slots(
                    url=url,
                    auth_token=auth_token,
                    wid=wid,
                    caps=caps,
                    work=work,
                    poll_interval=poll_interval,
                    heartbeat_interval=heartbeat_interval,
                    worker_file=worker_file,
                    slots=capability.max_job_concurrency,
                    shutdown=shutdown,
                    stopped=lambda: signalled() is not None,
                    environment=capability.environment,
                )
                signum = signalled()
        finally:
            if temporary:
                shutil.rmtree(work, ignore_errors=True)
        if signum is not None:
            typer.echo("  stopped; in-flight leases left to lapse")
            raise typer.Exit(128 + signum)
        return

    current = _CurrentJob(pgid_file=work / JOB_PGID_FILE)
    try:
        # A stop signal cancels the leased job the way the control plane's cancel does: gracefully
        # for a run, whose cancelled result is then posted, so the queue records it cancelled
        # rather than reclaiming the lease and running it again (BE-0449). The loop then ends; a
        # second SIGINT ends the job's group at once, as for `--once`.
        with cancel_on_signals(current) as signalled:
            _serve_leases(
                url=url,
                auth_token=auth_token,
                wid=wid,
                caps=caps,
                work=work,
                poll_interval=poll_interval,
                heartbeat_interval=heartbeat_interval,
                worker_file=worker_file,
                current=current,
                stopped=lambda: signalled() is not None,
                environment=capability.environment,
            )
            signum = signalled()
    finally:
        if temporary:
            shutil.rmtree(work, ignore_errors=True)
    if signum is not None:
        typer.echo("  stopped")
        raise typer.Exit(128 + signum)


def _serve_leases(
    *,
    url: str,
    auth_token: str | None,
    wid: str,
    caps: list[str],
    work: Path,
    poll_interval: float,
    heartbeat_interval: float,
    worker_file: Path | None,
    current: _CurrentJob,
    stopped: Callable[[], bool],
    environment: str = LOCAL,
) -> None:
    """Lease and run jobs until *stopped* answers True; the worker's poll loop."""
    while not stopped():
        body = _lease(url, auth_token, wid, caps)
        if body is None:
            time.sleep(poll_interval)
            continue

        job_id = body["job_id"]
        spec = body["spec"]
        typer.echo(f"  leased job {job_id}")

        # The worker's object I/O is brokered by presigned URLs (BE-0160): the lease already carries
        # signed GET URLs for this run's baselines, and this io asks the control plane for signed PUT
        # URLs when uploading the run tree / authored scenario — so the worker holds no credentials.
        io = PresignedWorkerIO(
            url=url,
            auth_token=auth_token,
            job_id=job_id,
            worker_id=wid,
            baseline_urls=body.get("baseline_urls"),
        )
        streamer = _LogStreamer(functools.partial(_post_log, url, wid, job_id, auth_token))
        bus = _TeeLogBus(streamer.add)
        relay = _CancelRelay()
        current.attach(relay)
        try:
            result, abandoned, job_work = _run_with_heartbeat(
                spec,
                job_id=job_id,
                work=work,
                bundle_urls=body.get("bundle_urls"),
                override_urls=_override_urls(body),
                batch_run_arn=body.get("batch_run_arn"),
                app_url=body.get("app_url"),
                bus=bus,
                url=url,
                wid=wid,
                auth_token=auth_token,
                heartbeat_interval=heartbeat_interval,
                io=io,
                worker_config=worker_file,
                streamer=streamer,
                secret_env=_lease_secret_env(body),
                cancel=relay,
                environment=environment,
            )
        finally:
            current.detach()
        try:
            _finish_job(
                url=url,
                auth_token=auth_token,
                wid=wid,
                job_id=job_id,
                spec=spec,
                outcome=(result, abandoned, job_work),
            )
        finally:
            _discard_batch_workspace(work, job_work)


def _lease(url: str, auth_token: str | None, wid: str, caps: list[str]) -> dict[str, Any] | None:
    """Ask the control plane for one job; the lease body, or None when there is nothing to run."""
    try:
        code, body = _post_json(
            f"{url}/api/worker/lease",
            {"worker_id": wid, "capabilities": caps},
            token=auth_token,
        )
    except (URLError, OSError) as e:
        _logger.warning("lease request failed: %s", e)
        return None

    # Say why a lease was refused instead of polling on in silence: a rejected credential or a
    # proxy blocking the request looks exactly like an empty queue otherwise.
    if code >= 400:
        # Truncated: a refusal never self-resolves, so this repeats every poll, and a WAF's
        # error page is kilobytes of HTML — one refusal must stay one readable line.
        _logger.warning("lease refused with HTTP %s: %.200s", code, body)
        return None

    if code == 204 or not body.get("spec"):
        return None
    return dict(body)


def _serve_slots(
    *,
    url: str,
    auth_token: str | None,
    wid: str,
    caps: list[str],
    work: Path,
    poll_interval: float,
    heartbeat_interval: float,
    worker_file: Path | None,
    slots: int,
    shutdown: _SlotShutdown,
    stopped: Callable[[], bool],
    environment: str,
) -> None:
    """Lease jobs into at most *slots* concurrent slots until stopped; the slot model (BE-0448).

    A job counts against *slots* from its lease until its result is posted, so a slot that is only
    uploading evidence frees its place, and no lease request goes out while every place is taken. The
    slot's own heartbeat keeps the worker live meanwhile, so its advertised set never changes. Each
    slot runs on a thread of its own, in a workspace no other running or uploading job shares.

    Once *stopped* answers True or *shutdown* is requested, the loop leases nothing more and lets
    every in-flight lease lapse (`_SlotShutdown`). It then waits for the slots to end: those uploading
    finish, and the others leave their runs. A second SIGINT ends the wait at once.
    """
    places = threading.Condition()
    busy = 0
    dirs = _SlotDirs(work)
    threads: list[threading.Thread] = []

    def free() -> None:
        nonlocal busy
        with places:
            busy -= 1
            places.notify_all()

    try:
        while not (stopped() or shutdown.stopping):
            with places:
                if busy >= slots:
                    places.wait(timeout=poll_interval)
                    continue
            body = _lease(url, auth_token, wid, caps)
            if body is None:
                time.sleep(poll_interval)
                continue
            with places:
                busy += 1
            thread = threading.Thread(
                target=_run_slot,
                kwargs={
                    "body": body,
                    "dirs": dirs,
                    "free": free,
                    "shutdown": shutdown,
                    "url": url,
                    "auth_token": auth_token,
                    "wid": wid,
                    "heartbeat_interval": heartbeat_interval,
                    "worker_file": worker_file,
                    "environment": environment,
                },
                daemon=True,
            )
            try:
                thread.start()
            except BaseException:
                free()  # no slot ever owned the place, so give it back here
                raise
            threads = [t for t in threads if t.is_alive()] + [thread]
    finally:
        # Whatever ends the loop, an exception included, the leases in flight lapse as on a stop.
        shutdown.request()
        for thread in threads:
            while thread.is_alive() and not shutdown.now:
                thread.join(timeout=0.2)


def _run_slot(
    *,
    body: dict[str, Any],
    dirs: _SlotDirs,
    free: Callable[[], None],
    shutdown: _SlotShutdown,
    url: str,
    auth_token: str | None,
    wid: str,
    heartbeat_interval: float,
    worker_file: Path | None,
    environment: str,
) -> None:
    """Run one leased job in a slot of its own, and give its place back once its result is posted.

    The slot owns everything the job touches: its workspace directory, log bus, live-log streamer,
    heartbeat, and cancel relay. Its *lost* flag is the one `_SlotShutdown` sets to let the lease
    lapse. The place is freed exactly once, after the result post or, when nothing is posted, at the
    end.
    """
    freed = False

    def post_done() -> None:
        nonlocal freed
        if not freed:
            freed = True
            free()

    # Read inside the try, so a malformed lease body still gives the place back.
    job_id = str(body.get("job_id"))
    slot_work: Path | None = None
    lost = threading.Event()
    try:
        spec = body["spec"]
        typer.echo(f"  leased job {job_id}")
        slot_work = dirs.acquire()
        io = PresignedWorkerIO(
            url=url,
            auth_token=auth_token,
            job_id=job_id,
            worker_id=wid,
            baseline_urls=body.get("baseline_urls"),
        )
        streamer = _LogStreamer(functools.partial(_post_log, url, wid, job_id, auth_token))
        shutdown.attach(lost)
        try:
            result, abandoned, job_work = _run_with_heartbeat(
                spec,
                job_id=job_id,
                work=slot_work,
                bundle_urls=body.get("bundle_urls"),
                override_urls=_override_urls(body),
                batch_run_arn=body.get("batch_run_arn"),
                app_url=body.get("app_url"),
                bus=_TeeLogBus(streamer.add),
                url=url,
                wid=wid,
                auth_token=auth_token,
                heartbeat_interval=heartbeat_interval,
                io=io,
                worker_config=worker_file,
                streamer=streamer,
                secret_env=_lease_secret_env(body),
                environment=environment,
                lost=lost,
            )
        finally:
            shutdown.detach(lost)
        _finish_job(
            url=url,
            auth_token=auth_token,
            wid=wid,
            job_id=job_id,
            spec=spec,
            outcome=(result, abandoned, job_work),
            on_posted=post_done,
        )
    except Exception:  # one slot's failure never takes the worker, or its other slots, down
        _logger.exception("slot for job %s failed", job_id)
    finally:
        post_done()
        if slot_work is not None:
            # The slot held this one job, so every cloud-batch tree under it is this job's: removed
            # on every exit, a failed run included, before the directory goes back to the pool.
            shutil.rmtree(slot_work / _BATCH_JOBS_DIR, ignore_errors=True)
            dirs.release(slot_work)


# Where a slot worker keeps one workspace per slot under its own working directory (BE-0448). The
# numbered directories are reused, so a slot's bundle cache outlives the job that filled it.
_SLOTS_DIR = ".slots"


class _SlotDirs:
    """The workspace directories of a slot worker: each job gets one no other live job holds.

    A directory goes back to the pool only after its job has uploaded its evidence, so a job still
    uploading never shares its tree with the next lease, even though it no longer counts as in flight.
    """

    def __init__(self, work: Path) -> None:
        self._root = work / _SLOTS_DIR
        self._lock = threading.Lock()
        self._free: set[int] = set()
        self._next = 0

    def acquire(self) -> Path:
        """The lowest-numbered free directory, created when it is new."""
        with self._lock:
            if self._free:
                index = min(self._free)
                self._free.discard(index)
            else:
                index = self._next
                self._next += 1
        path = self._root / str(index)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def release(self, path: Path) -> None:
        """Give *path* back to the pool once its job is entirely done."""
        with self._lock:
            self._free.add(int(path.name))


class _SlotShutdown:
    """What a stop signal does to a slot worker: stop leasing, and let in-flight leases lapse (BE-0448).

    Unlike `_CurrentJob`, it cancels nothing. Each in-flight slot's lease is treated as lost: the
    slot stops its heartbeat and posts nothing, so the lease lapses and the job is re-leased. A run
    whose ARN the server holds keeps running, and a restart or another worker resumes it; a run whose
    ARN the server does not hold yet is stopped, since nobody could resume it. A second SIGINT ends
    the wait for the slots at once (`kill`). It serves `cancel_on_signals` as the child.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: set[threading.Event] = set()
        self._stopping = threading.Event()
        self._now = threading.Event()

    @property
    def stopping(self) -> bool:
        """Whether a stop was requested: the worker leases nothing more."""
        return self._stopping.is_set()

    @property
    def now(self) -> bool:
        """Whether a second SIGINT asked the worker not to wait for its slots."""
        return self._now.is_set()

    def attach(self, lost: threading.Event) -> None:
        """Track a slot's *lost* flag, setting it at once when a stop came first."""
        with self._lock:
            self._slots.add(lost)
            stopping = self.stopping
        if stopping:
            lost.set()

    def detach(self, lost: threading.Event) -> None:
        """Stop tracking a slot whose job has ended."""
        with self._lock:
            self._slots.discard(lost)

    def request(self) -> None:
        """Stop leasing, and let the lease of every in-flight slot lapse."""
        with self._lock:
            self._stopping.set()
            slots = list(self._slots)
        for lost in slots:
            lost.set()

    def pgid(self) -> int | None:
        """No process group: a slot submits to a device cloud and spawns no run of its own."""
        return None

    def kill(self) -> None:
        """Stop waiting for the slots."""
        self._now.set()


def _finish_job(
    *,
    url: str,
    auth_token: str | None,
    wid: str,
    job_id: str,
    spec: dict[str, Any],
    outcome: tuple[dict[str, Any] | None, bool, Path],
    on_posted: Callable[[], None] | None = None,
) -> None:
    """Post a run job's result and upload its evidence, or say why neither happens.

    *on_posted* runs right after the result post, before the evidence upload: a slot worker frees
    the job's place there (BE-0448).
    """
    result, abandoned, job_work = outcome
    if abandoned:
        # The control plane reclaimed and likely re-leased this job to another worker; posting a
        # result would race that worker, so drop it (the re-run is the source of truth).
        typer.echo(f"  lease lost for job {job_id}; abandoning")
        return
    if result is None:
        # The bundle fetch hit a transient failure (a reset connection, an expired signature) —
        # logged already, inside `_run_with_heartbeat`. Posting a failure here would turn one
        # network blip into a red run for a scenario that never executed; leaving the lease to
        # lapse instead gives the queue's own reclaim-and-retry a chance to hand the job to
        # another attempt (directive 2: keep worker-side flakiness out of the verdict).
        typer.echo(
            f"  bundle fetch failed transiently for job {job_id}; leaving it to be re-leased"
        )
        return

    run_id = result.get("runId")
    _post_result(url, job_id, wid, result, auth_token)
    if on_posted is not None:
        on_posted()

    # Upload the run's evidence via presigned URLs the control plane signs (BE-0110): the worker
    # holds no cloud credentials of its own. Runs *after* the result is posted — heartbeats stop
    # once the run returns, so a slow upload must not delay the post and risk the lease being
    # reclaimed. Best-effort and time-bounded: a failure or stall warns and never affects the run.
    if run_id:
        _upload_evidence(
            job_work,
            run_id,
            url=url,
            auth_token=auth_token,
            evidence_prefix=str(spec.get("evidence_prefix") or ""),
        )

    # Say which way it went: a run that died on its first line ("No module named bajutsu")
    # otherwise looked exactly like a pass from this console.
    ending = "cancelled" if result.get("cancelled") else "ok" if result.get("ok") else "FAILED"
    typer.echo(f"  completed job {job_id} ({ending})")


# The prefix of a temporary workspace, and the file in it naming the process that owns it: a
# workspace whose owner is gone is left over from a worker killed outright, and the next worker
# removes it (BE-0449). `scripts/serve.sh` writes the same file into the workspace it hands over.
WORKSPACE_PREFIX = "bajutsu-worker-"
OWNER_PID_FILE = ".owner-pid"
# The file in the workspace naming the process group of the job running now, so a supervisor that
# must kill this worker outright (`scripts/serve.sh` past its wait) can end the run as well; the
# run lives in a session of its own and would otherwise outlive the worker.
JOB_PGID_FILE = ".job-pgid"


def _workspace(workdir: Path | None) -> tuple[Path, bool]:
    """The directory leased jobs run in, and whether it is a temporary one to remove at exit.

    Not the current directory: a worker started in a project would otherwise extract bundles into
    it, and upload a run tree it wrote there onto the same files (BE-0449). A temporary one records
    this process as its owner, after workspaces whose owners are gone are swept.
    """
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir.resolve(), False
    root = Path(tempfile.gettempdir())
    _sweep_stale_workspaces(root)
    work = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=root))
    (work / OWNER_PID_FILE).write_text(f"{os.getpid()}\n", encoding="utf-8")
    return work, True


def _sweep_stale_workspaces(root: Path) -> None:
    """Remove the temporary workspaces under *root* whose owning process has exited.

    Only a real directory of this account, named with `WORKSPACE_PREFIX`, whose owner file names
    one or more processes (one per line) that have all exited, is removed. Anything else is left
    alone, a workspace with no owner file included, since nothing says it is abandoned. Skipped on
    Windows, where `os.kill(pid, 0)` sends a console control event rather than probing.
    """
    if os.name == "nt":
        return
    uid = os.getuid()
    for path in root.glob(f"{WORKSPACE_PREFIX}*"):
        try:
            if path.is_symlink() or not path.is_dir() or path.stat().st_uid != uid:
                continue
            text = (path / OWNER_PID_FILE).read_text(encoding="utf-8")
            pids = [int(line) for line in text.split() if line]
        except (OSError, ValueError):
            continue
        if pids and not any(_process_alive(pid) for pid in pids):
            shutil.rmtree(path, ignore_errors=True)


def _group_alive(pgid: int) -> bool:
    """Whether the process group *pgid* still has a member (POSIX; assumed alive elsewhere)."""
    if os.name == "nt":
        return True
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _process_alive(pid: int) -> bool:
    """Whether a process with *pid* exists (one of another account counts as alive)."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class _CurrentJob:
    """The job a stop signal cancels: the one leased now, or the next one the moment it is leased.

    The signal handlers of `cancel_on_signals` call into it from their helper threads, while the
    poll loop attaches and detaches each job's relay, so a lock orders the two.
    """

    def __init__(self, pgid_file: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._relay: _CancelRelay | None = None
        self._stopping = False
        self._pgid_file = pgid_file

    def attach(self, relay: _CancelRelay) -> None:
        """Make *relay* the job a signal cancels, cancelling it at once when a stop came first.

        With a *pgid_file*, a helper thread records the job's process group there once its child
        exists, and keeps it current while the job runs.
        """
        with self._lock:
            self._relay = relay
            stopping = self._stopping
        if self._pgid_file is not None:
            threading.Thread(target=self._record_pgid, args=(relay,), daemon=True).start()
        if stopping:
            relay.request()

    def detach(self) -> None:
        """Forget the finished job's relay, and its recorded process group."""
        with self._lock:
            self._relay = None
            if self._pgid_file is not None:
                self._pgid_file.unlink(missing_ok=True)

    def _record_pgid(self, relay: _CancelRelay) -> None:
        recorded: int | None = None
        while True:
            pgid = relay.pgid()
            with self._lock:
                if self._relay is not relay:
                    return
                # The child has gone; drop its group once nothing is left in it, so a supervisor
                # never kills a group number the system may hand to another.
                if (
                    pgid is None
                    and recorded is not None
                    and self._pgid_file is not None
                    and not _group_alive(recorded)
                ):
                    self._pgid_file.unlink(missing_ok=True)
                    recorded = None
                if pgid is not None and pgid != recorded and self._pgid_file is not None:
                    staging = self._pgid_file.with_suffix(".tmp")
                    staging.write_text(f"{pgid}\n", encoding="utf-8")
                    staging.replace(self._pgid_file)
                    recorded = pgid
            time.sleep(0.2)

    def request(self) -> None:
        """Cancel the current job gracefully, and every job leased after it."""
        with self._lock:
            self._stopping = True
            relay = self._relay
        if relay is not None:
            relay.request()

    def pgid(self) -> int | None:
        """The current job's process group, for job control."""
        with self._lock:
            relay = self._relay
        return None if relay is None else relay.pgid()

    def kill(self) -> None:
        """End the current job's group at once."""
        with self._lock:
            relay = self._relay
        if relay is not None:
            relay.kill()


def _worker_file_or_exit(path: Path | None) -> tuple[Path | None, WorkerCapability]:
    """Validate the worker capability file at startup; its absolute path and contents (BE-0450).

    A bad file stops the worker before it leases anything, rather than failing every job it takes.
    The path is what each spawned `run` reads, so it is resolved once here, independent of the
    per-job working directory the run starts in. None (no flag) leaves every run on the default.
    """
    from bajutsu.common.capability.worker_capability import (
        WorkerCapabilityError,
        load_worker_capability,
    )

    try:
        cap = load_worker_capability(path)
    except WorkerCapabilityError as e:
        raise typer.BadParameter(str(e), param_hint="--worker-config") from None
    return (None if path is None else path.resolve()), cap


def _register_environment_or_exit(environment: str) -> None:
    """Register the batch provider a device-cloud worker's environment names, or stop (BE-0448).

    The provider and its lifecycle hooks (BE-0435) live in the worker that submits, so the AWS role
    is held here and never by the server. A worker that cannot register it would lease jobs only to
    fail each one, so it exits at startup instead, with the reason. A `local` worker registers none,
    and neither does an `appium` one, whose grid submits nothing (BE-0452).
    """
    if environment in (LOCAL, APPIUM):
        return
    from bajutsu.serve.batch_bootstrap import register_batch_providers

    try:
        kinds = register_batch_providers()
    except Exception as e:  # a missing extra, a bad hook path, a client that cannot be built
        raise typer.BadParameter(
            f"environment '{environment}': cannot register its provider: {e}",
            param_hint="--worker-config",
        ) from None
    if environment not in kinds:
        raise typer.BadParameter(
            f"environment '{environment}' registered no provider: set DEVICEFARM_PROJECT_ARN "
            "(and the AWS credentials) in this worker's environment",
            param_hint="--worker-config",
        )
    typer.echo(f"  provider registered: {environment}")


def _run_once(path: Path, worker_file: Path | None, environment: str = LOCAL) -> int:
    """Execute the one job spec in *path* without a server; the exit code for the caller (BE-0449).

    A spec that cannot run without a server is refused before anything runs, with exit 2 like any
    other usage error, and so is any spec on Windows (see the body). Under a device-cloud
    *environment* the spec must be that provider's batch, submitted with no ARN stored (BE-0448).
    """
    from bajutsu.serve.server import one_shot

    if os.name == "nt":
        # The one-shot path relays signals to its child's process group and watches it through an
        # inherited pipe, neither of which Windows offers; refusing beats a run nothing can cancel.
        raise typer.BadParameter(
            "`bajutsu worker --once` needs a POSIX host (macOS or Linux) for now; "
            "on Windows, run `bajutsu run` directly",
            param_hint="--once",
        )
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise typer.BadParameter(f"cannot read the job spec: {e}", param_hint="--once") from None
    if (reason := one_shot.refusal(spec, Path.cwd(), environment=environment)) is not None:
        raise typer.BadParameter(reason, param_hint="--once")
    try:
        return one_shot.run_spec_once(spec, worker_config=worker_file, environment=environment)
    except (
        ValueError
    ) as e:  # a spec the run could still not take, e.g. a run argv with no flag slot
        raise typer.BadParameter(str(e), param_hint="--once") from None


class _LogStreamer:
    """Posts a leased job's log lines to the control plane in batches while it runs (BE-0449).

    Best-effort by design: the uploaded ``console.log`` is the durable copy, so a batch that fails
    to post is logged and dropped, never retried into a growing backlog, and nothing here can fail
    or hold up the job. A 409 means the lease is gone, as on the heartbeat: `lost` turns True and
    no further line is sent. A control plane older than the log route (404 / 405, or 401 from a
    gate that does not know it) turns the streamer off for the rest of the job. *post* sends one
    batch and returns the HTTP status, or None when the request did not complete. *clock* bounds
    the final flush and is injectable for tests.
    """

    def __init__(
        self,
        post: Callable[[list[str]], int | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        max_pending: int = _LOG_MAX_PENDING,
    ) -> None:
        self._post = post
        self._clock = clock
        self._max_pending = max_pending
        # One masker for the whole job, so a private key block spanning two lines (or two batches)
        # stays masked to its END line.
        self._masker = LineCredentialMasker()
        self._lock = threading.Lock()  # guards `_pending`, `_dropped`, and the masker
        # Held for a whole flush, so the timed flusher and the final flush never interleave
        # batches and the server receives lines in the order the run printed them.
        self._flushing = threading.Lock()
        self._pending: list[str] = []
        self._dropped = 0  # lines discarded because the control plane fell behind
        self._lost = threading.Event()
        self._disabled = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        """Whether the log route answered 409: the lease is no longer this worker's."""
        return self._lost.is_set()

    @property
    def dropped(self) -> int:
        """How many lines were discarded because more than the pending cap waited to post."""
        with self._lock:
            return self._dropped

    def add(self, line: str) -> None:
        """Queue one line for the next flush, masked and cut to the server's line length."""
        if self._lost.is_set() or self._disabled.is_set():
            return
        with self._lock:
            self._pending.append(self._masker.mask(line)[:WORKER_LOG_MAX_LINE_CHARS])
            overflow = len(self._pending) - self._max_pending
            if overflow > 0:
                # Keep the newest lines: a live console is about what the run does now.
                del self._pending[:overflow]
                self._dropped += overflow

    def flush(self, *, deadline: float | None = None) -> None:
        """Post every queued line, in batches the server accepts, until *deadline* on the clock."""
        with self._flushing:
            with self._lock:
                batch, self._pending = self._pending, []
            start = 0
            while start < len(batch):
                if self._lost.is_set() or self._disabled.is_set():
                    return
                if deadline is None and self._stop.is_set():
                    # A background flush yields to `stop`: the rest goes back to the front of the
                    # queue for the final flush, which posts it within its own budget, so `stop`
                    # never waits on a long backlog here.
                    with self._lock:
                        self._pending[:0] = batch[start:]
                    return
                if deadline is not None and self._clock() >= deadline:
                    _logger.warning(
                        "log flush ran out of time; dropping %d lines", len(batch) - start
                    )
                    return
                end = _post_end(batch, start)
                code = self._post(batch[start:end])
                start = end
                if code == 409:
                    self._lost.set()
                    return
                if code in _LOG_ROUTE_ABSENT:
                    _logger.warning(
                        "the control plane has no worker log route (HTTP %s); "
                        "streaming the live log is off for this job",
                        code,
                    )
                    self._disabled.set()
                    return
                if code is None:
                    return  # the control plane is unreachable; drop the rest of this batch

    def start(self, interval: float) -> None:
        """Flush every *interval* seconds on a background thread until `stop`."""
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()

    def stop(self, *, flush: bool, budget: float = _LOG_FINAL_FLUSH_BUDGET) -> None:
        """End the background flushes, then post what is still queued when *flush* is True,
        spending at most *budget* seconds on it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if flush:
            self.flush(deadline=self._clock() + budget)
        if self.dropped:
            _logger.warning("the live log dropped %d lines it could not post in time", self.dropped)

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self.flush()


def _post_end(batch: list[str], start: int) -> int:
    """Where the post beginning at *start* ends: within the server's line count and body size.

    Sized the way `_post_json` encodes the lines, so a batch heavy in escaped characters is split
    before the server would refuse its body. A post always carries at least one line, and one line
    cut to `WORKER_LOG_MAX_LINE_CHARS` always fits.
    """
    end = min(len(batch), start + WORKER_LOG_MAX_LINES)
    size = 0
    for i in range(start, end):
        size += len(json.dumps(batch[i])) + 2  # the quoted line and its ", " separator
        if size > WORKER_LOG_BATCH_BYTES and i > start:
            return i
    return end


class _TeeLogBus(InMemoryLogBus):
    """The worker's log buffer, which also hands each line to the live stream (BE-0449).

    The buffer still becomes ``console.log`` after the run, unchanged. A handoff request line
    (BE-0179) stays out of the live log: it goes to *on_request* instead, the handoff relay that
    carries it to the page on a route of its own and brings the person's answer back (unit 8).
    """

    def __init__(self, on_line: Callable[[str], None]) -> None:
        super().__init__()
        self._on_line = on_line
        # Set by `_run_with_heartbeat` once the job's handoff relay exists; until then, and for a
        # job with no relay, a request line is dropped.
        self.on_request: Callable[[str], None] | None = None

    def publish(self, job_id: str, line: str) -> None:
        super().publish(job_id, line)
        self._tee([line])

    def publish_many(self, job_id: str, lines: list[str]) -> None:
        super().publish_many(job_id, lines)
        self._tee(lines)

    def _tee(self, lines: list[str]) -> None:
        for line in lines:
            if not line.startswith(REQUEST_LINE_PREFIX):
                self._on_line(line)
            elif self.on_request is not None:
                self.on_request(line)


def _post_relay(
    url: str,
    wid: str,
    job_id: str,
    auth_token: str | None,
    path: str,
    body: dict[str, Any],
    *,
    timeout: float = _LOG_POST_TIMEOUT,
) -> tuple[int | None, Any]:
    """Post one relay request for the job (BE-0449 unit 8); ``(None, None)`` when it failed.

    The relays are best effort, like the live log: a failure is logged and never reaches the job.
    """
    try:
        return _post_json(
            f"{url}{path}",
            {"worker_id": wid, "job_id": job_id, **body},
            token=auth_token,
            timeout=timeout,
        )
    except Exception as e:  # a relay post must never reach the job, whatever went wrong
        _logger.warning("relay post %s failed for job %s: %s", path, job_id, e)
        return None, None


def _post_log(
    url: str,
    wid: str,
    job_id: str,
    auth_token: str | None,
    lines: list[str],
    *,
    timeout: float = _LOG_POST_TIMEOUT,
) -> int | None:
    """Post one batch of log lines; the HTTP status, or None when the post failed (logged)."""
    try:
        code, _body = _post_json(
            f"{url}/api/worker/log",
            {"worker_id": wid, "job_id": job_id, "lines": lines},
            token=auth_token,
            timeout=timeout,
        )
    except Exception as e:  # a live-log post must never reach the job, whatever went wrong
        _logger.warning("log post failed for job %s; dropping %d lines: %s", job_id, len(lines), e)
        return None
    if code not in (200, 409):
        _logger.warning("log post for job %s answered HTTP %s; dropping it", job_id, code)
    return code


def _run_with_heartbeat(
    spec: dict[str, Any],
    *,
    job_id: str,
    work: Path,
    bundle_urls: Any,
    bus: InMemoryLogBus,
    url: str,
    wid: str,
    auth_token: str | None,
    heartbeat_interval: float,
    io: WorkerIO | None = None,
    override_urls: dict[str, str] | None = None,
    worker_config: Path | None = None,
    streamer: _LogStreamer | None = None,
    log_flush_interval: float = DEFAULT_LOG_FLUSH_INTERVAL,
    secret_env: dict[str, str] | None = None,
    cancel: _CancelRelay | None = None,
    environment: str = LOCAL,
    batch_run_arn: Any = None,
    app_url: Any = None,
    lost: threading.Event | None = None,
) -> tuple[dict[str, Any] | None, bool, Path]:
    """Run the job on a background thread while heart-beating its lease from this one.

    The bundle fetch (`_workspace_or_failure`) runs on this same thread, ahead of `execute_job_spec`,
    so the heartbeat covers it too. It is the single largest transfer in the whole job — an app
    binary, up to the 1 GiB on-the-wire upload cap — and it used to run *before* this function
    was even called, with nothing renewing the lease while it downloaded; a fetch slower than the
    lease timeout tripped a reclaim this worker never noticed, and ran the job anyway alongside
    whichever worker won the re-lease. Object I/O (baseline download, run-tree/scenario upload) runs
    here too through *io*, for the same reason.

    Returns ``(result, abandoned, job_work)``. *abandoned* is True when the control plane reclaimed
    the lease mid-run (HTTP 409): another worker now owns the job, and *result* should be dropped.
    A `None` *result* means the bundle fetch failed transiently (already logged): nothing to post,
    and the lease is left to lapse rather than reported as a failed job (directive 2 — a network
    blip must not surface as a red run for a scenario that never executed). *job_work* is *work*
    itself for a job that ships no bundle, or meaningless alongside a `None` result.

    A reclaim seen while the fetch is still running must stop the run from ever starting, not merely
    get dropped once it finishes: without *lost*, the run thread would walk straight from a finished
    fetch into `execute_job_spec` — installing the app and driving the device beside whichever worker
    won the re-lease — and only discard the result afterward. The fetch is the one point past which
    nothing has touched the device yet, so returning early there costs nothing.

    With a *streamer*, a flusher thread posts the job's log lines every *log_flush_interval*
    seconds, and one last flush runs after the job, before this returns, so every line is on the
    server ahead of the result. A 409 on the log route abandons the job like a 409 on the heartbeat.

    *secret_env* is the org's stored secrets from the lease (BE-0449); it reaches the job's child
    environment alone and is never logged. *cancel* is the relay a stop signal cancels the job
    through, as the heartbeat's cancel does; a fresh one when the caller has none. A job that does
    not belong to this worker's *environment* (BE-0448) fails before anything is fetched.

    A cloud-batch job stores its run's ARN through the server (`_LeaseCheckpoint`), resumes the run
    *batch_run_arn* names when the lease carries one, and has its application fetched from *app_url*
    when the job names a stored one (BE-0448). The same *lost* flag that keeps a run from starting
    tells its poll to leave the run to the worker that holds the job now. A slot worker passes its
    own *lost*, which its shutdown sets to let the lease lapse; a fresh one when the caller has none.
    """
    holder: dict[str, Any] = {"work": work}
    lost = lost if lost is not None else threading.Event()
    cancel = cancel if cancel is not None else _CancelRelay()
    # The live relays of `record` and `crawl` (BE-0449 unit 8). Both are bound to the job once it
    # exists and stay idle for every other kind.
    relay_post = functools.partial(_post_relay, url, wid, job_id, auth_token)
    handoff = HandoffRelay(relay_post, cancel=cancel)
    artifacts = ArtifactStreamer(relay_post)
    if isinstance(bus, _TeeLogBus):
        bus.on_request = handoff.offer

    def on_job(job: Job) -> None:
        cancel.bind(job)
        handoff.bind(job)
        artifacts.bind(job, holder["work"])

    checkpoint = (
        _LeaseCheckpoint(
            url=url,
            auth_token=auth_token,
            wid=wid,
            job_id=job_id,
            stored=batch_run_arn if isinstance(batch_run_arn, str) and batch_run_arn else None,
            lost=lost,
            cancelled=lambda: cancel.requested,
        )
        if spec.get("batch") is not None
        else None
    )

    def _run() -> None:
        if (reason := environment_refusal(spec, environment)) is not None:
            holder["result"] = {"ok": False, "error": reason}
            return
        try:
            job_work, failure = _workspace_or_failure(
                work, spec, bundle_urls, override_urls, app_url=app_url
            )
        except _TransientFetch as e:
            _logger.warning(
                "bundle fetch failed for job %s; leaving the lease to lapse: %s", job_id, e
            )
            holder["transient"] = True
            return
        holder["work"] = job_work
        if failure is not None:
            holder["result"] = failure
            return
        if lost.is_set():  # the lease is already gone — never start the run
            return
        if cancel.requested:  # cancelled during the fetch — nothing has touched the device yet
            holder["result"] = cancelled_job_result(job_id)
            return
        try:
            job = execute_job_spec(
                spec,
                popen=subprocess.Popen,
                simctl=simctl.real_run,
                cwd=job_work,
                bus=bus,
                io=io,
                worker_config=worker_config,
                on_job=on_job,
                secret_env=secret_env,
                batch_checkpoint=checkpoint,
                lease_lost=lost,
                # Written before the run tree uploads, so console.log travels with it and is the
                # durable copy the server's log stream falls back to (BE-0449).
                before_upload=functools.partial(_write_job_console_log, job_work, bus),
            )
            result = job.view()
            result.pop("lines", None)
            holder["result"] = result
        except Exception as e:  # a worker keeps running past one job's failure
            _logger.exception("job %s failed", job_id)
            holder["result"] = {"ok": False, "error": str(e)}

    runner = threading.Thread(target=_run, daemon=True)
    runner.start()
    artifacts.start(log_flush_interval)
    try:
        abandoned = _beat_and_stream(
            runner,
            beat=lambda: _heartbeat(url, wid, job_id, auth_token, heartbeat_interval, cancel),
            interval=heartbeat_interval,
            lost=lost,
            streamer=streamer,
            log_flush_interval=log_flush_interval,
        )
    finally:
        handoff.stop()
        # The last snapshot goes out ahead of the result, which closes the job's event stream.
        artifacts.stop(final=not lost.is_set())

    if holder.get("transient"):
        return None, abandoned, holder["work"]
    result = holder.get("result", {"ok": False, "error": "worker produced no result"})
    return result, abandoned, holder["work"]


def _beat_and_stream(
    runner: threading.Thread,
    *,
    beat: Callable[[], int | None],
    interval: float,
    lost: threading.Event,
    streamer: _LogStreamer | None,
    log_flush_interval: float,
) -> bool:
    """`_beat_until_done`, with the live log flushed alongside; True when the lease was lost.

    The streamer is stopped whatever happens, so its thread never outlives the job. Its tail is
    posted here, before the caller posts the result, so the result route closes a stream that
    already holds every line; a heartbeat loop that raised or lost the lease posts no tail.
    """
    if streamer is None:
        return _beat_until_done(runner, beat=beat, interval=interval, lost=lost, streamer=None)
    abandoned: bool | None = None
    streamer.start(log_flush_interval)
    try:
        abandoned = _beat_until_done(
            runner, beat=beat, interval=interval, lost=lost, streamer=streamer
        )
    finally:
        streamer.stop(flush=abandoned is False)
    return abandoned or streamer.lost


def _beat_until_done(
    runner: threading.Thread,
    *,
    beat: Callable[[], int | None],
    interval: float,
    lost: threading.Event,
    streamer: _LogStreamer | None,
) -> bool:
    """Heartbeat every *interval* until *runner* ends; True when the lease was lost on the way.

    A 409 from the heartbeat or from the log route both mean another worker may now own the job.
    So does *lost* set from outside, by a slot worker's shutdown: the lease is left to lapse.
    """
    while runner.is_alive():
        runner.join(timeout=interval)
        if not runner.is_alive():
            return lost.is_set()
        if lost.is_set() or beat() == 409 or (streamer is not None and streamer.lost):
            lost.set()  # seen in time, this stops _run before it ever calls execute_job_spec
            runner.join()  # wait it out so this worker never runs two jobs at once
            return True
    # Reached when *runner* had already ended at the first check: a shutdown that set *lost* first
    # still leaves the lease to lapse, the same answer as the exit inside the loop.
    return lost.is_set()


def _heartbeat(
    url: str,
    wid: str,
    job_id: str,
    auth_token: str | None,
    interval: float,
    cancel: _CancelRelay,
) -> int | None:
    """Renew the lease once and relay a cancel it carries; the HTTP status, or None on a transport
    failure (logged), which the loop rides out until the next beat."""
    try:
        code, beat = _post_json(
            f"{url}/api/worker/heartbeat",
            {"worker_id": wid, "job_id": job_id},
            token=auth_token,
            # Bounded by one interval: without a timeout a stalled connection blocks the loop
            # indefinitely and the lease lapses anyway — the failure the loop exists to prevent,
            # arriving through the renewal path itself.
            timeout=interval,
        )
    except (URLError, OSError) as e:
        _logger.warning("heartbeat failed for job %s: %s", job_id, e)
        return None
    if code == 200 and isinstance(beat, dict) and beat.get("cancel") is True:
        # The loop keeps heart-beating afterwards: the row stays leased while a `run` winds down
        # gracefully, and only this worker's cancelled result finishes it (BE-0449).
        cancel.request()
    return code


def _write_job_console_log(work: Path, bus: InMemoryLogBus, job: Job) -> None:
    """`_write_console_log` for a finished job, when it produced a run.

    The file goes into the runs directory the run wrote, which is the workspace's for a job that
    shipped its files, and the server's own for a local start's run that names it by path (BE-0449),
    where the server reads it without an upload.
    """
    if job.run_id:
        runs = _runs_dir_of(job.cmd, job.cwd or work)
        _write_run_console_log(runs / job.run_id, bus, job.id)


def _write_console_log(work: Path, run_id: str, bus: InMemoryLogBus, job_id: str) -> None:
    """Write the job's buffered log to runs/<run_id>/console.log under *work* for upload."""
    _write_run_console_log(work / DEFAULT_RUNS_DIR / run_id, bus, job_id)


def _write_run_console_log(run_dir: Path, bus: InMemoryLogBus, job_id: str) -> None:
    """Write the job's buffered log to *run_dir*/console.log.

    The log is whatever the run printed, so it goes through the sink like every other run artifact
    (BE-0331). The redactor is inert here and only the sink's pattern backstop, which needs no
    configuration, reaches this text; the run masks its own `${secrets.X}` values as it prints them.
    """
    if not run_dir.is_dir():
        return
    lines = list(bus.stream(job_id, timeout=0.0))
    if not lines:
        return
    RunArtifactWriter(run_dir, Redactor(None)).write_text(
        "console.log",
        # The bus carries lines without their newline (`jobs.py` strips it), and a viewer that
        # missed the live stream replays this file line by line.
        "".join(line if line.endswith("\n") else f"{line}\n" for line in lines if line is not None),
    )


def _evidence_files(run_dir: Path) -> list[str]:
    """Relative POSIX paths of every real file under *run_dir* (the keys an upload endpoint signs).

    Shared by the artifact and evidence uploads. Symlinks and non-files are skipped, and each
    resolved path must stay under the run dir, so nothing outside the tree is offered for upload.
    """
    base = run_dir.resolve()
    files: list[str] = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            continue
        files.append(resolved.relative_to(base).as_posix())
    return files


def _put_file(url: str, path: Path, content_type: str, *, timeout: float | None = None) -> None:
    """Upload one file to a presigned PUT *url*, streaming it from disk.

    The Content-Type must match what the control plane signed into the URL (the presigned signature
    covers it), so send the same value. *timeout* bounds a stalled connection. The file is streamed
    (``http.client`` reads the open handle in blocks) with an explicit Content-Length, so a large run
    artifact like a video never loads wholly into memory.
    """
    headers = {"Content-Length": str(path.stat().st_size), "User-Agent": _USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    with path.open("rb") as body:
        req = Request(url, data=body, method="PUT", headers=headers)  # noqa: S310
        with urlopen(req, timeout=timeout):  # noqa: S310
            pass


def _get_file(url: str, dest: Path, *, timeout: float | None = None) -> None:
    """Download a presigned GET *url* into *dest*, streaming it to disk.

    Streamed rather than read whole: a baseline is a small image, but a bundle zip carries a built
    app binary and would otherwise sit in memory in full.
    """
    req = Request(url, method="GET", headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urlopen(req, timeout=timeout) as r, dest.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(r, out)


def _request_upload_urls(
    url: str, endpoint: str, body: dict[str, Any], auth_token: str | None
) -> dict[str, Any]:
    """Ask the control plane for a presigned PUT URL per file (BE-0110 evidence, BE-0160 artifacts).

    Returns the ``{rel: url}`` mapping. Raises on a transport/HTTP error or a malformed response, so
    the caller decides whether to swallow it (evidence, uploaded after the verdict) or fail the run
    (artifacts). An empty mapping means the destination isn't configured — nothing to upload.
    """
    code, resp = _post_json(
        f"{url}{endpoint}", body, token=auth_token, timeout=_UPLOAD_HTTP_TIMEOUT
    )
    if code != 200:
        raise RuntimeError(f"{endpoint} returned {code}")
    urls = resp.get("urls")
    if not isinstance(urls, dict):
        raise RuntimeError(f"{endpoint} returned an unexpected response shape")  # noqa: TRY004  # invalid external payload, not a caller type error
    return urls


def _put_tree_files(run_dir: Path, urls: dict[str, Any], *, best_effort: bool) -> int:
    """PUT each ``rel -> presigned url`` file under *run_dir*, returning how many uploaded.

    Each returned key is confined under *run_dir* and required to be a string URL, so a malformed or
    hostile response can't read files outside the tree. With *best_effort* a bad entry or a failed
    PUT is logged and skipped (evidence, already past the verdict); otherwise it raises — a report
    artifact the control plane can't serve must fail the run loudly, not vanish (BE-0160).
    """
    base = run_dir.resolve()
    uploaded = 0
    for rel, put_url in urls.items():
        # Require a string key + URL and confine the key under the run dir, so a malformed or hostile
        # response can neither crash the loop (a non-string key would blow up the path-join) nor read
        # a file outside the tree. Check the types before the join so a bad key hits this guard.
        ok = isinstance(rel, str) and isinstance(put_url, str)
        src = (run_dir / rel).resolve() if ok else base
        if not ok or not src.is_relative_to(base):
            if not best_effort:
                raise RuntimeError(f"unexpected upload entry {rel!r}")
            _logger.warning("skipping unexpected upload entry %r under %s", rel, run_dir)
            continue
        try:
            _put_file(put_url, src, content_type_for(rel), timeout=_UPLOAD_HTTP_TIMEOUT)
        except Exception:
            if not best_effort:
                raise
            _logger.warning("upload failed for %s", rel)
        else:
            uploaded += 1
    return uploaded


def _post_result(
    url: str, job_id: str, worker_id: str, result: dict[str, Any], auth_token: str | None
) -> None:
    """Post a finished (or unstartable) job's result; a transport failure is logged, never raised."""
    try:
        _post_json(
            f"{url}/api/worker/result",
            {"job_id": job_id, "result": result, "worker_id": worker_id},
            token=auth_token,
        )
    except (URLError, OSError):
        _logger.exception("result post failed for job %s", job_id)


def _safe_org(org: Any) -> str:
    """*org* reduced to one safe path segment for the bundle cache, or ``default`` when unusable.

    The org travels in the job spec, so it is server-authored — but it becomes a directory name here,
    and a leased spec is still remote input. An allowlist of the characters an org id may hold keeps
    a separator or a `..` out of the path rather than trusting the value's provenance.

    An org id is operator-authored (`orgs:` or the database) and already reaches object-store keys
    unsanitized through `org_prefix`, so a space, a non-ASCII character, or a 70-character id is legal
    upstream — and stripping it down here can't be allowed to collide two such ids onto one directory.
    An id returns as-is only when it is already a safe segment: nothing stripped, at most 64
    characters, and lowercase. Anything else — a stripped character, an over-long id, or any
    uppercase — is disambiguated by a digest of the *raw* value instead, so two ids that would
    otherwise share one segment (`"acme corp"` / `"acmecorp"`, two ids agreeing on their first 64
    characters, or `"Acme"` / `"acme"` on a case-insensitive filesystem, which is the default on a
    macOS worker) never share the mutable tree underneath — the isolation this cache exists to give
    each tenant.

    The digest encodes with ``surrogatepass`` because a lone surrogate survives `json.loads` into a
    `str` that strict UTF-8 refuses: without it this sanitizer would raise, and
    `_workspace_or_failure` would classify that as a permanent failure and post a red run for a
    scenario that never executed. Surrogates are the only strs strict UTF-8 rejects, and the
    error handler is injective over them, so collision resistance is unchanged.
    """
    raw = org if isinstance(org, str) else ""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", raw).strip(".")
    if cleaned == raw and cleaned == cleaned.lower() and len(cleaned) <= 64:
        return cleaned or "default"
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{cleaned[:51] or 'org'}-{digest[:12]}"


def _lease_secret_env(lease: dict[str, Any]) -> dict[str, str]:
    """The environment variables the lease relayed from the org's stored secrets (BE-0449).

    Only string names and values are kept, so a malformed lease cannot put anything but text into
    the child's environment. An older control plane sends none, and the job then runs with the
    worker's own environment alone, as before.
    """
    raw = lease.get("secret_env")
    if not isinstance(raw, dict):
        return {}
    # A control plane is trusted to send values, never to rewrite how this worker's own processes
    # load and run, so a variable on the deny list is dropped whoever sent it.
    kept: dict[str, str] = {}
    for k, v in raw.items():
        if not (isinstance(k, str) and k and isinstance(v, str)):
            continue
        if unsafe_env_name(k):
            warn_unsafe_env_name(k, "the lease's secret_env")
            continue
        kept[k] = v
    return kept


def _override_urls(lease: dict[str, Any]) -> dict[str, str]:
    """The lease's signed override GETs (``binary_url`` / ``scenarios_url``), keyed by kind."""
    return {
        kind: url for kind in OVERRIDE_KINDS if isinstance(url := lease.get(f"{kind}_url"), str)
    }


def _workspace_or_failure(
    work: Path,
    spec: dict[str, Any],
    bundle_urls: Any,
    override_urls: dict[str, str] | None = None,
    *,
    app_url: Any = None,
) -> tuple[Path, dict[str, Any] | None]:
    """The workspace to run this job from, or the failed result to post when none can be prepared.

    A job whose bundle cannot be fetched for a reason no worker can ever get past — an invalid
    bundle id, a lease that signed no url, a bundle that fails validation — is reported as failed
    rather than left to crash the poll loop: the control plane would otherwise keep re-leasing a job
    this worker can never start, and the user would wait on a run that never returns a verdict. The
    returned path is meaningless when a failure comes back with it.

    Raises `_TransientFetch` instead when the *download* failed — a reset connection, a stalled
    socket, a body that arrived truncated: another worker, or this one a minute later, would likely
    succeed, so the caller lets the lease lapse rather than turning one network blip into a
    permanent failed job. Everything after the download is deterministic over verified bytes, so it
    keeps the permanent classification.
    """
    try:
        base = _bundle_workspace(work, spec, bundle_urls)
        _place_batch_app(base, spec, app_url)
    except _TransientFetch:
        raise
    except Exception as e:
        _logger.exception("could not materialize the job's bundle")
        return work, {"ok": False, "error": f"bundle unavailable: {e}"}
    try:
        return _override_workspace(work, base, spec, override_urls or {}), None
    except _TransientFetch:
        raise
    except Exception as e:
        _logger.exception("could not place the job's artifact overrides")
        return work, {"ok": False, "error": f"artifact override unavailable: {e}"}


def _job_overrides(spec: dict[str, Any]) -> ArtifactOverrides | None:
    """The job's per-job artifact overrides (BE-0431), re-validated, or None when it names none.

    Server-authored, but each sha becomes a directory key and is checked against a download, and a
    leased spec is still remote input.
    """
    raw = spec.get("overrides")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        # The spec's provenance still names the overrides, so running the binding would misreport it.
        raise RuntimeError(f"job carries malformed overrides: {raw!r}")  # noqa: TRY004  # invalid external payload, not a caller type error
    shas = {kind: raw.get(kind) for kind in OVERRIDE_KINDS if raw.get(kind) is not None}
    if not shas:
        return None
    for kind, sha in shas.items():
        if not valid_sha256(sha):
            raise RuntimeError(f"job carries an invalid {kind} override: {sha!r}")
    target = raw.get("target")
    if not isinstance(target, str) or not target:
        raise RuntimeError("job carries artifact overrides but names no target")
    return ArtifactOverrides(target, binary=shas.get("binary"), scenarios=shas.get("scenarios"))


def _override_workspace(work: Path, base: Path, spec: dict[str, Any], urls: dict[str, str]) -> Path:
    """The tree a job carrying artifact overrides runs from, or *base* when it carries none.

    The overrides are placed into a tree of the job's own — a copy of the cached bundle's, or a
    fresh directory for a materials-based job — never into *base*: *base* is shared with every
    later job off the same bundle, or is the worker's own working directory, and an override left
    there would reach the next job with nothing announcing it. The tree is keyed by what it was built
    from, the target, and the overrides' identity, so an identical job reuses it and two jobs that
    differ in any of them never share one. Built aside and renamed into place, so a failed fetch or
    placement leaves nothing a later job could mistake for a finished tree.
    """
    overrides = _job_overrides(spec)
    if overrides is None:
        return base
    bundle = spec.get("bundle")
    raw_materials = spec.get("materials")
    materials: dict[str, str] = raw_materials if isinstance(raw_materials, dict) else {}
    source = (
        bundle["id"]
        if isinstance(bundle, dict)
        # Only the config decides where the overrides land; every other material is re-written into
        # the tree by `execute_job_spec` on each run, so keying on them would re-fetch the binary
        # once per scenario of a single CI fan-out.
        else hashlib.sha256(materials.get("bajutsu.config.yaml", "").encode()).hexdigest()
    )
    key = hashlib.sha256(f"{source}:{overrides.target}:{overrides.identity}".encode()).hexdigest()
    cache = work / _OVERRIDE_CACHE_DIR / _safe_org(spec.get("org"))
    tree = cache / key
    if tree.exists():
        return tree
    for kind, sha in overrides.shas.items():
        if kind not in urls:
            raise RuntimeError(
                f"job needs {kind} artifact {sha}, but the lease signed no url for it"
            )
    cache.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=cache, prefix=f".{key}.tmp-"))
    try:
        if isinstance(bundle, dict):
            # Copy the config's tree without the runs earlier jobs wrote into it: an override is a
            # local copy of the cached bundle, never a second download of it.
            shutil.copytree(
                base,
                tmp,
                dirs_exist_ok=True,
                ignore=lambda d, _names: [DEFAULT_RUNS_DIR] if Path(d) == base else [],
            )
        else:
            # Only the config material is keyed (see `source` above); writing the rest here too
            # would let one job's scenario text leak into a tree a later, differently-scenario'd
            # job reuses, since nothing else refreshes it once the tree exists.
            _materialize(tmp, {"bajutsu.config.yaml": materials.get("bajutsu.config.yaml", "")})
        with tempfile.TemporaryDirectory(dir=cache, prefix=".fetch-") as raw:
            parts: dict[str, Path] = {}
            for kind, sha in overrides.shas.items():
                parts[kind] = Path(raw) / kind
                _fetch_part(urls[kind], parts[kind], sha)
            place_overrides(
                tmp, overrides.target, binary=parts.get("binary"), scenarios=parts.get("scenarios")
            )
        try:
            tmp.rename(tree)
        except OSError:
            # A concurrent build of the same key won the rename; its tree is equivalent, so drop ours.
            if not tree.exists():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tree


def _bundle_workspace(work: Path, spec: dict[str, Any], bundle_urls: Any) -> Path:
    """The directory to run this job from: the uploaded bundle's root, or *work* when it ships none.

    A hosted `serve` binds an uploaded zip (or a composed triple) whose config names its `appPath`
    binary, its scenarios, and its baselines relative to the bundle root. The worker holds no project
    on disk, so it fetches what the lease signed and rebuilds that tree here. Running the job *from
    the bundle root* is what makes every one of those relative paths resolve, with no rewriting on
    either side.

    The tree is keyed by the bundle id, so a second job off the same bundle reuses it and fetches
    nothing. One tree therefore serves every job off one bundle, and each run writes its `runs/` and
    console log inside it — the worker already shares one working directory across jobs, and this
    keeps that property rather than paying a copy of an app binary per job.

    A cloud-batch job that ships no bundle gets a fresh tree of its own instead (BE-0448).

    Raises rather than falling back to *work*: a run started against a missing binary fails opaquely
    at install time, far from this cause (directive 2).
    """
    bundle = spec.get("bundle")
    if not isinstance(bundle, dict):
        return _batch_workspace(work, spec) if spec.get("batch") else work
    bundle_id = bundle.get("id")
    if not valid_sha256(bundle_id):
        # Server-authored, so this is purely defensive — but the id becomes a directory name below,
        # and a leased job spec is still remote input.
        raise RuntimeError(f"job carries an invalid bundle id: {bundle_id!r}")
    urls = bundle_urls if isinstance(bundle_urls, dict) else {}
    # Scoped per org, mirroring the control plane's own `_org_uploads_dir` / `_org_compositions_dir`.
    # A content-derived id makes a cross-org hit imply identical bytes, so what this buys is not
    # isolation of the bundle: the tree is *mutable* — each run writes its `runs/` inside it — and one
    # tenant's run evidence has no business landing in another tenant's directory.
    cache = work / _BUNDLE_CACHE_DIR / _safe_org(spec.get("org"))
    tree = cache / bundle_id
    if not tree.exists():
        if not urls:
            # The lease signed nothing: a control plane with no object store configured has nowhere
            # to have stored this bundle, so no worker can ever run the job. Say that here.
            raise RuntimeError(f"job needs bundle {bundle_id}, but the lease signed no url for it")
        tree = _fetch_bundle(cache, bundle_id, bundle, urls)
    config = find_bundle_config(tree)
    if config is None:
        raise RuntimeError(f"bundle {bundle_id} holds no bajutsu.config.yaml")
    return config.parent


def _batch_workspace(work: Path, spec: dict[str, Any]) -> Path:
    """A fresh directory of this cloud-batch job's own under *work* (see `_BATCH_JOBS_DIR`)."""
    job_id = str(spec.get("job_id") or "")
    if not _SAFE_SEGMENT.fullmatch(job_id):
        raise RuntimeError(f"job carries an id that cannot name a directory: {job_id!r}")
    tree = work / _BATCH_JOBS_DIR / job_id
    # A re-leased job may find the tree of its earlier attempt; a stale material would ride along.
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    return tree


def _place_batch_app(tree: Path, spec: dict[str, Any], app_url: Any) -> None:
    """Fetch the stored application a cloud-batch job names into *tree*, at its `appPath` (BE-0448).

    The job ships its config and scenario as materials and no bundle, so the lease's signed URL is
    the only way its binary reaches this worker. The `appPath` comes from a leased spec, so it must
    name a place strictly inside *tree*; the bytes must hash to the sha the job names, as a bundle
    part must. A job naming no stored application, or carrying a bundle, is left alone, so *tree* is
    always the job's own fresh workspace (`_batch_workspace`).

    Raises:
        RuntimeError: The job names an invalid sha or an `appPath` outside *tree*, or the lease
            signed no URL for it; no worker can get past any of these.
        _TransientFetch: The download failed or arrived corrupted; another attempt may succeed.
    """
    sha = spec.get("batch_app")
    # A bundle's tree is shared with every later job off it, so nothing is placed into one.
    if sha is None or spec.get("bundle") is not None:
        return
    if not valid_sha256(sha):
        raise RuntimeError(f"job carries an invalid application sha: {sha!r}")
    batch = spec.get("batch")
    rel = batch.get("app_path") if isinstance(batch, dict) else None
    if not isinstance(rel, str) or not rel:
        raise RuntimeError("job names a stored application but no batch.app_path to place it at")
    dest = tree / rel
    if Path(rel).is_absolute() or tree.resolve() not in dest.resolve().parents:
        raise RuntimeError(f"application path {rel!r} is outside this job's workspace")
    if not isinstance(app_url, str) or not app_url:
        raise RuntimeError(f"job needs application {sha}, but the lease signed no url for it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _fetch_part(app_url, dest, sha)


class _CheckpointLostError(RuntimeError):
    """The server answered 409 (or the heartbeat saw it): this worker no longer holds the lease."""


class _CheckpointRefusedError(RuntimeError):
    """The server refused the ARN with a 4xx other than 409: a server or configuration error."""


class _CheckpointTimeoutError(RuntimeError):
    """The server could not be reached to store the ARN before `_CHECKPOINT_DEADLINE` ran out."""


class _LeaseCheckpoint:
    """A leased cloud-batch job's `BatchCheckpoint`, kept by the server (BE-0448).

    `load` answers the ARN the lease carried, so a re-leased job resumes the run an earlier attempt
    scheduled. `save` posts the new ARN to the lease-owner-checked route and retries until the
    server stores it, refuses it, or the retry deadline runs out; the worker never holds a database
    connection for it. Every way `save` gives up raises, and the provider then stops the run: a run
    whose ARN the server does not hold cannot be resumed by anyone, so it must not keep a device.
    """

    def __init__(
        self,
        *,
        url: str,
        auth_token: str | None,
        wid: str,
        job_id: str,
        stored: str | None,
        lost: threading.Event,
        cancelled: Callable[[], bool] = lambda: False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        deadline: float = _CHECKPOINT_DEADLINE,
    ) -> None:
        self._url = url
        self._auth_token = auth_token
        self._wid = wid
        self._job_id = job_id
        self._stored = stored
        self._lost = lost
        self._cancelled = cancelled
        self._sleep = sleep
        self._clock = clock
        self._deadline = deadline

    def load(self) -> str | None:
        """The run ARN the lease carried, or the one this attempt stored."""
        return self._stored

    def save(self, run_arn: str) -> None:
        """Store *run_arn* on the server, retrying a transport failure or a server error.

        Raises:
            RunInterrupted: The job was cancelled while the save was retrying.
            _CheckpointLostError: The lease is gone, by the route's 409 or by the heartbeat's.
            _CheckpointRefusedError: The route answered another 4xx, which no retry changes.
            _CheckpointTimeoutError: The deadline ran out before the server answered.
        """
        give_up_at = self._clock() + self._deadline
        delay = _CHECKPOINT_RETRY_INITIAL
        while True:
            # Checked on every attempt, so a cancel never waits out the backoff.
            if self._cancelled():
                raise RunInterrupted("stop", run_arn)
            # A lost lease still asks once more: the route answers 200 for an ARN it already holds,
            # so a save whose answer was lost before a reclaim must not stop a run the next
            # holder's lease already carries (`worker_batch_run`). The 409 or the check below the
            # refusals then ends it without waiting out the backoff.
            code, body = self._post(run_arn)
            if code == 200:
                self._stored = run_arn
                return
            if code == 409:
                raise _CheckpointLostError(
                    f"lease on job {self._job_id} lost before run {run_arn} was stored"
                )
            if code is not None and 400 <= code < 500:
                # Not a lease question: the route is missing, the token is refused, or the request
                # is malformed. Durability cannot be had, so the run stops and the job fails loudly.
                _logger.error(
                    "server refused to store run %s for job %s (HTTP %s): a server or "
                    "configuration error, not a lost lease: %.200s",
                    run_arn,
                    self._job_id,
                    code,
                    body,
                )
                raise _CheckpointRefusedError(
                    f"server or configuration error storing run {run_arn} for job "
                    f"{self._job_id}: HTTP {code}: {body!s:.200}"
                )
            if self._lost.is_set():
                raise _CheckpointLostError(
                    f"lease on job {self._job_id} lost before run {run_arn} was stored"
                )
            if self._clock() + delay > give_up_at:
                raise _CheckpointTimeoutError(
                    f"could not store run {run_arn} for job {self._job_id} within "
                    f"{self._deadline:.0f}s (last: {code if code is not None else body})"
                )
            _logger.warning(
                "storing run %s for job %s failed (%s); retrying in %.0fs",
                run_arn,
                self._job_id,
                code if code is not None else body,
                delay,
            )
            self._sleep(delay)
            delay = min(delay * 2, _CHECKPOINT_RETRY_MAX)

    def _post(self, run_arn: str) -> tuple[int | None, Any]:
        """One attempt at the route: its status and body, or None and the transport error."""
        try:
            return _post_json(
                f"{self._url}/api/worker/batch-run",
                {"worker_id": self._wid, "job_id": self._job_id, "run_arn": run_arn},
                token=self._auth_token,
                timeout=_UPLOAD_HTTP_TIMEOUT,
            )
        except (URLError, OSError) as e:
            return None, str(e)


def _discard_batch_workspace(work: Path, job_work: Path) -> None:
    """Remove a cloud-batch job's own directory once its result and evidence are posted."""
    if job_work.parent == work / _BATCH_JOBS_DIR:
        shutil.rmtree(job_work, ignore_errors=True)


def _expected_digest(bundle: dict[str, Any], name: str) -> str | None:
    """The sha256 the fetched *name* part must hash to, or None when the job names none for it.

    Both bind kinds hand over a real content digest: a single-zip bind's `id` *is* its zip's sha256,
    and a composed triple names one per leg. A composed bind's own `id` is a derived composition key
    with no single file behind it, so it is deliberately not used as a part digest here.
    """
    artifacts = bundle.get("artifacts")
    if artifacts is None:
        return bundle["id"] if name == "bundle" else None
    return artifacts.get(name) if isinstance(artifacts, dict) else None


def _fetch_part(url: str, dest: Path, expected_sha256: str | None) -> None:
    """Download one bundle part and prove it arrived whole, or raise `_TransientFetch`.

    The digest check is what makes a truncated download visible at all: a short body is *not* an
    error to `http.client`, which drops `IncompleteRead` on a `Content-Length` mismatch rather than
    raising, so `_get_file` returns happily with a partial file. Left unchecked, a truncated zip
    surfaces as a permanent "invalid bundle" — the network blip landing in the verdict by the other
    door — and a truncated raw binary (an `.ipa`, an `.apk`) is worse still: nothing downstream reads
    its bytes, so the corrupt tree is committed to the cache and every later job off that bundle
    reuses it without re-downloading.

    A `404`/`410` is re-raised untouched, so the caller reports it: the object is not there and no
    retry will conjure it.
    """
    try:
        _get_file(url, dest, timeout=_UPLOAD_HTTP_TIMEOUT)
    except HTTPError as e:
        if e.code in _GONE_STATUSES:
            raise
        raise _TransientFetch(f"HTTP {e.code} fetching {dest.name}") from e
    except (URLError, OSError) as e:
        raise _TransientFetch(f"could not fetch {dest.name}: {e}") from e
    if expected_sha256 is None:
        return
    digest = hashlib.sha256()
    with dest.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise _TransientFetch(
            f"{dest.name} did not match its digest (truncated or corrupted in transit)"
        )


def _fetch_bundle(
    cache: Path, bundle_id: str, bundle: dict[str, Any], urls: dict[str, Any]
) -> Path:
    """Download the bundle's stored objects and rebuild its tree at ``cache/<bundle_id>``.

    A single-zip bind arrives as one ``bundle`` zip and goes through `materialize_bundle`; a composed
    triple arrives as its legs and goes through `materialize_composition`. Both are the control
    plane's own functions, so the worker reproduces its tree by running that code rather than
    re-implementing it, and both apply `validate_bundle_config` — the check that confines every
    target's paths to the tree (BE-0051).

    The downloads land in a temporary directory that is removed either way: the rebuilt tree is what
    persists, and a half-fetched set of parts must not look like a cache entry.
    """
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix=".fetch-") as raw:
        parts: dict[str, Path] = {}
        for name, url in urls.items():
            # Control-plane-authored names, re-checked because a lease response is remote input and
            # each one is joined onto a path below.
            if name not in _BUNDLE_PART_NAMES or not isinstance(url, str):
                raise RuntimeError(f"unexpected bundle part {name!r} in the lease response")
            parts[name] = Path(raw) / name
            _fetch_part(url, parts[name], _expected_digest(bundle, name))
        if bundle.get("artifacts") is None:
            if "bundle" not in parts:
                raise RuntimeError(f"bundle {bundle_id} was signed with no zip to fetch")
            return materialize_bundle(
                parts["bundle"], cache, bundle_id, validate=validate_bundle_config
            )
        if "config" not in parts:
            raise RuntimeError(f"composed bundle {bundle_id} was signed with no config artifact")
        return materialize_composition(
            parts["config"],
            parts.get("scenarios"),
            parts.get("binary"),
            compositions_dir=cache,
            composition_id=bundle_id,
            scenarios_filename=bundle.get("scenarios_filename"),
        )


def _download_baselines(work: Path, baseline_urls: dict[str, Any]) -> None:
    """Download each ``name -> presigned GET url`` baseline into ``work/baselines`` before the run.

    The dir is cleared first — the workspace is reused across jobs, so a baseline renamed/removed in
    storage must not linger and skew the comparison — and each name is confined under it. A download
    failure raises: a run that silently dropped its visual baselines would compare against nothing.
    """
    baselines = work / "baselines"
    if baselines.exists():
        shutil.rmtree(baselines, ignore_errors=True)
    if not baseline_urls:
        return
    base = baselines.resolve()
    for name, get_url in baseline_urls.items():
        # The control plane signs only safe baseline names, so a non-string name/URL or an escaping
        # name is a broken/hostile lease: fail loudly rather than silently drop a baseline (which
        # would leave the run comparing against nothing). Validate the types before the path-join so
        # a bad name raises this RuntimeError, not a TypeError, and never place a file outside the dir.
        if not isinstance(name, str) or not isinstance(get_url, str):
            raise RuntimeError(f"baseline {name!r} has a non-string name or URL")  # noqa: TRY004  # invalid external payload, not a caller type error
        dest = (baselines / name).resolve()
        if base not in dest.parents:
            raise RuntimeError(f"baseline {name!r} escapes the baselines dir")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _get_file(get_url, dest, timeout=_UPLOAD_HTTP_TIMEOUT)


class PresignedWorkerIO:
    """The worker's object I/O over the control plane's presigned URLs (BE-0160), the `WorkerIO` seam.

    Holds no cloud credentials — only the control-plane URL, the operator token, the leased job id,
    and the signed baseline GET URLs the lease returned. The org is fixed server-side from the leased
    job, so this can never touch another tenant's prefix. Uploads fail loudly (they feed the report),
    unlike the best-effort post-verdict evidence upload.
    """

    def __init__(
        self, *, url: str, auth_token: str | None, job_id: str, worker_id: str, baseline_urls: Any
    ) -> None:
        self._url = url
        self._token = auth_token
        self._job_id = job_id
        self._worker_id = worker_id
        self._baseline_urls = baseline_urls if isinstance(baseline_urls, dict) else {}

    def download_baselines(self, work: Path) -> None:
        _download_baselines(work, self._baseline_urls)

    def upload_run(self, work: Path, run_id: str) -> None:
        run_dir = work / DEFAULT_RUNS_DIR / run_id
        if not run_dir.is_dir():
            return
        files = _evidence_files(run_dir)
        if not files:
            return
        urls = _request_upload_urls(
            self._url,
            "/api/worker/artifact-urls",
            {
                "job_id": self._job_id,
                "worker_id": self._worker_id,
                "run_id": run_id,
                "files": files,
            },
            self._token,
        )
        _put_tree_files(run_dir, urls, best_effort=False)

    def save_scenario(self, work: Path, out_path: str, app: str, ref: str) -> None:
        src = (work / out_path).resolve()
        # Confine to the workspace: a crafted spec with an absolute / `..` out_path must not read &
        # upload a host file outside it (the control plane never builds such a path).
        if work.resolve() not in src.parents:
            return
        # A `record` job that reached here was expected to author a scenario; if the file is missing,
        # fail loudly rather than report success having persisted nothing (BE-0160 / fail loud).
        if not src.is_file():
            raise RuntimeError(f"record job authored no scenario at {out_path!r}")
        code, resp = _post_json(
            f"{self._url}/api/worker/scenario-url",
            {"job_id": self._job_id, "worker_id": self._worker_id, "app": app, "ref": ref},
            token=self._token,
            timeout=_UPLOAD_HTTP_TIMEOUT,
        )
        if code != 200:
            raise RuntimeError(f"scenario-url returned {code}")
        put_url = resp.get("url")
        if not isinstance(put_url, str):
            raise RuntimeError("scenario-url returned no URL")  # noqa: TRY004  # invalid external payload, not a caller type error
        _put_file(put_url, src, content_type_for(ref), timeout=_UPLOAD_HTTP_TIMEOUT)


def _upload_evidence(
    work: Path, run_id: str, *, url: str, auth_token: str | None, evidence_prefix: str
) -> None:
    """Ask the control plane for presigned PUT URLs for this run's tree and upload each file.

    Best-effort by design (BE-0110): the run's result is already posted, so any failure here is
    logged and dropped, never raised, and every HTTP call is time-bounded so a stall can't strand the
    worker. When no evidence store is configured the endpoint returns no URLs and this uploads nothing.
    """
    run_dir = work / DEFAULT_RUNS_DIR / run_id
    if not run_dir.is_dir():
        return
    files = _evidence_files(run_dir)
    if not files:
        return
    try:
        urls = _request_upload_urls(
            url,
            f"/api/runs/{run_id}/upload-urls",
            {"files": files, "evidence_prefix": evidence_prefix},
            auth_token,
        )
    except Exception as e:
        _logger.warning("evidence upload-urls failed for run %s: %s", run_id, e)
        return
    uploaded = _put_tree_files(run_dir, urls, best_effort=True)
    if uploaded:
        typer.echo(f"  uploaded {uploaded} evidence file(s) for run {run_id}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(worker)
