"""Pure query, path, and validation helpers for ``bajutsu serve``.

These functions are free of server state and fully unit-testable on their own. The CLI command
builders that once lived here now sit in `serve/commands.py` (BE-0206).
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from bajutsu import simctl as _simctl
from bajutsu.backends import KNOWN_ACTUATORS, PLATFORMS
from bajutsu.config import Config, IosConfig, resolve
from bajutsu.device_id import is_valid_device_id
from bajutsu.scenario import load_scenario_file
from bajutsu.serve.capabilities import required_capabilities
from bajutsu.serve.orgs import OrgConfig, load_serve_config

# Tokens a `--backend` may name: a platform (ios/android/web/fake) or a known actuator (xcuitest/…).
_VALID_BACKENDS = frozenset(PLATFORMS) | frozenset(KNOWN_ACTUATORS)
# A run id is a single safe path segment (timestamps like 20260610-153045): alphanumeric start,
# then [A-Za-z0-9._-]. Blocks "..", path separators, and absolute paths, so a client-supplied run
# id (a resumed crawl) can't redirect a run's --out dir outside runs_dir.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def mask_secret(value: str) -> str:
    """Redact a secret for display: keep a short head and tail, hide the rest.  Short values
    (≤8 chars) are fully masked so nothing useful leaks."""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


_RANGE_RE = re.compile(r"bytes=([0-9]*)-([0-9]*)")


def parse_byte_range(range_header: str | None, total: int) -> tuple[int, int] | None:
    """Parse a single ``Range: bytes=start-end`` request header against a body of *total* bytes.

    Returns the inclusive ``(start, end)`` byte offsets to serve, or None when *range_header* is
    absent, a multi-range list, or otherwise not a plain single range — the caller then serves the
    whole body as a normal 200. Raises ValueError when the header parses as a byte-range but isn't
    satisfiable (e.g. a start past *total*), so the caller can reply 416 instead of serving garbage.

    Without this, an HTML5 `<video>` can't seek: a report's video is served over HTTP by `bajutsu
    serve`, and every browser needs 206/`Content-Range` responses to fetch into a scrub target that
    isn't buffered yet — a 200-only server makes it silently restart from 0 instead."""
    if not range_header:
        return None
    match = _RANGE_RE.fullmatch(range_header.strip())
    if match is None:
        return None  # multi-range or unrecognized syntax: ignore, serve the whole body
    start_s, end_s = match.groups()
    if not start_s and not end_s:
        raise ValueError("empty range")
    if not start_s:  # suffix range: last N bytes
        suffix_length = int(end_s)
        if suffix_length == 0:
            raise ValueError("zero-length suffix range")
        start, end = max(0, total - suffix_length), total - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else total - 1
    if start > end or start >= total:
        raise ValueError("range not satisfiable")
    return start, min(end, total - 1)


def range_reply(data: bytes, range_header: str | None) -> tuple[int, bytes, dict[str, str]]:
    """The status/body/headers for serving *data* against an incoming ``Range`` header — the one
    206/416/200 reply shape both serve backends (the stdlib handler and the FastAPI app) emit for a
    report's `<video>` Range request, so a future header change can't drift between the two."""
    try:
        byte_range = parse_byte_range(range_header, len(data))
    except ValueError:
        return 416, b"", {"Content-Range": f"bytes */{len(data)}"}
    if byte_range is None:
        return 200, data, {"Accept-Ranges": "bytes"}
    start, end = byte_range
    headers = {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{len(data)}"}
    return 206, data[start : end + 1], headers


# --- query helpers ---


def summarize_scenario(file: str, path: str, text: str) -> dict[str, Any]:
    """One scenario file's UI summary: its *file* name, the *path*/ref the run command takes, the
    file-level ``description``, and each scenario's name + description. A parse failure degrades to
    a bare entry (no descriptions/names) so a malformed file still lists.  Shared by the local dir
    listing and the object-storage backing so the two never drift (BE-0015)."""
    description: str | None = None
    scenarios: list[dict[str, Any]] = []
    try:
        sf = load_scenario_file(text)
        description = sf.description
        scenarios = [{"name": s.name, "description": s.description} for s in sf.scenarios]
    except (OSError, ValueError):
        scenarios = []  # a malformed/unparseable file still lists as a bare entry (no names)
    return {
        "file": file,
        "path": path,
        "description": description,
        "scenarios": scenarios,
        "names": [s["name"] for s in scenarios],
    }


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    """Every ``*.yaml`` under *scenarios_dir*: a path the run command can take, the file-level
    ``description``, and each scenario's name + description (for the UI)."""
    out: list[dict[str, Any]] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            text = ""  # an unreadable / non-UTF-8 file (ValueError) still lists as a bare entry
        out.append(summarize_scenario(path.name, str(path), text))
    return out


# Parsed-config cache, keyed by the resolved path and a freshness stamp ``(st_mtime_ns, st_size)``.
# The serve config is read on most requests (targets/scenarios listing, org resolution); without this
# each one re-reads and re-validates the YAML. The entry is invalidated whenever the file's mtime or
# size changes; an edit that preserves both (a same-size rewrite that also keeps the timestamp) won't
# be noticed, which is acceptable for an operator-edited config. A lock guards the dict since serve
# handles requests on multiple threads.
_config_cache: dict[str, tuple[tuple[int, int], Config, dict[str, OrgConfig]]] = {}
_config_cache_lock = threading.Lock()


def _load_serve_config_cached(config_path: Path) -> tuple[Config, dict[str, OrgConfig]]:
    """The parsed config *and* its org model at *config_path*, cached by resolved path + file
    mtime/size so a request parses it at most once and unchanged files aren't re-parsed across
    requests. The core `Config` drops `orgs:`; the org model is recovered here (BE-0129). Raises on a
    read/validation error (callers handle it); a malformed-YAML error is normalized to `ValueError`
    so the callers' `except (OSError, ValueError)` covers it. Only successful parses are cached, so a
    fix to a bad config is picked up at once."""
    stat = config_path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    # Canonicalize the key so the same file via a relative/absolute/symlinked path shares one entry.
    key = str(config_path.resolve())
    with _config_cache_lock:
        cached = _config_cache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1], cached[2]
    try:
        config, orgs = load_serve_config(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(str(e)) from e  # so callers catching ValueError handle a malformed config
    with _config_cache_lock:
        _config_cache[key] = (stamp, config, orgs)
    return config, orgs


def _load_config_cached(config_path: Path) -> Config:
    """The parsed config at *config_path* (org model discarded); see `_load_serve_config_cached`."""
    return _load_serve_config_cached(config_path)[0]


def load_serve_config_file(config_path: Path | None) -> tuple[Config, dict[str, OrgConfig]] | None:
    """The parsed config and its org model, or None if there is none or it can't be read/validated.
    Used where the org model is needed (resolving a user/target to its org)."""
    if config_path is None:
        return None
    try:
        return _load_serve_config_cached(config_path)
    except (OSError, ValueError):
        return None


def list_targets(config_path: Path) -> list[str]:
    try:
        return sorted(_load_config_cached(config_path).targets)
    except (OSError, ValueError):
        return []


def target_build_info(config_path: Path, target: str) -> tuple[str | None, str | None]:
    """``(app_path, build)`` for *target* from config — the built ``.app`` path and the shell
    command that builds it.  Either may be ``None`` (unset or any load/resolve error); the run
    then proceeds without an on-demand build (and the runner reports a missing binary as
    before)."""
    try:
        eff = resolve(_load_config_cached(config_path), target)
    except (OSError, ValueError, KeyError):
        return (None, None)
    ios = eff.platform_config
    # Only an iOS target carries an on-demand build; other platforms have no .app to build.
    return (ios.app_path, ios.build) if isinstance(ios, IosConfig) else (None, None)


def target_capabilities(config_path: Path, target: str) -> list[str]:
    """The capability tokens a worker must advertise to run *target* (BE-0166): its resolved
    platform axis (`platform:ios` / `platform:web`) plus the target's operator-declared `requires`.

    Empty on any load/resolve error, so a job with an unreadable config routes as before: with no
    required capabilities it is servable by any worker (an empty set is always a subset in
    `can_serve`) — including one that can't actually run it — rather than crashing dispatch. In
    practice `start_run` resolves the same config a step earlier (`target_build_info`), so this
    error path is not reached on the normal dispatch flow."""
    try:
        eff = resolve(_load_config_cached(config_path), target)
    except (OSError, ValueError, KeyError):
        return []
    return required_capabilities(eff.platform, eff.requires)


def target_scenarios_dir(config_path: Path, target: str) -> Path | None:
    """The configured ``scenarios`` dir for *target*, or None (unset, or any load/resolve error).
    Mirrors ``target_build_info``; resolved relative to the run's working directory."""
    try:
        eff = resolve(_load_config_cached(config_path), target)
    except (OSError, ValueError, KeyError):
        return None
    return Path(eff.evidence_dirs.scenarios) if eff.evidence_dirs.scenarios else None


def list_fs(root: Path, sub: str | None) -> dict[str, Any]:
    """One directory listing for the UI's config picker: subdirectories and ``*.yml``/``*.yaml``
    files under *sub* (default *root*), confined to *root*.  Raises ValueError if *sub* escapes
    *root*.  ``parent`` is the dir to go up to, or None at *root* (the browse ceiling)."""
    base = root.resolve()
    here = (base / sub).resolve() if sub else base
    if here != base and base not in here.parents:
        raise ValueError("outside root")
    if not here.is_dir():
        raise ValueError("not a directory")
    dirs: list[str] = []
    files: list[str] = []
    for entry in sorted(here.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            dirs.append(entry.name)
        elif entry.suffix in (".yml", ".yaml"):
            files.append(entry.name)
    return {
        "cwd": str(here),
        "parent": None if here == base else str(here.parent),
        "dirs": dirs,
        "files": files,
    }


def list_simulators(simctl: _simctl.RunFn = _simctl._real_run) -> list[dict[str, Any]]:
    """Available simulators for the device picker (booted first): udid, name, runtime, booted.
    A run boots any picked-but-shut-down device first, so the UI can start from a cold list."""
    try:
        data = json.loads(simctl(_simctl.list_devices_cmd(), None))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return []
    sims: list[dict[str, Any]] = []
    for runtime, devices in (data.get("devices") or {}).items():
        label = _simctl.runtime_label(runtime)
        for d in devices:
            if not d.get("isAvailable", True) or not d.get("udid"):
                continue
            sims.append(
                {
                    "udid": d["udid"],
                    "name": d.get("name", "?"),
                    "runtime": label,
                    "booted": d.get("state") == "Booted",
                }
            )
    sims.sort(key=lambda s: (not s["booted"], s["name"]))
    return sims


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Past runs under *runs_dir* (newest first), each summarized from its manifest.json for
    the history list.  Run ids are timestamps, so a reverse lexicographic sort is newest-first."""
    out: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        return out
    for d in runs_dir.iterdir():
        manifest = d / "manifest.json"
        if not (d.is_dir() and manifest.is_file()):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scenarios = [s for s in (data.get("scenarios") or []) if isinstance(s, dict)]
        out.append(
            {
                "id": d.name,
                "ok": bool(data.get("ok")),
                "report": (d / "report.html").is_file(),
                "scenarios": [str(s.get("scenario", "")) for s in scenarios],
                "passed": sum(1 for s in scenarios if s.get("ok")),
                "total": len(scenarios),
            }
        )
    out.sort(key=lambda r: r["id"], reverse=True)
    return out


def list_crawl_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Past crawl runs under *runs_dir* (newest first), each summarized from its screenmap.json.

    Unlike `list_runs`, which keys on manifest.json (a pass/fail scenario report a crawl never
    writes), this keys on screenmap.json — the one file every crawl streams regardless of outcome —
    so it is independent of whether a manifest also happens to exist. Each entry carries the
    screen/transition/crash counts the Crawl tab already shows and the names of the `crashes/*.yaml`
    and `flows/*.yaml` scenario files the run produced, so the UI can link straight to them via the
    existing `/runs/<id>/...` static mount. Run ids are timestamps, so a reverse sort is newest-first.
    """
    out: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        return out
    for d in runs_dir.iterdir():
        screenmap = d / "screenmap.json"
        if not (d.is_dir() and screenmap.is_file()):
            continue
        try:
            data = json.loads(screenmap.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            crawl_run_summary(
                d.name, data, _scenario_file_names(d / "crashes"), _scenario_file_names(d / "flows")
            )
        )
    out.sort(key=lambda r: r["id"], reverse=True)
    return out


def crawl_run_summary(
    run_id: str,
    screenmap: dict[str, Any],
    crash_files: list[str],
    flow_files: list[str],
) -> dict[str, Any]:
    """One crawl run's history-list entry, from its parsed screenmap.json and its crash/flow file names.

    Shared by the local filesystem scan (`list_crawl_runs`) and the object-store listing (BE-0190) so
    both backends emit an identical entry — the shape the Crawl tab consumes. *crash_files* /
    *flow_files* are the `crashes/*.yaml` / `flows/*.yaml` names the caller already resolved for its
    backend (a directory glob locally, object keys on the server).
    """
    return {
        "id": run_id,
        "screens": _list_len(screenmap.get("nodes")),
        "transitions": _list_len(screenmap.get("edges")),
        "crashes": _list_len(screenmap.get("crashes")),
        "crashFiles": crash_files,
        "flowFiles": flow_files,
        # Screens the run left with untried operations — what a full-frontier continuation
        # (BE-0181) would explore. Lets the Crawl tab offer "continue" only when >0.
        "frontier": _frontier_count(screenmap.get("plan")),
        "stopReason": str(screenmap.get("stop_reason") or ""),
    }


def _list_len(value: Any) -> int:
    """The length of *value* when it's a list, else 0 — so a missing or hand-corrupted screenmap
    count field (a dict, a scalar) summarizes as 0 instead of miscounting or raising."""
    return len(value) if isinstance(value, list) else 0


def _frontier_count(plan: Any) -> int:
    """Screens with a non-empty untried-operation list in *plan* — the size of a continuation's
    frontier. Tolerant of a missing or hand-corrupted `plan` (not a dict → 0), like `_list_len`."""
    if not isinstance(plan, dict):
        return 0
    return sum(1 for ops in plan.values() if isinstance(ops, list) and ops)


def _scenario_file_names(dir_: Path) -> list[str]:
    """Sorted `*.yaml` file names directly under *dir_* (a crawl's crashes/ or flows/), or []."""
    if not dir_.is_dir():
        return []
    # Only real files — a directory named `foo.yaml` also matches the glob and would become a dead link.
    return sorted(p.name for p in dir_.glob("*.yaml") if p.is_file())


# --- path helpers ---


def scenario_out_name(name: str) -> str:
    """A safe ``<stem>.yaml`` file name for an authored scenario.  ``name`` is the user's file name
    (or, lacking one, the goal); path separators and control chars are stripped so it can never
    escape its dir, and a blank / unusable name falls back to 'authored'.  A ``.yaml`` suffix is
    normalized so 'foo' and 'foo.yaml' name the same file.  The bare-name form, for a store with no
    filesystem dir (the server's object storage)."""
    stem = (name or "").strip().replace("/", "-").replace("\\", "-")
    if stem.endswith(".yaml"):
        stem = stem[: -len(".yaml")]
    stem = re.sub(r"[\x00-\x1f]", "", stem).strip(" .")
    if not stem or stem in {".", ".."}:
        stem = "authored"
    return f"{stem}.yaml"


def scenario_out_path(scenarios_dir: Path, name: str) -> Path:
    """A safe ``*.yaml`` path under *scenarios_dir* for an authored scenario named *name* (see
    `scenario_out_name` for the sanitization)."""
    return scenarios_dir / scenario_out_name(name)


def unique_scenario_path(path: Path, stamp: str | None = None) -> Path:
    """*path* if it's free, else the same stem with the run's date-time appended
    (``foo`` → ``foo-20260613-153045``) so authoring a scenario never overwrites an existing
    one."""
    if not path.exists():
        return path
    stamp = stamp or datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return path.parent / f"{path.stem}-{stamp}.yaml"


def valid_backend(backend: str) -> bool:
    """Whether `backend` is a comma-list of known platform/actuator tokens (not free text).
    Used to reject arbitrary `--backend` strings from a serve client before they reach argv."""
    tokens = [t.strip() for t in backend.split(",") if t.strip()]
    return bool(tokens) and all(t in _VALID_BACKENDS for t in tokens)


def valid_udid(udid: str) -> bool:
    """Whether `udid` is a comma-list of safe device tokens (the shared `device_id` policy, e.g. a
    UUID, a serial, or `booted`), so a serve client can't pass surprising free text through to the
    run argv — in particular a leading `-` that xcrun/adb would read as an option."""
    tokens = [t.strip() for t in udid.split(",") if t.strip()]
    return bool(tokens) and all(is_valid_device_id(t) for t in tokens)


def valid_run_id(run_id: str) -> bool:
    """Whether `run_id` is a single safe path segment, so ``runs_dir / run_id`` can't escape
    ``runs_dir`` — a resumed crawl takes the run id from the client."""
    return bool(_RUN_ID_RE.fullmatch(run_id))


def valid_scenario_ref(ref: str | None, *, allow_absolute: bool = False) -> bool:
    """Whether *ref* is an obviously safe scenario reference: a non-empty, NUL-free ``*.yaml`` with
    no ``..`` traversal (and, unless *allow_absolute*, a relative path).  A lightweight guard for a
    caller with no filesystem to resolve against — the server store, where a ref is a storage key —
    and for ordering the save handler's path error ahead of YAML parsing.  The local store still
    does full path-containment resolution (`_scenario_path`) on top of this, so it accepts an
    absolute path that lands inside its dir (the UI passes one)."""
    if not ref or "\x00" in ref:
        return False
    if not ref.endswith(".yaml"):
        return False
    pure = PurePosixPath(ref.replace("\\", "/"))  # treat a Windows separator as traversal too
    if ".." in pure.parts:
        return False
    return allow_absolute or not pure.is_absolute()


def valid_relative_key(key: str, *, allow_empty: bool = False) -> bool:
    """Whether *key* is a safe relative object-storage key segment (BE-0110): NUL-free, relative
    (no leading ``/``), and free of ``..`` traversal — so a client-supplied prefix or file path
    can't escape the base prefix the server prepends. *allow_empty* accepts the empty string, used
    for an optional per-run prefix (no extra segment)."""
    if not key:
        return allow_empty
    if "\x00" in key or key.startswith("/"):
        return False
    pure = PurePosixPath(key.replace("\\", "/"))  # a Windows separator counts as traversal too
    return ".." not in pure.parts and not pure.is_absolute()


def _scenario_path(scenarios_dir: Path, p: str | None) -> Path | None:
    """Resolve *p* (the path the UI passes for a scenario to read or save) to a ``*.yaml`` file
    inside *scenarios_dir*, or None if it would escape the dir or isn't a scenario file.  The
    file need not exist yet (saving a freshly authored scenario), but its parent must be the
    scenarios dir."""
    if not p:
        return None
    target = Path(p)
    if not target.is_absolute():
        target = scenarios_dir / target
    try:
        target = target.resolve()
    except ValueError:
        return None  # an invalid client path (e.g. an embedded NUL) is simply not a scenario
    base = scenarios_dir.resolve()
    if target != base and base not in target.parents:
        return None
    if target.suffix != ".yaml":
        return None
    return target
