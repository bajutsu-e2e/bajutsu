"""CLI command builders for ``bajutsu serve``.

Split from `serve/helpers.py` (BE-0206): these build the ``python -m bajutsu …`` argv a serve
request spawns, rendering every flag from the CLI's own option metadata (BE-0134) so the argv can't
drift from the CLI. They depend on nothing else in the serve package but `_cli_flags.flag_args`, and
are free of server state — fully unit-testable on their own.
"""

from __future__ import annotations

import sys
from typing import Any

from bajutsu.serve._cli_flags import flag_args


def _int(value: Any, default: int) -> int:
    """Coerce a JSON value to int, falling back to *default* (e.g. for ``workers``)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_command(
    scenario: str,
    target: str,
    *,
    backend: str = "",
    udid: str = "",
    workers: int = 1,
    erase: bool | None = None,
    alert_handling: bool | None = None,
    config: str = "bajutsu.config.yaml",
    baselines: str = "",
    headed: bool | None = None,
    runs_dir: str = "",
    upload_exec: str = "",
    browser: str = "",
    browsers: str = "",
    tag: str = "",
    exclude: str = "",
    schemas: str = "",
    goldens: str = "",
    network: bool | None = None,
    log_predicate: str = "",
    log_subsystem: str = "",
    alert_instruction: str = "",
    zip_run: bool | None = None,
    config_offline: bool | None = None,
    require_pinned_config: bool | None = None,
) -> list[str]:
    """The ``python -m bajutsu run ...`` argv for a launch request.  ``udid`` may be a comma
    list and ``workers > 1`` runs those devices as a parallel pool (capped to the pool size by
    the CLI).  ``erase`` / ``alert_handling`` / ``headed`` / ``network`` are overrides: True/False
    force the flag on/off, None omits it so the CLI's own default applies — for ``erase`` /
    ``alert_handling`` that means each scenario's preconditions.erase / alertHandling, for
    ``headed`` the target's ``headless`` config, and for ``network`` the ``--network`` default (on).
    ``runs_dir`` (when set) points the run's output tree elsewhere via ``--runs-dir`` — an uploaded
    bundle runs from its own extracted dir (the working directory) but must still write its run into
    serve's runs store (BE-0073).  Every flag is rendered from ``run``'s own option metadata
    (BE-0134), so this argv can't drift from the CLI."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        scenario,
        "--target",
        target,
        "--config",
        config,
        "--progress",
    ]  # stream per-scenario/step progress into the run log
    cmd += flag_args(
        "run",
        {
            "backend": backend,
            "udid": udid,
            # --workers 1 is the CLI default; omit it (a single device isn't a pool).
            "workers": workers if workers > 1 else None,
            "erase": erase,
            "alert_handling": alert_handling,
            "headed": headed,
            "baselines": baselines,
            "runs_dir": runs_dir,
            "upload_exec": upload_exec,
            "browser": browser,
            "browsers": browsers,
            "tag": tag,
            "exclude": exclude,
            "schemas": schemas,
            "goldens": goldens,
            "network": network,
            "log_predicate": log_predicate,
            "log_subsystem": log_subsystem,
            "alert_instruction": alert_instruction,
            "zip_run": zip_run,
            "config_offline": config_offline,
            "require_pinned_config": require_pinned_config,
        },
    )
    return cmd


def record_command(
    out: str,
    target: str,
    goal: str,
    *,
    backend: str = "",
    udid: str = "",
    erase: bool | None = None,
    alert_handling: bool | None = None,
    headed: bool | None = None,
    config: str = "bajutsu.config.yaml",
    upload_exec: str = "",
) -> list[str]:
    """The ``python -m bajutsu record --out OUT --target … --goal …`` argv for an authoring request —
    the Tier-1 record loop the Record tab drives.  ``erase`` / ``alert_handling`` mirror
    ``run_command`` (None leaves the CLI default — record erases and handles alerts by default), and
    ``out`` is the ``*.yaml`` the recorded scenario is written to. The AI provider is inherited from
    the serve process's environment (`BAJUTSU_AI_PROVIDER`, BE-0163)."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "record",
        "--out",
        out,
        "--target",
        target,
        "--goal",
        goal,
        "--config",
        config,
        # A human is at the browser watching this record; a "needs human" turn hands off to the UI
        # over the SSE stream + response endpoint rather than blocking on a terminal stdin (BE-0179).
        "--handoff",
        "stream",
    ]
    cmd += flag_args(
        "record",
        {
            "backend": backend,
            "udid": udid,
            "erase": erase,
            "alert_handling": alert_handling,
            "headed": headed,
            "upload_exec": upload_exec,
        },
    )
    return cmd


def crawl_command(
    target: str,
    *,
    out: str,
    backend: str = "",
    udid: str = "",
    workers: int = 1,
    max_screens: int = 50,
    max_steps: int = 200,
    erase: bool | None = None,
    alert_handling: bool | None = None,
    headed: bool | None = None,
    config: str = "bajutsu.config.yaml",
    resume_src: str = "",
    resume_key: str = "",
    continue_crawl: bool = False,
    upload_exec: str = "",
) -> list[str]:
    """The ``python -m bajutsu crawl --target … --out …`` argv for a crawl request — the explorer the
    Crawl tab drives.  ``out`` is the run dir the screen map is streamed into
    (``<out>/screenmap.json``, which the UI polls live); ``erase`` mirrors ``run_command`` (None
    leaves the CLI default — crawl erases by default). ``udid`` may be a comma list and
    ``workers > 1`` crawls with that many workers at once, sharing one screen map: across that many
    simulators on iOS (BE-0064, capped to the pool size by the CLI) or that many browser processes
    on web (BE-0077). Crawl is AI-driven; the AI provider is inherited from the serve process's
    environment (`BAJUTSU_AI_PROVIDER`, BE-0163). When ``resume_src`` / ``resume_key`` are set,
    ``out`` points at an existing run and the crawl resumes one pruned branch, appending to that
    run's map instead of starting a fresh one; ``continue_crawl`` instead continues that run's whole
    remaining frontier (BE-0181, mutually exclusive with the resume keys)."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "crawl",
        "--target",
        target,
        "--out",
        out,
        "--config",
        config,
        "--max-screens",
        str(max_screens),
        "--max-steps",
        str(max_steps),
    ]
    # Resuming appends to the existing run, so force --no-erase (don't wipe the app state mid-walk)
    # and carry the resume keys; a fresh crawl or a full-frontier continuation leaves erase to the
    # override (a continuation re-derives its frontier from a clean baseline, like a fresh crawl).
    resuming = bool(resume_src and resume_key)
    cmd += flag_args(
        "crawl",
        {
            "backend": backend,
            "udid": udid,
            "workers": workers if workers > 1 else None,
            "erase": False if resuming else erase,
            "alert_handling": alert_handling,
            "headed": headed,
            "upload_exec": upload_exec,
            "resume_src": resume_src if resuming else "",
            "resume_key": resume_key if resuming else "",
            "continue_crawl": continue_crawl,
        },
    )
    return cmd


def triage_command(
    run_dir: str,
    *,
    target: str = "",
    ai: bool = False,
    apply_path: str = "",
    json_out: str = "",
    config: str = "bajutsu.config.yaml",
) -> list[str]:
    """The ``python -m bajutsu triage <run_dir> …`` argv for a serve triage job (BE-0147).

    Diagnoses the failed run at ``run_dir`` — the deterministic heuristic agent by default;
    ``ai`` opts into the Claude investigator, which reads provider config from the serve
    environment like record/crawl (`BAJUTSU_AI_PROVIDER`, BE-0163) and, with ``target`` set, that
    target's ``ai`` block and redaction rules (BE-0047). ``apply_path`` is the scenario **source**
    file a structured fix is previewed against — a dry-run diff the job never writes; the human
    applies it through the validated scenario-save path. ``json_out`` is where the machine-readable
    result is written for the UI to read back. Every flag renders from the CLI's own option metadata
    (BE-0134), so this argv can't drift from `bajutsu triage`."""
    cmd = [sys.executable, "-m", "bajutsu", "triage", run_dir, "--config", config]
    cmd += flag_args(
        "triage",
        {
            "target_name": target,
            "ai": ai,
            "apply": apply_path,
            "json_out": json_out,
        },
    )
    return cmd
