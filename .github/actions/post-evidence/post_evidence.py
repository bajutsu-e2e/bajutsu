"""Post a finished Bajutsu run to a pull request or issue (BE-0504).

Standard library only: the composite action runs this with a bare Python and never installs
Bajutsu. It reads ``manifest.json`` (or ``<sid>/result.json`` when a run left no manifest) and
nothing else from Bajutsu, so the verdict it transports was already fixed by the deterministic run.

Two subcommands, one per phase of the action:

* ``stage``   copies the run directory, caps oversize device logs, and picks the files the
  non-zipped upload slots take.
* ``publish`` renders the Markdown, writes the job summary, and creates or updates the sticky
  comment.

Every setting arrives through ``PE_*`` environment variables, never through the command line.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The lowest manifest schemaVersion whose scenario/step/artifact fields this renderer reads: a
# scenario's scenario/ok/failure/sid, a step's index/action/ok/reason, and an artifact's kind/name
# are all original fields; every bump through v18 added optional ones this script never touches
# (see bajutsu/common/report/manifest.py's version log). A lower or unrecognized version makes the
# comment say so instead of rendering an empty block.
MIN_SCHEMA_VERSION = 1

SLOTS = 3
MAX_COMMENT_CHARS = 60_000
MAX_REASON_CHARS = 600
MARKER_FORMAT = "<!-- bajutsu-evidence:{key} -->"
_BAD_ARTIFACT_CHARS = re.compile(r'["`:<>|*?\\/\r\n]')
_STATE_FILE = "post-evidence-state.json"


@dataclass
class ScenarioView:
    """One scenario as the renderer needs it, whichever file it came from."""

    name: str
    ok: bool
    failure: str | None
    sid: str
    steps: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]

    def failed_step(self) -> dict[str, Any] | None:
        """The first step that did not pass, or ``None`` when only the scenario failed."""
        return next((s for s in self.steps if s.get("ok") is False), None)

    def screenshot(self) -> str | None:
        """Name of the failed step's screenshot, preferring ``after.png`` like the report."""
        step = self.failed_step()
        shots = [
            str(a["name"])
            for a in (step.get("artifacts") or [] if step else [])
            if a.get("kind") == "screenshot" and a.get("name")
        ]
        after = [n for n in shots if n.rsplit("/", 1)[-1] == "after.png"]
        picked = after or shots
        return picked[0] if picked else None

    def video(self) -> str | None:
        """Name of the scenario's video, when the manifest lists one."""
        return next(
            (str(a["name"]) for a in self.artifacts if a.get("kind") == "video" and a.get("name")),
            None,
        )

    def log_names(self) -> list[str]:
        """Names of every ``deviceLog`` artifact, scenario-wide or per step."""
        pools = [self.artifacts, *[s.get("artifacts") or [] for s in self.steps]]
        return [str(a["name"]) for p in pools for a in p if a.get("kind") == "deviceLog"]


@dataclass
class RunView:
    """One run directory: its manifest (if any) and the scenarios read from it."""

    key: str
    path: Path
    rel: str
    manifest: dict[str, Any] | None
    scenarios: list[ScenarioView] = field(default_factory=list)

    @property
    def schema_ok(self) -> bool:
        """Whether the manifest's version is one this renderer understands."""
        if self.manifest is None:
            return True
        version = self.manifest.get("schemaVersion")
        return isinstance(version, int) and version >= MIN_SCHEMA_VERSION


def _scenario_view(raw: dict[str, Any]) -> ScenarioView:
    failure = raw.get("failure")
    return ScenarioView(
        name=str(raw.get("scenario") or raw.get("sid") or "(unnamed)"),
        ok=raw.get("ok") is True,
        failure=str(failure) if failure else None,
        sid=str(raw.get("sid") or ""),
        steps=[s for s in raw.get("steps") or [] if isinstance(s, dict)],
        artifacts=[a for a in raw.get("artifacts") or [] if isinstance(a, dict)],
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_run(path: Path, rel: str) -> RunView | None:
    # The directory name, never the manifest's `runId`: a run id is a second-resolution UTC
    # timestamp, so two runs collected under one root can share one, which would collide in
    # `stage_runs` and make `run_root_of` resolve a slot to the wrong run. It also keeps
    # manifest text out of the staged path join.
    key = path.resolve().name or "run"
    manifest = _read_json(path / "manifest.json")
    if manifest is not None:
        entries = [e for e in manifest.get("scenarios") or [] if isinstance(e, dict)]
        return RunView(key, path, rel, manifest, [_scenario_view(e) for e in entries])
    results = sorted(path.glob("*/result.json"))
    if not results:
        return None
    views = []
    for result in results:
        body = _read_json(result)
        entry = body.get("scenario") if body else None
        if isinstance(entry, dict):
            views.append(_scenario_view(entry))
    return RunView(key, path, rel, None, views)


def load_runs(run_dir: Path) -> list[RunView]:
    """Read one run directory, or every run directly under a root that holds several."""
    single = _read_run(run_dir, "")
    if single is not None:
        return [single]
    runs = []
    for child in sorted(p for p in run_dir.iterdir() if p.is_dir()) if run_dir.is_dir() else []:
        run = _read_run(child, child.name)
        if run is not None:
            runs.append(run)
    return runs


def failed_scenarios(runs: list[RunView]) -> list[tuple[RunView, ScenarioView]]:
    """Every failed scenario across all runs, in run then scenario order."""
    return [(r, s) for r in runs for s in r.scenarios if not s.ok]


def sanitize_artifact_name(name: str) -> str:
    """Replace each character ``actions/upload-artifact`` rejects with ``-``."""
    return _BAD_ARTIFACT_CHARS.sub("-", name)


# ---------------------------------------------------------------------------------------------
# Staging


def _truncate_log(path: Path, keep_bytes: int) -> bool:
    size = path.stat().st_size
    if keep_bytes <= 0 or size <= keep_bytes:
        return False
    with path.open("rb") as fh:
        fh.seek(size - keep_bytes)
        tail = fh.read()
    note = f"[post-evidence: truncated, original {size} bytes, kept last {keep_bytes} bytes]\n"
    path.write_bytes(note.encode() + tail)
    return True


def _dir_size(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def stage_runs(
    runs: list[RunView], stage_dir: Path, log_tail_kb: int, size_warn_mb: int
) -> dict[str, Any]:
    """Copy each run under ``stage_dir/<key>/`` and cap its oversize device logs.

    Returns:
        The state the publish phase needs: truncated log names, the staged size in bytes, and
        whether the whole-run upload must be skipped for exceeding ``size_warn_mb``.
    """
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    truncated: list[str] = []
    for run in runs:
        dest = stage_dir / run.key
        shutil.copytree(run.path, dest)
        for scenario in run.scenarios:
            for name in scenario.log_names():
                target = dest / name
                if target.is_file() and _truncate_log(target, log_tail_kb * 1024):
                    truncated.append(f"{run.key}/{name}")
    total = _dir_size(stage_dir) if stage_dir.exists() else 0
    skip = size_warn_mb > 0 and total > size_warn_mb * 1024 * 1024
    return {"truncated": truncated, "bytes": total, "skipZip": skip}


def run_root_of(runs: list[RunView], rel: str) -> Path:
    """The source directory of the run a ``<key>/<name>`` slot path belongs to."""
    key = rel.split("/", 1)[0]
    return next(r.path for r in runs if r.key == key)


def select_slots(runs: list[RunView]) -> list[dict[str, str]]:
    """Pick the screenshot and video of each of the first failed scenarios, if they exist."""
    slots: list[dict[str, str]] = []
    for run, scenario in failed_scenarios(runs)[:SLOTS]:
        slot = {"screenshot": "", "video": ""}
        for kind, name in (("screenshot", scenario.screenshot()), ("video", scenario.video())):
            if name and (run.path / name).is_file():
                slot[kind] = f"{run.key}/{name}"
        slots.append(slot)
    return slots


# ---------------------------------------------------------------------------------------------
# Rendering


def _clean(text: str) -> str:
    """Strip comment delimiters repeatedly, so app text cannot forge a second marker."""
    previous = None
    while previous != text:
        previous = text
        text = text.replace("<!--", "").replace("-->", "")
    return text


def _longest_backtick_run(text: str) -> int:
    return max((len(m) for m in re.findall(r"`+", text)), default=0)


def code(text: str, limit: int = MAX_REASON_CHARS) -> str:
    """Wrap untrusted text in a code span, or a fenced block when it spans lines."""
    text = _clean(text)
    if len(text) > limit:
        text = text[:limit] + "…"
    ticks = _longest_backtick_run(text)
    if "\n" in text or "\r" in text:
        fence = "`" * max(3, ticks + 1)
        return f"\n{fence}\n{text}\n{fence}\n"
    delim = "`" * (ticks + 1)
    return f"{delim} {text} {delim}"


def _encode_path(path: str) -> str:
    return "/".join(urllib.parse.quote(seg, safe="") for seg in path.split("/"))


@dataclass
class RenderConfig:
    """Everything the renderer reads from the environment."""

    comment_key: str
    max_failures: int
    upload: bool
    artifact_url: str
    run_url: str
    sha: str
    base_url: str
    log_tail_kb: int
    size_warn_mb: int
    zip_status: str  # "ok" | "skipped-size" | "failed" | "external"
    state: dict[str, Any]
    slot_urls: list[dict[str, str]]


def _link(label: str, url: str) -> str:
    return f"[{label}]({url})" if url else label


def _evidence_lines(
    run: RunView, scenario: ScenarioView, index: int, cfg: RenderConfig
) -> list[str]:
    """Screenshot and video links for one failed scenario."""
    lines = []
    for kind, name in (("Screenshot", scenario.screenshot()), ("Video", scenario.video())):
        if not name:
            continue
        rel = f"{run.rel}/{name}" if run.rel else name
        slot = cfg.slot_urls[index] if index < len(cfg.slot_urls) else {}
        url = slot.get(kind.lower(), "")
        if cfg.base_url:
            href = f"{cfg.base_url.rstrip('/')}/{_encode_path(rel)}"
            lines.append(
                f"- {kind}: " + (f"![]({href})" if kind == "Screenshot" else f"[play]({href})")
            )
        elif url:
            lines.append(f"- {kind}: [open]({url})")
        else:
            where = f"{run.key}/{name}"
            suffix = "" if cfg.zip_status == "failed" else " in the whole-run artifact"
            lines.append(f"- {kind}: {code(where, 300)}{suffix}")
    return lines


def _failure_block(
    run: RunView, scenario: ScenarioView, index: int, cfg: RenderConfig
) -> list[str]:
    step = scenario.failed_step()
    out = [f"#### {code(scenario.name, 200)}"]
    if step is not None:
        label = f"step {step.get('index')} `{_clean(str(step.get('action') or '?'))}`"
        out.append(f"Failed at {label}: " + code(str(step.get("reason") or "no reason recorded")))
    else:
        out.append("Failed: " + code(scenario.failure or "no reason recorded"))
    out.extend(_evidence_lines(run, scenario, index, cfg))
    return out


def _header(runs: list[RunView], cfg: RenderConfig) -> list[str]:
    scenarios = [s for r in runs for s in r.scenarios]
    failed = sum(1 for s in scenarios if not s.ok)
    incomplete = not runs or any(r.manifest is None for r in runs)
    verdict = "incomplete" if incomplete else ("failed" if failed else "passed")
    sha = cfg.sha[:7]
    head = f"### Bajutsu run {verdict}: {len(scenarios) - failed} passed, {failed} failed"
    meta = [m for m in (f"commit `{sha}`" if sha else "", _link("workflow run", cfg.run_url)) if m]
    return [head, " · ".join(meta)] if meta else [head]


def _notices(runs: list[RunView], cfg: RenderConfig) -> list[str]:
    notes = []
    if not runs:
        notes.append(
            "> **No results found.** The run directory holds no `manifest.json` or "
            f"`<sid>/result.json`; see {_link('the workflow run', cfg.run_url)}."
        )
    for run in runs:
        if run.manifest is None:
            where = run.rel or run.key
            notes.append(
                f"> **The run did not complete** ({code(where, 120)} has no `manifest.json`). "
                f"Scenarios that finished are listed; see {_link('the workflow run', cfg.run_url)}."
            )
        elif not run.schema_ok:
            notes.append(
                f"> `{_clean(run.key)}` uses manifest schema "
                f"`{_clean(str(run.manifest.get('schemaVersion')))}`, which this action does not "
                f"understand (needs {MIN_SCHEMA_VERSION} or newer); scenario details are omitted."
            )
    state = cfg.state
    if cfg.upload and state.get("truncated"):
        notes.append(
            f"> {len(state['truncated'])} device log(s) were cut to their last "
            f"{cfg.log_tail_kb} KB; the full logs are uploaded nowhere."
        )
    if cfg.zip_status == "skipped-size":
        mb = state.get("bytes", 0) / (1024 * 1024)
        notes.append(
            f"> The whole-run upload was skipped: {mb:.0f} MB staged exceeds "
            f"`size-warn-mb` ({cfg.size_warn_mb} MB)."
        )
    if cfg.zip_status == "failed":
        notes.append(f"> The evidence upload failed; see {_link('the workflow run', cfg.run_url)}.")
    return notes


def render(runs: list[RunView], cfg: RenderConfig) -> str:
    """Render the sticky comment body, marker first."""
    lines = [MARKER_FORMAT.format(key=_clean(cfg.comment_key)), ""]
    lines += _header(runs, cfg)
    lines += ["", *_notices(runs, cfg)]
    failed = failed_scenarios(runs)
    shown = failed[: cfg.max_failures]
    for index, (run, scenario) in enumerate(shown):
        if run.schema_ok:
            lines += ["", *_failure_block(run, scenario, index, cfg)]
    if len(failed) > len(shown):
        lines += ["", f"…and {len(failed) - len(shown)} more failed scenario(s) not listed."]
    if cfg.base_url:
        lines += ["", f"[Full report]({cfg.base_url.rstrip('/')}/report.html)"]
    elif cfg.artifact_url and cfg.zip_status in ("ok", "external"):
        lines += ["", f"[Whole run (zip)]({cfg.artifact_url})"]
    body = "\n".join(lines).rstrip() + "\n"
    if len(body) > MAX_COMMENT_CHARS:
        body = body[:MAX_COMMENT_CHARS].rstrip() + "\n\n…comment truncated."
    return body


# ---------------------------------------------------------------------------------------------
# GitHub API


def _api(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - the URL is GITHUB_API_URL, an https endpoint
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode() or "null")


def is_marker_line(first_line: str, key: str) -> bool:
    """Whether a comment's first line is this key's marker, tolerant of CRLF and trailing space."""
    return first_line.rstrip() == MARKER_FORMAT.format(key=key)


def find_comment(comments_url: str, token: str, key: str, author: str) -> int | None:
    """Page through every comment and return the id of the sticky one, if any."""
    page = 1
    while True:
        batch = _api("GET", f"{comments_url}?per_page=100&page={page}", token)
        for comment in batch:
            first = (comment.get("body") or "").split("\n", 1)[0]
            login = (comment.get("user") or {}).get("login")
            if login == author and is_marker_line(first, key):
                return int(comment["id"])
        if len(batch) < 100:
            return None
        page += 1


def upsert_comment(
    api_url: str, repo: str, number: str, token: str, key: str, author: str, body: str
) -> str:
    """Update the sticky comment, or create it. Returns ``updated`` or ``created``."""
    comments_url = f"{api_url}/repos/{repo}/issues/{number}/comments"
    existing = find_comment(comments_url, token, key, author)
    if existing is not None:
        _api("PATCH", f"{api_url}/repos/{repo}/issues/comments/{existing}", token, {"body": body})
        return "updated"
    _api("POST", comments_url, token, {"body": body})
    return "created"


# ---------------------------------------------------------------------------------------------
# Entry points


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"PE_{name}", default)


def _upload_enabled() -> bool:
    return _env("UPLOAD", "true") == "true"


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _out(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:  # noqa: PTH123
            fh.write(f"{name}={value}\n")


def _warn(message: str) -> None:
    sys.stdout.write(f"::warning::{message}\n")


def _state_path() -> Path:
    return Path(os.environ.get("RUNNER_TEMP", ".")) / _STATE_FILE


def _stage() -> int:
    """Stage the run, pick the slots, and expose them as step outputs."""
    runs = load_runs(Path(_env("RUN_DIR", "runs")))
    stage_dir = Path(os.environ.get("RUNNER_TEMP", ".")) / "post-evidence-stage"
    state: dict[str, Any] = {"truncated": [], "bytes": 0, "skipZip": False}
    if _upload_enabled() and runs:
        state = stage_runs(runs, stage_dir, _int("LOG_TAIL_KB", 1024), _int("SIZE_WARN_MB", 500))
    slots = select_slots(runs) if _upload_enabled() else []
    # A non-zipped upload names the artifact after the file, so each slot file is copied under a
    # unique name: three failing steps would otherwise all upload an `after.png`.
    slot_dir = Path(os.environ.get("RUNNER_TEMP", ".")) / "post-evidence-slots"
    shutil.rmtree(slot_dir, ignore_errors=True)
    prefix = sanitize_artifact_name(_env("ARTIFACT_NAME", "bajutsu-run"))
    for i in range(SLOTS):
        slot = slots[i] if i < len(slots) else {"screenshot": "", "video": ""}
        for kind in ("screenshot", "video"):
            rel = slot[kind]
            out = ""
            if rel:
                slot_dir.mkdir(parents=True, exist_ok=True)
                dest = slot_dir / f"{prefix}-{kind}-{i + 1}{Path(rel).suffix}"
                shutil.copy2(run_root_of(runs, rel) / rel.split("/", 1)[1], dest)
                out = str(dest)
            _out(f"{kind}-{i + 1}", out)
    _out("stage-dir", str(stage_dir))
    _out("artifact-name", prefix)
    _out("upload-zip", "true" if runs and not state["skipZip"] and _upload_enabled() else "")
    state["slots"] = slots
    _state_path().write_text(json.dumps(state), encoding="utf-8")
    return 0


def _zip_status(upload: bool, state: dict[str, Any]) -> str:
    if not upload:
        return "external"
    if state.get("skipZip"):
        return "skipped-size"
    outcome = _env("ZIP_OUTCOME")
    return "ok" if outcome == "success" else ("none" if outcome == "skipped" else "failed")


def _publish() -> int:
    """Render, write the job summary, and post the sticky comment (never failing the job)."""
    runs = load_runs(Path(_env("RUN_DIR", "runs")))
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    upload = _upload_enabled()
    status = _zip_status(upload, state)
    artifact_url = _env("ZIP_URL") if upload else _env("ARTIFACT_URL")
    cfg = RenderConfig(
        comment_key=_env("COMMENT_KEY", "bajutsu"),
        max_failures=_int("MAX_FAILURES", 10),
        upload=upload,
        artifact_url=artifact_url or (run_url if not upload else ""),
        run_url=run_url,
        sha=os.environ.get("GITHUB_SHA", ""),
        base_url=_env("EVIDENCE_BASE_URL"),
        log_tail_kb=_int("LOG_TAIL_KB", 1024),
        size_warn_mb=_int("SIZE_WARN_MB", 500),
        zip_status=status,
        state=state,
        slot_urls=[
            {"screenshot": _env(f"SHOT_URL_{i}"), "video": _env(f"VIDEO_URL_{i}")}
            for i in range(1, SLOTS + 1)
        ],
    )
    body = render(runs, cfg)
    _out("artifact-url", artifact_url if status == "ok" else "")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:  # noqa: PTH123
            fh.write(body)
    number = _env("ISSUE_NUMBER")
    if not number:
        return 0
    try:
        verb = upsert_comment(
            os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            repo,
            number,
            _env("TOKEN"),
            cfg.comment_key,
            _env("COMMENT_AUTHOR", "github-actions[bot]"),
            body,
        )
        sys.stdout.write(f"Evidence comment {verb} on #{number}.\n")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        _warn(f"could not post the evidence comment ({type(exc).__name__}: {exc}); summary only")
    return 0


def _guarded(name: str, fn: Callable[[], int]) -> Callable[[], int]:
    """Turn any crash into a warning: this action never changes the job's result."""

    def run() -> int:
        try:
            return fn()
        except Exception as exc:
            _warn(f"post-evidence {name} failed ({type(exc).__name__}: {exc}); continuing")
            return 0

    return run


def cmd_stage() -> int:
    """Stage the run, pick the slots, and expose them as step outputs (never failing the job)."""
    return _guarded("stage", _stage)()


def cmd_publish() -> int:
    """Render, write the job summary, and post the sticky comment (never failing the job)."""
    return _guarded("publish", _publish)()


def main(argv: list[str]) -> int:
    """Dispatch ``stage`` or ``publish``."""
    commands = {"stage": cmd_stage, "publish": cmd_publish}
    if len(argv) != 2 or argv[1] not in commands:
        sys.stderr.write("usage: post_evidence.py {stage|publish}\n")
        return 2
    return commands[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
