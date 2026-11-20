#!/usr/bin/env python3
"""Per-job CI telemetry for the on-device E2E lanes, and the cross-run report built from it.

An on-device E2E job that flakes leaves its evidence in four places: the run manifests, the
diagnostics BE-0361 / BE-0367 collect under `runs/diagnostics/` (free-text host samples, a render
probe, stall captures), the job log (channel retries, unconfirmed recordings), and the GitHub API
(how long each step took, how long the job queued). Each answers one question about one job. None
of them answers the question a recurring flake raises: *which factor do the red jobs share that the
green ones do not?* That needs one machine-readable record per job, keyed so records from many runs
line up, and a report that compares them.

Three subcommands, split by where they run:

  summarize   in CI, on every job, as the diagnostics `collect` phase's last step. Reads only what
              is already on the runner — no device call, no network — and writes
              `ci-telemetry.json` plus a copy of each run manifest into one small directory that
              the job uploads as its own artifact, so the report never downloads videos.
  fetch       offline, by an operator. Lists a workflow's recent runs with `gh`, downloads each
              run's telemetry artifacts, and joins each record with what only the API knows: the
              job's conclusion, its queue time, every step's duration and conclusion, and the
              signals counted from the job log.
  report      offline. Groups the fetched records and prints, for each candidate cause, how often
              its signal shows up on red jobs against green ones, and failure rates by runner
              image, hardware, toolchain version, and hour of day.

Everything here observes. The candidate causes are fixed threshold rules over measured numbers,
written into the record as a hint for whoever reads the report; they never reach a verdict, and
the scenario's pass/fail stays the deterministic runner's alone. Standard library only, so the
`summarize` step runs on the runner's system `python3` before (or without) any environment sync.

Usage:
    python3 scripts/ci_telemetry.py summarize --lane ios [--runs runs] [--out DIR]
    python3 scripts/ci_telemetry.py fetch --workflow ios-e2e.yml [--limit 30] [--out DIR]
    python3 scripts/ci_telemetry.py report DIR [--json]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence

# `timezone.utc`, not `datetime.UTC`: `summarize` runs on the runner's system `python3`, which can
# be as old as 3.9 (the interpreter Xcode ships), so this file sticks to what 3.9 offers.
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RECORD_NAME = "ci-telemetry.json"
ARTIFACT_PREFIX = "ci-telemetry-"
DEFAULT_OUT = "runs/diagnostics/ci-telemetry"

# --- Thresholds behind the candidate causes --------------------------------------------------
# Each is a level past which the measured number is worth a reader's attention, not a proof. The
# report shows how often each rule fires on green jobs as well as red ones, so a threshold set too
# low reads as "fires everywhere" there instead of silently blaming the host for every flake.
LOAD_PER_CPU_HIGH = 1.5  # one-minute load average per logical CPU
CPU_IDLE_LOW = 5.0  # percent idle in a measured (two-sample) interval
MEM_FREE_LOW = 10.0  # percent of memory free or available
IOWAIT_HIGH = 20.0  # percent of CPU time waiting on I/O (Linux)
STEAL_HIGH = 10.0  # percent of CPU time stolen by the hypervisor (Linux)
RENDER_SCREENSHOT_SLOW = 10.0  # seconds for the pre-run `simctl` screenshot
QUEUE_SLOW = 300.0  # seconds a job waited for a runner
DEVICE_BOOT_SLOW = 240.0  # seconds spent in a step that boots or waits for the device

# Steps whose name says they boot or wait for the device. Matched case-insensitively against the
# GitHub step name, which is the one label every lane already gives these steps.
# Not `toolchain`: the iOS toolchain step is a composite action whose duration is mostly the package
# install, so it would turn a slow mirror into a slow-device label. `_DEPS_STEP` covers it.
_BOOT_STEP = re.compile(r"simulator|emulator|avd|boot", re.IGNORECASE)
# Steps that only fetch or install dependencies. A job that fails in one of these failed before
# any scenario ran, which is the dependency-fetch cause rather than a device or test one.
_DEPS_STEP = re.compile(
    r"set up|setup|install|sync|download|cache|checkout|fetch|toolchain", re.IGNORECASE
)
# Steps that run scenarios. A failing step outside both lists is reported by name only.
_RUN_STEP = re.compile(r"\brun\b|scenario|conformance|fault|golden|visual|e2e", re.IGNORECASE)

# Job-log lines this report counts. The fragments are copied from the warnings `bajutsu` itself
# logs; `tests/test_ci_telemetry.py` pins each against its source so a reworded warning fails the
# gate instead of silently counting zero.
LOG_SIGNALS: Mapping[str, re.Pattern[str]] = {
    "channel_retries": re.compile(r"runner channel \S+ \S+ failed \(attempt \d+/\d+\), retrying"),
    "channel_crashes": re.compile(r"the runner became unreachable past the retry budget"),
    "video_start_unconfirmed": re.compile(
        r"recordVideo did not report 'Recording started'|screenrecord on \S+ wrote no bytes within"
    ),
    "network_errors": re.compile(
        r"Could not resolve host|Temporary failure in name resolution|Connection reset by peer"
        r"|TLS handshake timeout|ETIMEDOUT|ECONNRESET|HTTP (?:error )?50[234]|rate limit exceeded",
        re.IGNORECASE,
    ),
}


# --- summarize: one record per job, from what is already on the runner ------------------------


def _floats(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _size_mb(value: str) -> float:
    """`12G` / `340M` / `5K` / `1024` (bytes) in megabytes — the units macOS `top` prints."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT]?)B?", value.strip())
    if not match:
        return 0.0
    number, unit = float(match.group(1)), match.group(2)
    scale = {"": 1 / 1024 / 1024, "K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024}
    return number * scale[unit]


def _cpu_fields(line: str) -> dict[str, float]:
    """Idle, I/O-wait, and steal percentages from one CPU line of either `top`, else empty."""
    body = line.removeprefix("Interval ")
    if body.startswith("CPU usage:"):
        m = re.search(r"([\d.]+)% idle", body)
        return {"idle": float(m.group(1))} if m else {}
    if body.startswith("%Cpu(s):"):
        names = {"id": "idle", "wa": "iowait", "st": "steal"}
        return {
            names[name]: float(value)
            for value, name in re.findall(r"([\d.]+)\s*(id|wa|st)\b", body)
        }
    return {}


def parse_host_telemetry(text: str) -> dict[str, Any]:
    """Reduce the diagnostics sampler's free-text log to the numbers the report compares.

    Reads both samplers: the macOS one (`top -l`, `vm_stat`, `memory_pressure`) and the Linux one
    (`top -b`, `free -m`). Each sample contributes whatever lines it holds; a sample that lost a line
    to a hung command simply has fewer numbers. A CPU line prefixed `Interval` comes from a
    two-sample `top`, the only CPU reading a single-sample `top` cannot fake: its first sample
    reports an average since boot, so an unprefixed CPU line is ignored when an interval one exists.
    """
    series: dict[str, list[float]] = defaultdict(list)
    samples = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("=== ") or re.match(r"^\d{4}-\d\d-\d\dT[\d:]+Z$", stripped):
            samples += 1
        elif m := re.search(r"(?:Load Avg|load average):\s*([\d.]+)", line):
            series["load"].append(float(m.group(1)))
        elif cpu := _cpu_fields(line):
            interval = line.startswith("Interval")
            for name, value in cpu.items():
                series[f"{name}_interval" if interval else name].append(value)
        elif m := re.search(r"System-wide memory free percentage:\s*(\d+)%", line):
            series["mem_free"].append(float(m.group(1)))
        elif stripped.startswith("Mem:") and len(numbers := _floats(stripped)) >= 6:
            total, available = numbers[0], numbers[5]
            if total > 0:
                series["mem_free"].append(100.0 * available / total)
        elif m := re.search(r"Disks:\s*\d+/(\S+) read,\s*\d+/(\S+) written", line):
            series["disk_mb"].append(_size_mb(m.group(1)) + _size_mb(m.group(2)))
    load, mem_free, disk_mb = series["load"], series["mem_free"], series["disk_mb"]
    idle_interval = series["idle_interval"]
    idle = idle_interval or series["idle"]
    iowait = series["iowait_interval"] or series["iowait"]
    steal = series["steal_interval"] or series["steal"]
    # macOS `top` prints cumulative disk traffic; the per-interval delta is what shows a burst.
    disk_delta = [disk_mb[i + 1] - disk_mb[i] for i in range(len(disk_mb) - 1)]
    disk_delta = [d for d in disk_delta if d >= 0]
    return {
        "samples": samples,
        "load1_max": max(load, default=None),
        "load1_median": statistics.median(load) if load else None,
        "cpu_idle_min": min(idle, default=None),
        "cpu_idle_measured": bool(idle_interval),
        "iowait_max": max(iowait, default=None),
        "steal_max": max(steal, default=None),
        "mem_free_min_pct": min(mem_free, default=None),
        "disk_mb_per_sample_max": round(max(disk_delta), 1) if disk_delta else None,
    }


def parse_render_probe(text: str) -> dict[str, Any]:
    """The pre-run render probe's answer: did a screenshot and a 5s recording produce anything."""
    out: dict[str, Any] = {
        "screenshot_s": None,
        "screenshot_hung": False,
        "screenshot_bytes": None,
        "video_bytes": None,
    }
    if m := re.search(r"screenshot: exit -?\d+ in (\d+)s, (\d+) bytes", text):
        out["screenshot_s"], out["screenshot_bytes"] = float(m.group(1)), int(m.group(2))
    if m := re.search(r"screenshot: STILL RUNNING after (\d+)s", text):
        out["screenshot_s"], out["screenshot_hung"] = float(m.group(1)), True
    if m := re.search(r"recordVideo: exit -?\d+, (\d+) bytes", text):
        out["video_bytes"] = int(m.group(1))
    return out


def parse_host_environment(text: str) -> dict[str, Any]:
    """The macOS / Xcode / hardware key the iOS collect step writes to `host-environment.txt`."""
    out: dict[str, Any] = {}
    patterns = {
        "os_version": r"ProductVersion:\s*(\S+)",
        "os_build": r"BuildVersion:\s*(\S+)",
        "hw_model": r"hw\.model:\s*(\S+)",
        "ncpu": r"hw\.ncpu:\s*(\d+)",
        "memsize": r"hw\.memsize:\s*(\d+)",
        "virtualized": r"kern\.hv_vmm_present:\s*(\d+)",
        "xcode": r"Xcode\s+(\d[\w.]*)",
        "xcode_build": r"Build version\s+(\S+)",
    }
    for key, pattern in patterns.items():
        if m := re.search(pattern, text):
            out[key] = m.group(1)
    for key in ("ncpu", "memsize"):
        if key in out:
            out[key] = int(out[key])
    if "memsize" in out:
        out["memory_gb"] = round(out.pop("memsize") / 1024**3)
    if "virtualized" in out:
        out["virtualized"] = out["virtualized"] == "1"
    return out


def _linux_host() -> dict[str, Any]:
    out: dict[str, Any] = {"os_version": platform.release(), "ncpu": os.cpu_count()}
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return out
    if m := re.search(r"MemTotal:\s*(\d+) kB", meminfo):
        out["memory_gb"] = round(int(m.group(1)) / 1024**2)
    return out


def ci_identity(env: Mapping[str, str]) -> dict[str, Any]:
    """The keys that line one job's record up with the same job on other runs and with the API."""
    keys = {
        "repository": "GITHUB_REPOSITORY",
        "workflow": "GITHUB_WORKFLOW",
        "job": "GITHUB_JOB",
        "run_id": "GITHUB_RUN_ID",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "sha": "GITHUB_SHA",
        "ref": "GITHUB_REF",
        "event": "GITHUB_EVENT_NAME",
        "runner_name": "RUNNER_NAME",
        "runner_os": "RUNNER_OS",
        "runner_arch": "RUNNER_ARCH",
        "runner_environment": "RUNNER_ENVIRONMENT",
        "image_os": "ImageOS",
        "image_version": "ImageVersion",
    }
    return {key: env.get(name) for key, name in keys.items()}


def _is_wait(action: str) -> bool:
    return action.lower().startswith("wait")


def summarize_manifest(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per scenario: verdict, where it failed, how long it ran, and how long its waits took.

    The first wait is kept apart because it is the step a slow device hurts first: the app has just
    launched, and a wait that normally settles in a second and here took its whole timeout reads as
    a device that never finished starting rather than a screen that changed.
    """
    rows: list[dict[str, Any]] = []
    for scenario in manifest.get("scenarios") or []:
        steps = [s for s in scenario.get("steps") or [] if isinstance(s, dict)]
        waits = [float(s.get("duration_s") or 0.0) for s in steps if _is_wait(str(s.get("action")))]
        failed = next((s for s in steps if s.get("ok") is False), None)
        rows.append(
            {
                "scenario": scenario.get("scenario"),
                "ok": scenario.get("ok"),
                "duration_s": round(sum(float(s.get("duration_s") or 0.0) for s in steps), 2),
                "first_wait_s": waits[0] if waits else None,
                "max_wait_s": max(waits) if waits else None,
                "failed_action": failed.get("action") if failed else None,
                "failure": (scenario.get("failure") or "")[:300] or None,
            }
        )
    return rows


def _stall_reasons(runs: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for stall in runs.glob("**/stalls/stall-*"):
        # stall-NN-<reason>-<pid>: the reason itself may contain hyphens.
        if m := re.fullmatch(r"stall-\d+-(.+)-\d+", stall.name):
            counts[m.group(1)] += 1
    return dict(counts)


def _manifests(runs: Path) -> list[Path]:
    return sorted(p for p in runs.glob("**/manifest.json") if "ci-telemetry" not in p.parts)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def summarize(
    runs: Path,
    lane: str,
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
    diagnostics: Path | None = None,
) -> dict[str, Any]:
    """Build one job's telemetry record from what the job has already written under `runs`.

    `diagnostics` is where the lane's diagnostics action wrote its files; it defaults to
    `runs/diagnostics`, which is where every caller puts them today.
    """
    now = now or datetime.now(timezone.utc)  # noqa: UP017 — see the import
    diagnostics = diagnostics or runs / "diagnostics"
    host: dict[str, Any] = (
        parse_host_environment(_read(diagnostics / "host-environment.txt"))
        if lane == "ios"
        else _linux_host()
    )
    scenarios: list[dict[str, Any]] = []
    for manifest_path in _manifests(runs):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict):
            for row in summarize_manifest(manifest):
                row["run"] = manifest_path.parent.name
                scenarios.append(row)
    oks = [s["ok"] for s in scenarios if s["ok"] is not None]
    return {
        "schema": SCHEMA_VERSION,
        "lane": lane,
        "collected_at": now.isoformat(timespec="seconds"),
        "hour_utc": now.hour,
        "weekday": now.strftime("%a"),
        "ci": ci_identity(env),
        "host": host,
        "host_load": parse_host_telemetry(_read(diagnostics / "host-telemetry.log")),
        "render_probe": parse_render_probe(_read(diagnostics / "render-probe.txt"))
        if lane == "ios"
        else None,
        "stalls": _stall_reasons(runs),
        "runs_ok": all(oks) if oks else None,
        "scenarios": scenarios,
    }


def write_summary(record: Mapping[str, Any], runs: Path, out: Path) -> Path:
    """Write the record and a copy of every manifest, laid out for `bajutsu flakiness --history`."""
    out.mkdir(parents=True, exist_ok=True)
    for manifest_path in _manifests(runs):
        dest = out / "manifests" / manifest_path.parent.name / "manifest.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest_path, dest)
    path = out / RECORD_NAME
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- fetch: join the records with what only the GitHub API knows ------------------------------

Gh = Callable[[Sequence[str]], str]


def _gh(args: Sequence[str]) -> str:
    return subprocess.run(
        ["gh", *args], text=True, capture_output=True, check=True, errors="replace"
    ).stdout


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (b - a).total_seconds()


def count_log_signals(log: str) -> dict[str, int]:
    """How many times each `LOG_SIGNALS` pattern appears in a job log."""
    return {name: len(pattern.findall(log)) for name, pattern in LOG_SIGNALS.items()}


def job_facts(job: Mapping[str, Any]) -> dict[str, Any]:
    """The API's view of one job: its conclusion, queue time, and each step's duration."""
    steps = [
        {
            "name": s.get("name"),
            "conclusion": s.get("conclusion"),
            "seconds": _seconds(s.get("started_at"), s.get("completed_at")),
        }
        for s in job.get("steps") or []
    ]
    failed = next((s["name"] for s in steps if s["conclusion"] == "failure"), None)
    boot = [s["seconds"] for s in steps if s["seconds"] and _BOOT_STEP.search(s["name"] or "")]
    return {
        "job_id": job.get("id"),
        "job_name": job.get("name"),
        "conclusion": job.get("conclusion"),
        "runner_name": job.get("runner_name"),
        "labels": job.get("labels"),
        "queue_s": _seconds(job.get("created_at"), job.get("started_at")),
        "duration_s": _seconds(job.get("started_at"), job.get("completed_at")),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "failed_step": failed,
        "device_boot_s": max(boot) if boot else None,
        "steps": steps,
    }


def concurrency(jobs: Sequence[Mapping[str, Any]]) -> list[int]:
    """For each job, how many of the given jobs were running when it started (itself excluded)."""
    # ISO-8601 UTC timestamps from one API compare correctly as strings.
    spans = [(j.get("started_at"), j.get("completed_at")) for j in jobs]
    return [
        sum(1 for s, e in spans if s and e and s < start < e) if start else 0 for start, _ in spans
    ]


def _run_jobs(gh: Gh, repo: str, run_id: str) -> list[dict[str, Any]]:
    # `--jq '.jobs[] | @json'` prints one job per line across every page, which a paginated plain
    # `gh api` would instead emit as several concatenated JSON documents.
    listing = gh(
        ["api", "--paginate", f"repos/{repo}/actions/runs/{run_id}/jobs", "--jq", ".jobs[] | @json"]
    )
    return [json.loads(line) for line in listing.splitlines() if line.strip()]


def _run_records(gh: Gh, repo: str, run_id: str, out: Path) -> dict[str, dict[str, Any]]:
    """Download one run's telemetry artifacts; the records keyed by runner, manifests copied out."""
    artifact_dir = out / "artifacts" / run_id
    # No telemetry on this run (older than the collection, or every job died early): the API-only
    # records `fetch` builds still count its jobs.
    with contextlib.suppress(subprocess.CalledProcessError):
        gh(["run", "download", run_id, "--repo", repo, "--pattern", f"{ARTIFACT_PREFIX}*",
            "--dir", str(artifact_dir)])  # fmt: skip
    by_runner: dict[str, dict[str, Any]] = {}
    for record_path in sorted(artifact_dir.glob(f"*/{RECORD_NAME}")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # A job that collects twice uploads two records; the later one is a superset.
        key = str(record.get("ci", {}).get("runner_name") or record_path.parent.name)
        if record.get("collected_at", "") >= by_runner.get(key, {}).get("collected_at", ""):
            by_runner[key] = record
        # Two jobs of one run can mint the same timestamped run id, so each copy is keyed by the
        # job's runner as well; a job's second upload then overwrites its first, not a sibling's.
        slug = re.sub(r"[^\w.-]", "_", key)
        for manifest in sorted((record_path.parent / "manifests").glob("*/manifest.json")):
            dest = out / "manifests" / f"{run_id}-{slug}-{manifest.parent.name}"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest, dest / "manifest.json")
    return by_runner


def _job_log_signals(gh: Gh, repo: str, job_id: Any) -> dict[str, int] | None:
    try:
        return count_log_signals(gh(["api", f"repos/{repo}/actions/jobs/{job_id}/logs"]))
    except subprocess.CalledProcessError:
        return None  # logs expired or withheld: unknown, which is not the same as zero


_TIMEOUT_ANNOTATION = "exceeded the maximum execution time"


def _timed_out(gh: Gh, repo: str, job_id: Any) -> bool:
    """Whether a cancelled job was killed by its `timeout-minutes` rather than superseded.

    The jobs API reports both as `cancelled`; the timeout leaves an annotation on the check run.
    """
    try:
        messages = gh(
            ["api", f"repos/{repo}/check-runs/{job_id}/annotations", "--jq", ".[].message"]
        )
    except subprocess.CalledProcessError:
        return False
    return _TIMEOUT_ANNOTATION in messages


def fetch(
    repo: str,
    workflow: str,
    limit: int,
    out: Path,
    *,
    gh: Gh = _gh,
    with_logs: bool = True,
) -> int:
    """Download the last `limit` completed runs' telemetry and write `records.jsonl` under `out`.

    A job with no telemetry artifact (an older run, a job that died before its collect step, a lane
    that does not collect) still gets a record from the API alone, so its failure is counted rather
    than silently missing from the denominator.
    """
    out.mkdir(parents=True, exist_ok=True)
    runs = json.loads(
        gh(["run", "list", "--repo", repo, "--workflow", workflow, "--status", "completed",
            "--limit", str(limit), "--json", "databaseId,attempt,createdAt,headBranch,event"])
    )  # fmt: skip
    records: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run["databaseId"])
        by_runner = _run_records(gh, repo, run_id, out)
        jobs = [job_facts(job) for job in _run_jobs(gh, repo, run_id)]
        # The record carries no API job id, so it joins by runner name; a name two jobs of one run
        # share would attach one job's telemetry to the other, so such jobs stay API-only.
        names = Counter(str(f["runner_name"]) for f in jobs)
        for facts in jobs:
            # A superseded run's cancelled jobs are neither red nor green: the E2E workflows cancel
            # one when a newer push arrives. A job its `timeout-minutes` killed is also `cancelled`,
            # but it is the wedged-device case this report exists for, so it counts red.
            cancelled = facts["conclusion"] == "cancelled" and facts["job_id"]
            if cancelled and _timed_out(gh, repo, facts["job_id"]):
                facts["conclusion"] = "timed_out"
            if facts["conclusion"] in (None, "skipped", "cancelled"):
                continue
            name = str(facts["runner_name"])
            record = dict(by_runner.get(name, {}) if names[name] == 1 else {})
            record["api"] = facts
            record.setdefault("ci", {"run_id": run_id, "workflow": workflow})
            record["run_event"] = run.get("event")
            record["run_branch"] = run.get("headBranch")
            if with_logs and facts["job_id"]:
                record["log_signals"] = _job_log_signals(gh, repo, facts["job_id"])
            records.append(record)
    overlaps = concurrency([r["api"] for r in records])
    for i, record in enumerate(records):
        record["api"]["concurrent_jobs"] = overlaps[i]
        record["causes"] = candidate_causes(record)
    with (out / "records.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    return len(records)


# --- candidate causes and the cross-run report ------------------------------------------------


def _gt(value: Any, limit: float) -> bool:
    return isinstance(value, (int, float)) and value > limit


def _lt(value: Any, limit: float) -> bool:
    return isinstance(value, (int, float)) and value < limit


def candidate_causes(record: Mapping[str, Any]) -> list[str]:
    """The factors whose signal is present in this job, by the fixed thresholds above.

    Computed for green jobs too: a signal that fires as often on green jobs as on red ones does not
    explain the red ones, and the report can only show that if both sides carry the same labels.
    `app-wait-timeout` and `test-assertion` are residual — they name a failing scenario that no
    infrastructure signal accompanies, which is where a real defect or a too-tight wait shows up.
    """
    causes: list[str] = []
    load = record.get("host_load") or {}
    host = record.get("host") or {}
    ncpu = host.get("ncpu") or 0
    if (
        (ncpu and _gt(load.get("load1_max"), LOAD_PER_CPU_HIGH * ncpu))
        or (load.get("cpu_idle_measured") and _lt(load.get("cpu_idle_min"), CPU_IDLE_LOW))
        or _lt(load.get("mem_free_min_pct"), MEM_FREE_LOW)
        or _gt(load.get("iowait_max"), IOWAIT_HIGH)
        or _gt(load.get("steal_max"), STEAL_HIGH)
    ):
        causes.append("host-resources")
    api = record.get("api") or {}
    probe = record.get("render_probe") or {}
    stalls = record.get("stalls") or {}
    if (
        _gt(api.get("device_boot_s"), DEVICE_BOOT_SLOW)
        or probe.get("screenshot_hung")
        or _gt(probe.get("screenshot_s"), RENDER_SCREENSHOT_SLOW)
        or stalls.get("runner-crash")
    ):
        causes.append("device-slow-or-wedged")
    signals = record.get("log_signals") or {}
    if signals.get("channel_retries") or signals.get("channel_crashes"):
        causes.append("channel-timeout")
    if (
        signals.get("video_start_unconfirmed")
        or probe.get("video_bytes") == 0
        or stalls.get("video-no-bytes")
        or stalls.get("screenrecord-no-growth")
    ):
        causes.append("video-start")
    failed_step = str(api.get("failed_step") or "")
    if signals.get("network_errors") or (
        failed_step and _DEPS_STEP.search(failed_step) and not _RUN_STEP.search(failed_step)
    ):
        causes.append("network-or-deps")
    if _gt(api.get("queue_s"), QUEUE_SLOW):
        causes.append("runner-contention")
    infra = bool(causes)
    failing = [s for s in record.get("scenarios") or [] if s.get("ok") is False]
    if failing and not infra:
        if record.get("api") and not isinstance(record.get("log_signals"), dict):
            # Log signals unknown (withheld, or `--no-logs`): "no infrastructure signal" is not
            # established, so a residual label would blame the code on missing evidence.
            causes.append("log-signals-unknown")
        elif any(_is_wait(str(s.get("failed_action") or "")) for s in failing):
            causes.append("app-wait-timeout")
        else:
            causes.append("test-assertion")
    return causes


def load_records(path: Path) -> list[dict[str, Any]]:
    """Every record under `path`: a `records.jsonl` (from `fetch`) or loose `ci-telemetry.json`."""
    files = [path] if path.is_file() else sorted(path.glob("**/records.jsonl"))
    records: list[dict[str, Any]] = []
    for file in files:
        records.extend(json.loads(line) for line in _read(file).splitlines() if line.strip())
    if not records and path.is_dir():
        for file in sorted(path.glob(f"**/{RECORD_NAME}")):
            record = json.loads(_read(file))
            record["causes"] = candidate_causes(record)
            records.append(record)
    return records


def is_failed(record: Mapping[str, Any]) -> bool:
    """A job's own conclusion when the API supplied one, else its scenarios' verdict."""
    conclusion = (record.get("api") or {}).get("conclusion")
    if conclusion:
        return bool(conclusion != "success")
    return record.get("runs_ok") is False


def _median(values: Iterable[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return round(statistics.median(nums), 2) if nums else None


def _job_label(record: Mapping[str, Any]) -> str:
    api = record.get("api") or {}
    return str(api.get("job_name") or (record.get("ci") or {}).get("job") or "?")


def _hour_bucket(record: Mapping[str, Any]) -> str:
    # The job's start hour: `hour_utc` is stamped in the `collect` phase, after the job ran, so a
    # joined record would otherwise land up to an hour later than an API-only one.
    started = (record.get("api") or {}).get("started_at")
    hour = int(started[11:13]) if started else record.get("hour_utc")
    return (
        f"{int(hour) // 4 * 4:02d}-{int(hour) // 4 * 4 + 3:02d}h UTC" if hour is not None else "?"
    )


DIMENSIONS: Mapping[str, Callable[[Mapping[str, Any]], str]] = {
    "job": _job_label,
    "runner image": lambda r: str((r.get("ci") or {}).get("image_version") or "?"),
    "os": lambda r: str((r.get("host") or {}).get("os_version") or "?"),
    "xcode": lambda r: str((r.get("host") or {}).get("xcode") or "-"),
    "hardware": lambda r: str((r.get("host") or {}).get("hw_model") or "?"),
    "hour": _hour_bucket,
}

METRICS: Mapping[str, Callable[[Mapping[str, Any]], Any]] = {
    "queue_s": lambda r: (r.get("api") or {}).get("queue_s"),
    "device_boot_s": lambda r: (r.get("api") or {}).get("device_boot_s"),
    "concurrent_jobs": lambda r: (r.get("api") or {}).get("concurrent_jobs"),
    "load1_max": lambda r: (r.get("host_load") or {}).get("load1_max"),
    "cpu_idle_min": lambda r: (r.get("host_load") or {}).get("cpu_idle_min"),
    "mem_free_min_pct": lambda r: (r.get("host_load") or {}).get("mem_free_min_pct"),
    "render_screenshot_s": lambda r: (r.get("render_probe") or {}).get("screenshot_s"),
    "channel_retries": lambda r: (r.get("log_signals") or {}).get("channel_retries"),
    "video_start_unconfirmed": lambda r: (r.get("log_signals") or {}).get(
        "video_start_unconfirmed"
    ),
    "first_wait_s_max": lambda r: max(
        (s["first_wait_s"] for s in r.get("scenarios") or [] if s.get("first_wait_s") is not None),
        default=None,
    ),
}


def aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Cause frequencies on red vs green jobs, failure rates per dimension, and metric medians."""
    red = [r for r in records if is_failed(r)]
    green = [r for r in records if not is_failed(r)]
    causes: dict[str, dict[str, int]] = defaultdict(lambda: {"red": 0, "green": 0})
    for r in records:
        for cause in r.get("causes") or []:
            causes[cause]["red" if is_failed(r) else "green"] += 1
    dimensions: dict[str, dict[str, dict[str, int]]] = {}
    for name, key in DIMENSIONS.items():
        table: dict[str, dict[str, int]] = defaultdict(lambda: {"jobs": 0, "red": 0})
        for r in records:
            row = table[key(r)]
            row["jobs"] += 1
            row["red"] += int(is_failed(r))
        dimensions[name] = dict(sorted(table.items()))
    metrics = {
        name: {"red": _median(get(r) for r in red), "green": _median(get(r) for r in green)}
        for name, get in METRICS.items()
    }
    unexplained = [
        {
            "job": _job_label(r),
            "run_id": (r.get("ci") or {}).get("run_id"),
            "failed_step": (r.get("api") or {}).get("failed_step"),
        }
        for r in red
        # An unread log leaves the job unexplained too: its label records missing evidence.
        if not set(r.get("causes") or []) - {"log-signals-unknown"}
    ]
    return {
        "jobs": len(records),
        "red": len(red),
        "causes": dict(sorted(causes.items())),
        "dimensions": dimensions,
        "metrics": metrics,
        "unexplained": unexplained,
    }


def render(summary: Mapping[str, Any], records_dir: Path | None = None) -> str:
    """The aggregate as Markdown tables a reader can paste into an issue."""
    lines = [f"# CI telemetry: {summary['red']} red of {summary['jobs']} jobs", ""]
    lines += [
        "## Candidate causes (signal present on red vs green jobs)",
        "",
        "| cause | red | green |",
        "|---|---|---|",
    ]
    for cause, counts in summary["causes"].items():
        lines.append(f"| {cause} | {counts['red']} | {counts['green']} |")
    lines += ["", "## Medians (red vs green)", "", "| metric | red | green |", "|---|---|---|"]
    for name, pair in summary["metrics"].items():
        lines.append(f"| {name} | {pair['red']} | {pair['green']} |")
    for name, table in summary["dimensions"].items():
        lines += ["", f"## Failure rate by {name}", "", f"| {name} | jobs | red | rate |"]
        lines.append("|---|---|---|---|")
        for value, row in table.items():
            rate = f"{100 * row['red'] / row['jobs']:.0f}%" if row["jobs"] else "-"
            lines.append(f"| {value} | {row['jobs']} | {row['red']} | {rate} |")
    if summary["unexplained"]:
        lines += ["", "## Red jobs with no candidate cause", ""]
        lines += [
            f"- {u['job']} (run {u['run_id']}, failed step: {u['failed_step']})"
            for u in summary["unexplained"]
        ]
    if records_dir is not None and (records_dir / "manifests").is_dir():
        lines += [
            "",
            "Per-scenario flip rates over the same runs:",
            f"`bajutsu flakiness --history {records_dir / 'manifests'}`",
        ]
    return "\n".join(lines) + "\n"


# --- CLI --------------------------------------------------------------------------------------


def _summarize_main(args: argparse.Namespace) -> int:
    runs = Path(args.runs)
    diagnostics = Path(args.diagnostics) if args.diagnostics else None
    record = summarize(runs, args.lane, env=os.environ, diagnostics=diagnostics)
    path = write_summary(record, runs, Path(args.out))
    print(f"ci telemetry → {path} ({len(record['scenarios'])} scenario(s))")
    return 0


def _fetch_main(args: argparse.Namespace) -> int:
    repo = args.repo or _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    count = fetch(
        repo.strip(), args.workflow, args.limit, Path(args.out), with_logs=not args.no_logs
    )
    print(f"{count} job record(s) → {Path(args.out) / 'records.jsonl'}")
    return 0


def _report_main(args: argparse.Namespace) -> int:
    path = Path(args.path)
    summary = aggregate(load_records(path))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(render(summary, path if path.is_dir() else None), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse `argv` and run one subcommand."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("summarize", help="write this job's telemetry record (in CI)")
    s.add_argument("--lane", choices=("ios", "android"), required=True)
    s.add_argument("--runs", default="runs")
    s.add_argument("--out", default=DEFAULT_OUT)
    s.add_argument(
        "--diagnostics", default="", help="diagnostics dir (default: <runs>/diagnostics)"
    )
    s.set_defaults(func=_summarize_main)
    f = sub.add_parser("fetch", help="download recent runs' telemetry and join it with the API")
    f.add_argument("--workflow", required=True, help="workflow file, e.g. ios-e2e.yml")
    f.add_argument("--repo", default="", help="owner/name (default: this checkout's repository)")
    f.add_argument("--limit", type=int, default=30, help="completed runs to read")
    f.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "bajutsu-ci-telemetry"))
    f.add_argument("--no-logs", action="store_true", help="skip the per-job log download")
    f.set_defaults(func=_fetch_main)
    r = sub.add_parser("report", help="aggregate fetched records")
    r.add_argument("path", help="a fetch output directory, a records.jsonl, or loose records")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=_report_main)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
