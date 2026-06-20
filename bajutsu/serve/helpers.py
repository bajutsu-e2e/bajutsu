"""Pure helpers and CLI command builders for ``bajutsu serve``.

These functions are free of server state and fully unit-testable on their own.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bajutsu import env
from bajutsu.backends import KNOWN_ACTUATORS, PLATFORMS
from bajutsu.config import load_config, resolve
from bajutsu.scenario import load_scenario_file

# Tokens a `--backend` may name: a platform (ios/android/web/fake) or a known actuator (idb/…).
_VALID_BACKENDS = frozenset(PLATFORMS) | frozenset(KNOWN_ACTUATORS)
# A udid token: hex groups + hyphens, or the literal "booted". No spaces/metacharacters.
_UDID_RE = re.compile(r"^[A-Za-z0-9-]+$")
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


# --- query helpers ---


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    """Every ``*.yaml`` under *scenarios_dir*: a path the run command can take, the file-level
    ``description``, and each scenario's name + description (for the UI)."""
    out: list[dict[str, Any]] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        description: str | None = None
        scenarios: list[dict[str, Any]] = []
        try:
            sf = load_scenario_file(path.read_text(encoding="utf-8"))
            description = sf.description
            scenarios = [{"name": s.name, "description": s.description} for s in sf.scenarios]
        except (OSError, ValueError):
            pass
        out.append(
            {
                "file": path.name,
                "path": str(path),
                "description": description,
                "scenarios": scenarios,
                "names": [s["name"] for s in scenarios],
            }
        )
    return out


def list_apps(config_path: Path) -> list[str]:
    try:
        return sorted(load_config(config_path.read_text(encoding="utf-8")).apps)
    except (OSError, ValueError):
        return []


def app_build_info(config_path: Path, app: str) -> tuple[str | None, str | None]:
    """``(app_path, build)`` for *app* from config — the built ``.app`` path and the shell
    command that builds it.  Either may be ``None`` (unset or any load/resolve error); the run
    then proceeds without an on-demand build (and the runner reports a missing binary as
    before)."""
    try:
        eff = resolve(load_config(config_path.read_text(encoding="utf-8")), app)
    except (OSError, ValueError, KeyError):
        return (None, None)
    return (eff.app_path, eff.build)


def app_scenarios_dir(config_path: Path, app: str) -> Path | None:
    """The configured ``scenarios`` dir for *app*, or None (unset, or any load/resolve error).
    Mirrors ``app_build_info``; resolved relative to the run's working directory."""
    try:
        eff = resolve(load_config(config_path.read_text(encoding="utf-8")), app)
    except (OSError, ValueError, KeyError):
        return None
    return Path(eff.scenarios) if eff.scenarios else None


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


def list_simulators(simctl: env.RunFn = env._real_run) -> list[dict[str, Any]]:
    """Available simulators for the device picker (booted first): udid, name, runtime, booted.
    A run boots any picked-but-shut-down device first, so the UI can start from a cold list."""
    try:
        data = json.loads(simctl(env.list_devices_cmd(), None))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return []
    sims: list[dict[str, Any]] = []
    for runtime, devices in (data.get("devices") or {}).items():
        # "com.apple.CoreSimulator.SimRuntime.iOS-26-5" -> "iOS 26.5"
        label = runtime.split("SimRuntime.")[-1].replace("-", " ", 1).replace("-", ".")
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


# --- command builders ---


def _int(value: Any, default: int) -> int:
    """Coerce a JSON value to int, falling back to *default* (e.g. for ``workers``)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_command(
    scenario: str,
    app: str,
    *,
    backend: str = "",
    udid: str = "",
    workers: int = 1,
    erase: bool | None = None,
    dismiss_alerts: bool | None = None,
    config: str = "bajutsu.config.yaml",
    baselines: str = "",
) -> list[str]:
    """The ``python -m bajutsu run ...`` argv for a launch request.  ``udid`` may be a comma
    list and ``workers > 1`` runs those devices as a parallel pool (capped to the pool size by
    the CLI).  ``erase`` / ``dismiss_alerts`` are overrides: True/False force the flag on/off,
    None leaves each scenario's own preconditions.erase / dismissAlerts (the latter on by
    default) to decide."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        scenario,
        "--app",
        app,
        "--config",
        config,
        "--progress",
    ]  # stream per-scenario/step progress into the run log
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    if workers > 1:
        cmd += ["--workers", str(workers)]
    if erase is True:
        cmd += ["--erase"]
    elif erase is False:
        cmd += ["--no-erase"]
    if dismiss_alerts is True:
        cmd += ["--dismiss-alerts"]
    elif dismiss_alerts is False:
        cmd += ["--no-dismiss-alerts"]
    if baselines:
        cmd += ["--baselines", baselines]
    return cmd


def record_command(
    out: str,
    app: str,
    goal: str,
    *,
    agent: str = "",
    backend: str = "",
    udid: str = "",
    erase: bool | None = None,
    dismiss_alerts: bool | None = None,
    config: str = "bajutsu.config.yaml",
) -> list[str]:
    """The ``python -m bajutsu record --out OUT --app … --goal …`` argv for an authoring request —
    the Tier-1 record loop the Record tab drives.  ``agent`` picks the brain ("api" /
    "claude-code"); ``erase`` / ``dismiss_alerts`` mirror ``run_command`` (None leaves the CLI
    default — record erases and dismisses by default), and ``out`` is the ``*.yaml`` the
    recorded scenario is written to."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "record",
        "--out",
        out,
        "--app",
        app,
        "--goal",
        goal,
        "--config",
        config,
    ]
    if agent:
        cmd += ["--agent", agent]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    if erase is True:
        cmd += ["--erase"]
    elif erase is False:
        cmd += ["--no-erase"]
    if dismiss_alerts is True:
        cmd += ["--dismiss-alerts"]
    elif dismiss_alerts is False:
        cmd += ["--no-dismiss-alerts"]
    return cmd


def crawl_command(
    app: str,
    *,
    out: str,
    agent: str = "",
    backend: str = "",
    udid: str = "",
    max_screens: int = 50,
    max_steps: int = 200,
    erase: bool | None = None,
    dismiss_alerts: bool | None = None,
    config: str = "bajutsu.config.yaml",
    resume_src: str = "",
    resume_key: str = "",
) -> list[str]:
    """The ``python -m bajutsu crawl --app … --out …`` argv for a crawl request — the explorer the
    Crawl tab drives.  ``out`` is the run dir the screen map is streamed into
    (``<out>/screenmap.json``, which the UI polls live); ``erase`` mirrors ``run_command`` (None
    leaves the CLI default — crawl erases by default). Crawl is AI-driven, so ``agent`` is the
    brain that proposes what to try ("api" / "claude-code"); blank leaves the CLI default. When
    ``resume_src`` / ``resume_key`` are set, ``out`` points at an existing run and the crawl
    resumes one pruned branch, appending to that run's map instead of starting a fresh one."""
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "crawl",
        "--app",
        app,
        "--out",
        out,
        "--config",
        config,
        "--max-screens",
        str(max_screens),
        "--max-steps",
        str(max_steps),
    ]
    if agent:
        cmd += ["--agent", agent]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    if erase is True:
        cmd += ["--erase"]
    elif erase is False:
        cmd += ["--no-erase"]
    if dismiss_alerts is True:
        cmd += ["--dismiss-alerts"]
    elif dismiss_alerts is False:
        cmd += ["--no-dismiss-alerts"]
    if resume_src and resume_key:
        # Resuming appends to the existing run: don't erase the device's app state mid-walk.
        cmd += ["--resume-src", resume_src, "--resume-key", resume_key, "--no-erase"]
    return cmd


# --- path helpers ---


def scenario_out_path(scenarios_dir: Path, name: str) -> Path:
    """A safe ``*.yaml`` path under *scenarios_dir* for an authored scenario.  ``name`` is the
    user's file name (or, lacking one, the goal); path separators and control chars are stripped
    so a request can never escape the scenarios dir, and a blank / unusable name falls back to
    'authored'.  A ``.yaml`` suffix is normalized so 'foo' and 'foo.yaml' name the same file."""
    stem = (name or "").strip().replace("/", "-").replace("\\", "-")
    if stem.endswith(".yaml"):
        stem = stem[: -len(".yaml")]
    stem = re.sub(r"[\x00-\x1f]", "", stem).strip(" .")
    if not stem or stem in {".", ".."}:
        stem = "authored"
    return scenarios_dir / f"{stem}.yaml"


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
    """Whether `udid` is a comma-list of safe device tokens (hex/hyphen or `booted`), so a serve
    client can't pass surprising free text through to the run argv."""
    tokens = [t.strip() for t in udid.split(",") if t.strip()]
    return bool(tokens) and all(_UDID_RE.fullmatch(t) for t in tokens)


def valid_run_id(run_id: str) -> bool:
    """Whether `run_id` is a single safe path segment, so ``runs_dir / run_id`` can't escape
    ``runs_dir`` — a resumed crawl takes the run id from the client."""
    return bool(_RUN_ID_RE.fullmatch(run_id))


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
