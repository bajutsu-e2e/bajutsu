#!/usr/bin/env python3
"""Post a finished Bajutsu run's evidence to a pull request or an issue (BE-0503).

    python3 scripts/post_evidence.py runs/<runId> --to owner/repo#123 --dry-run
    python3 scripts/post_evidence.py runs/<runId> --to owner/repo#123

The comment states the verdict and the counts, names each failed scenario with its first failed
step and reason, and links the run's evidence. A rerun edits the same comment, found by the hidden
marker `<!-- bajutsu-evidence:<key> -->` on its first line and the token's own login.

This file is deliberately standalone: it uses the standard library alone and imports nothing from
`bajutsu`, so a team can copy it into a repository with no Bajutsu checkout. The `post-evidence`
GitHub Action (BE-0504) renders through this same file. It runs after the verdict and never
changes it: the exit status reports whether the post succeeded, not whether the run passed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

API_URL_ENV = "GITHUB_API_URL"
DEFAULT_API_URL = "https://api.github.com"
DEFAULT_KEY = "local"
MARKER_PREFIX = "bajutsu-evidence:"
KEY_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
TARGET_PATTERN = re.compile(r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)#(?P<number>\d+)")
URL_PATTERN = re.compile(
    r"https?://[^/]+/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/(?:pull|issues)/(?P<number>\d+)(?:[/?#].*)?"
)
PAGE_SIZE = 100
SCREENSHOT_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# Exit statuses. A failed run is not among them: the verdict never reaches the exit status.
EXIT_OK = 0
EXIT_POST_FAILED = 1
EXIT_USAGE = 2


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect, carrying the token only when the target stays on the same host."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        auth = req.unredirected_hdrs.get("Authorization")
        same = urllib.parse.urlsplit(newurl).netloc == urllib.parse.urlsplit(req.full_url).netloc
        if new is not None and auth and same:
            new.add_unredirected_header("Authorization", auth)
        return new


class PostError(Exception):
    """A post that could not be completed; the message is the reason shown to the developer."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --- The run -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    """One failed scenario, reduced to what the comment shows."""

    name: str
    step: str
    reason: str
    screenshots: tuple[str, ...]
    videos: tuple[str, ...]


@dataclass(frozen=True)
class RunView:
    """What the renderer reads from a run directory."""

    run_id: str
    ok: bool
    passed: int
    failed: int
    failures: tuple[Failure, ...]
    from_manifest: bool
    notes: tuple[str, ...] = ()


def _dicts(value: object) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _artifact_paths(entries: object, kind: str, prefix: str) -> list[str]:
    # A manifest names each artifact by its path relative to the run directory; a matrix run's
    # `result.json` names it relative to `<engine>/`, which `prefix` restores.
    paths = []
    for a in _dicts(entries):
        name = str(a.get("name") or "")
        if not name:
            continue
        is_kind = a.get("kind") == kind or (
            kind == "screenshot" and name.lower().endswith(SCREENSHOT_SUFFIXES)
        )
        if is_kind:
            paths.append(prefix + name)
    return paths


def _failure(scenario: Mapping[str, Any], prefix: str) -> Failure:
    steps = _dicts(scenario.get("steps"))
    failed = next((s for s in steps if s.get("ok") is False), None)
    if failed is None:
        step = "scenario expectations"
        reason = str(scenario.get("failure") or "")
        screenshots: list[str] = []
    else:
        step = f"step {failed.get('index', '?')} ({failed.get('action', '?')})"
        reason = str(failed.get("reason") or scenario.get("failure") or "")
        screenshots = _artifact_paths(failed.get("artifacts"), "screenshot", prefix)
    return Failure(
        name=str(scenario.get("scenario") or ""),
        step=step,
        reason=reason,
        screenshots=tuple(screenshots),
        videos=tuple(_artifact_paths(scenario.get("artifacts"), "video", prefix)),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PostError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise PostError(f"{path} does not hold a JSON object")
    return data


def _partial_scenarios(run_dir: Path) -> tuple[list[tuple[dict[str, Any], str]], list[str]]:
    # A run killed partway leaves no manifest, only each finished scenario's `result.json`, at
    # `<sid>/result.json` or, on a cross-browser run, `<engine>/<sid>/result.json`.
    # A kill can cut a file off mid-write, so an unreadable one is skipped and noted rather than
    # hiding every scenario that did finish.
    found: list[tuple[dict[str, Any], str]] = []
    notes: list[str] = []
    for path in sorted([*run_dir.glob("*/result.json"), *run_dir.glob("*/*/result.json")]):
        rel = path.relative_to(run_dir)
        try:
            scenario = _read_json(path).get("scenario")
        except PostError as e:
            cause = str(e.__cause__ or e).splitlines()[0] if str(e) else "unreadable"
            notes.append(f"{literal(rel.as_posix())} skipped: {literal(cause)}")
            continue
        if isinstance(scenario, dict):
            prefix = f"{path.parent.parent.name}/" if len(rel.parts) == 3 else ""
            found.append((scenario, prefix))
    return found, notes


def load_run(run_dir: Path) -> RunView:
    """Read a run directory's verdict from `manifest.json`, or its `result.json` files.

    Raises:
        PostError: The directory holds neither a manifest nor any scenario result.
    """
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        if not isinstance(manifest.get("ok"), bool) or not isinstance(
            manifest.get("scenarios"), list
        ):
            raise PostError(f"{manifest_path} lacks a boolean `ok` or a `scenarios` list")
        scenarios = [(s, "") for s in _dicts(manifest.get("scenarios"))]
        notes: list[str] = []
        run_id = str(manifest.get("runId") or run_dir.name)
        ok = bool(manifest.get("ok"))
    else:
        scenarios, notes = _partial_scenarios(run_dir)
        if not scenarios:
            raise PostError(f"{run_dir} holds no manifest.json and no <sid>/result.json")
        run_id = run_dir.name
        ok = all(bool(s.get("ok")) for s, _ in scenarios)
    failures = tuple(_failure(s, p) for s, p in scenarios if not s.get("ok"))
    return RunView(
        run_id=run_id,
        ok=ok,
        passed=len(scenarios) - len(failures),
        failed=len(failures),
        failures=failures,
        from_manifest=manifest_path.is_file(),
        notes=tuple(notes),
    )


# --- Rendering ---------------------------------------------------------------------------------


def strip_comment_markers(text: str) -> str:
    """Remove `<!--` and `-->` until none remain, so run text cannot forge a marker."""
    while "<!--" in text or "-->" in text:
        text = text.replace("<!--", "").replace("-->", "")
    return text


def _longest_backtick_run(text: str) -> int:
    return max((len(m) for m in re.findall(r"`+", text)), default=0)


def literal(text: str) -> str:
    """Quote text copied from a run so Markdown shows it verbatim.

    A single line becomes a code span and several lines a fenced block, each delimited by a
    backtick run one longer than the longest inside, so the text can neither close its own quote
    nor add a mention, HTML, or a second marker.
    """
    text = strip_comment_markers(text)
    if "\n" in text or "\r" in text:
        fence = "`" * max(3, _longest_backtick_run(text) + 1)
        return f"\n\n{fence}text\n{text.rstrip()}\n{fence}\n"
    if not text:
        return "(none)"
    ticks = "`" * (_longest_backtick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def evidence_url(base_url: str, path: str) -> str:
    """Join a run-relative path to a base URL, percent-encoding each segment."""
    segments = [urllib.parse.quote(s, safe="") for s in path.split("/") if s]
    return base_url.rstrip("/") + "/" + "/".join(segments)


def marker(key: str) -> str:
    """The hidden first line that identifies this script's sticky comment for `key`."""
    return f"<!-- {MARKER_PREFIX}{key} -->"


def _evidence_lines(failure: Failure, links: Mapping[str, str]) -> list[str]:
    lines = []
    for path in failure.screenshots:
        if path in links:
            lines.append(f"  - Screenshot {literal(path)}:<br>![failing step]({links[path]})")
        else:
            lines.append(f"  - Screenshot {literal(path)}")
    for path in failure.videos:
        if path in links:
            lines.append(f"  - [Video]({links[path]}) {literal(path)}")
        else:
            lines.append(f"  - Video {literal(path)}")
    return lines


def _failure_block(failure: Failure, links: Mapping[str, str]) -> list[str]:
    lines = [
        f"#### {literal(failure.name)}",
        "",
        f"- Failed at: {literal(failure.step)}",
        f"- Reason: {literal(failure.reason)}",
    ]
    evidence = _evidence_lines(failure, links)
    if evidence:
        lines += ["- Evidence:", *evidence]
    return [*lines, ""]


def render(
    run: RunView,
    *,
    key: str,
    links: Mapping[str, str],
    max_failures: int,
    notes: Sequence[str] = (),
    run_link: str | None = None,
) -> str:
    """Render the comment's Markdown.

    Args:
        run: The run to describe.
        key: The sticky comment's key; it must already have passed `check_key`.
        links: Run-relative evidence path to the URL the comment links it with. A path without a
            link is named but not linked.
        max_failures: How many failed scenarios get a block; the rest are counted.
        notes: Lines stating each truncation and skipped file.
        run_link: A link to the whole run (a zip asset or a hosted report), if any.
    """
    verdict = "passed" if run.ok else "failed"
    total = run.passed + run.failed
    lines = [
        marker(key),
        f"### Bajutsu run {literal(run.run_id)}: {verdict}",
        "",
        f"{run.passed} passed, {run.failed} failed, {total} scenarios.",
    ]
    if not run.from_manifest:
        lines.append("The run left no `manifest.json`; this reads each finished scenario's result.")
    if run_link:
        lines.append(f"[Whole run]({run_link})")
    lines.append("")
    for failure in run.failures[: max(max_failures, 0)]:
        lines += _failure_block(failure, links)
    omitted = len(run.failures) - max(max_failures, 0)
    if omitted > 0:
        lines += [f"{omitted} more failed scenarios are not shown.", ""]
    if notes:
        lines += ["<details><summary>Notes</summary>", ""]
        lines += [f"- {strip_comment_markers(n)}" for n in notes]
        lines += ["", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


# --- Arguments ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """The pull request or issue the comment goes to."""

    owner: str
    repo: str
    number: int
    host: str | None = None  # the web host a URL named; None for the `owner/repo#N` form

    @property
    def slug(self) -> str:
        """`owner/repo`, the API's repository path."""
        return f"{self.owner}/{self.repo}"


def parse_target(text: str) -> Target:
    """Parse `owner/repo#123` or a pull request or issue URL.

    A bare `#123` is refused: in a fork workflow `origin` is the fork while the pull request lives
    upstream, and the two share no numbering, so a guess could post private pixels to the wrong
    thread.

    Raises:
        PostError: The text names no repository and number.
    """
    text = text.strip()
    if match := TARGET_PATTERN.fullmatch(text):
        return Target(match["owner"], match["repo"], int(match["number"]))
    if match := URL_PATTERN.fullmatch(text):
        return Target(
            match["owner"], match["repo"], int(match["number"]), urllib.parse.urlsplit(text).netloc
        )
    raise PostError(f"--to must be owner/repo#NUMBER or a pull request or issue URL, not {text!r}")


def check_host(target: Target, api_url: str) -> None:
    """Refuse a URL target whose host is not the instance the API URL talks to.

    The API host comes from `GITHUB_API_URL`, never from `--to`, so a URL on another instance
    would otherwise post to the same `owner/repo#N` on github.com, with private pixels attached.

    Raises:
        PostError: The URL's host and the API's web host differ.
    """
    if target.host is None:
        return
    netloc = urllib.parse.urlsplit(api_url).netloc.lower()
    api_web_host = (
        netloc.removeprefix("api.") if netloc.endswith((".github.com", ".ghe.com")) else netloc
    )
    if target.host.lower() != api_web_host:
        raise PostError(
            f"--to names host {target.host}, but the API URL {api_url} serves {api_web_host}"
        )


def check_key(key: str) -> str:
    """Return `key` if it is safe inside the marker.

    The match is anchored over the whole value: `re.match` would accept `local-->` on its prefix,
    and a `-->` or a newline would close the marker early and post a duplicate on every run.

    Raises:
        PostError: The key holds anything outside `[A-Za-z0-9._-]`.
    """
    if KEY_PATTERN.fullmatch(key) is None:
        raise PostError(f"--key must match [A-Za-z0-9._-]+, not {key!r}")
    return key


def resolve_run_dir(arg: str, runs: Path) -> Path:
    """A run directory given as a path, or as a run id under `runs`.

    Raises:
        PostError: Neither names a directory.
    """
    for candidate in (Path(arg), runs / arg):
        if candidate.is_dir():
            return candidate
    raise PostError(f"no run directory at {arg} or {runs / arg}")


def resolve_token(
    env: Mapping[str, str],
    run_gh: Callable[[], str | None] | None = None,
) -> str | None:
    """The token from `GITHUB_TOKEN` or `GH_TOKEN`, else from `gh auth token`, else None."""
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if env.get(name):
            return env[name]
    return (run_gh or _gh_auth_token)()


def _gh_auth_token() -> str | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    try:
        done = subprocess.run(
            [gh, "auth", "token"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = done.stdout.strip()
    return token if done.returncode == 0 and token else None


# --- GitHub ------------------------------------------------------------------------------------


@dataclass
class GitHub:
    """A minimal REST client over `urllib`, so the file stays copyable on its own."""

    token: str
    api_url: str = DEFAULT_API_URL

    def request(
        self,
        method: str,
        url: str,
        body: object = None,
        *,
        data: bytes | None = None,
        content_type: str = "application/json",
        accept: str = "application/vnd.github+json",
    ) -> Any:
        """Send one request and decode its reply: JSON (None when empty), or text for a raw `accept`.

        Raises:
            PostError: The request failed or GitHub answered with an error status.
        """
        full = url if url.startswith("http") else self.api_url.rstrip("/") + url
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(full, data=data, method=method)  # noqa: S310 — the API URL
        # Unredirected, so a 30x can never carry the token to another host.
        req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", accept)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "bajutsu-post-evidence")
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.build_opener(_SameHostRedirect).open(req, timeout=120) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise PostError(f"{method} {full} failed: HTTP {e.code}: {detail}", e.code) from e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise PostError(f"{method} {full} failed: {e}") from e
        if "json" not in accept:
            return raw.decode("utf-8", "replace")
        try:
            return json.loads(raw) if raw.strip() else None
        except ValueError as e:
            raise PostError(f"{method} {full} returned a reply that is not JSON") from e

    def paged(self, path: str) -> Iterator[dict[str, Any]]:
        """Every item of a list endpoint, page by page."""
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            items = self.request("GET", f"{path}{sep}per_page={PAGE_SIZE}&page={page}")
            if not isinstance(items, list):
                raise PostError(f"GET {path} returned {type(items).__name__}, not a list")
            yield from (i for i in items if isinstance(i, dict))
            if len(items) < PAGE_SIZE:
                return
            page += 1

    def login(self) -> str:
        """The login `GET /user` returns for the token.

        Raises:
            PostError: The token has no user (an installation token); pass `--author`.
        """
        try:
            user = self.request("GET", "/user")
        except PostError as e:
            if e.status in (403, 404):
                raise PostError(f"{e}\nA token with no user needs --author LOGIN.", e.status) from e
            raise
        login = user.get("login") if isinstance(user, dict) else None
        if not login:
            raise PostError("GET /user returned no login; pass --author LOGIN")
        return str(login)


def is_own_comment(comment: Mapping[str, Any], key: str, author: str) -> bool:
    """True when `comment` is this script's sticky comment for `key` by `author`.

    The first line is compared after stripping trailing whitespace and a carriage return, because
    a comment edited in the browser can come back with CRLF line endings.
    """
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    first = str(comment.get("body") or "").split("\n", 1)[0].rstrip()
    return login == author and first == marker(key)


def upsert_comment(gh: GitHub, target: Target, key: str, author: str, body: str) -> str:
    """Edit this key's sticky comment, or create it, and return its URL."""
    issue = f"/repos/{target.slug}/issues/{target.number}"
    existing = next(
        (c for c in gh.paged(f"{issue}/comments") if is_own_comment(c, key, author)), None
    )
    if existing is not None:
        url = f"/repos/{target.slug}/issues/comments/{existing['id']}"
        result = gh.request("PATCH", url, {"body": body})
    else:
        result = gh.request("POST", f"{issue}/comments", {"body": body})
    html_url = result.get("html_url") if isinstance(result, dict) else None
    if not html_url:
        raise PostError(f"the comment on {target.slug}#{target.number} came back with no URL")
    return str(html_url)


# --- Sinks and staging -------------------------------------------------------------------------


@dataclass(frozen=True)
class Staged:
    """One file to upload, copied out of the run directory."""

    path: str  # run-relative, as the comment names it
    source: Path  # inside the staging directory
    size: int


@dataclass
class Plan:
    """What the script will write, shown before it writes anything."""

    target: Target
    sink: str
    visibility: str = "unknown"
    release: dict[str, Any] | None = None
    files: list[Staged] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    workflows_error: str = ""
    remote_checked: bool = False

    @property
    def tag(self) -> str:
        """The tag publishing the release creates."""
        return release_tag(self.target.number)


def release_tag(number: int) -> str:
    """The evidence release's tag for a pull request or issue number."""
    return f"bajutsu-evidence-{number}"


def asset_name(run_id: str, path: str) -> str:
    """A release asset name unique per run and file, so a rerun never needs to delete one."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", f"{run_id}-{path}").strip("-")


def _is_device_log(path: str) -> bool:
    return path.endswith("device.log")


def _inside(run_dir: Path, path: str) -> Path | None:
    """The file `path` names under `run_dir`, or None when it would leave the run directory.

    A manifest can come from a CI job or a fork, so an artifact name such as `../x` or an absolute
    path must never make the script upload a file from outside the run.
    """
    root = run_dir.resolve()
    candidate = (root / path).resolve()
    return candidate if candidate.is_relative_to(root) else None


def stage(
    run_dir: Path,
    run: RunView,
    staging: Path,
    *,
    max_failures: int,
    log_tail_kb: int,
    size_warn_mb: int,
) -> tuple[list[Staged], list[str]]:
    """Copy the files the `release` sink uploads into `staging`, applying the guards.

    The run directory is never written. The files are copied, and the zip is built from the run
    directory, truncating each oversize `device.log` as it enters the archive. When the set exceeds `size_warn_mb`, the zip is skipped first,
    then any single file that still exceeds it, and each skip is noted.
    """
    notes: list[str] = []
    tail_bytes = max(log_tail_kb, 1) * 1024
    wanted: list[str] = []
    for f in run.failures[: max(max_failures, 0)]:
        wanted += [p for p in (*f.screenshots, *f.videos) if p not in wanted]
    copies = staging / "files"
    files: list[Staged] = []
    for path in wanted:
        src = _inside(run_dir, path)
        if src is None:
            notes.append(f"{literal(path)} skipped: the path leaves the run directory.")
            continue
        if not src.is_file():
            notes.append(f"{literal(path)} is named by the run but missing from the run directory.")
            continue
        dest = copies / src.relative_to(run_dir.resolve())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        files.append(Staged(path, dest, dest.stat().st_size))
    zipped = staging / (asset_name("", run.run_id) + ".zip")
    _zip_run(run_dir, zipped, tail_bytes, notes)
    files.append(Staged(f"{run.run_id}.zip", zipped, zipped.stat().st_size))
    return _apply_size_guard(files, size_warn_mb * 1024 * 1024, run.run_id, notes)


def _zip_run(run_dir: Path, dest: Path, tail_bytes: int, notes: list[str]) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for src in sorted(p for p in run_dir.rglob("*") if p.is_file() and not p.is_symlink()):
            rel = src.relative_to(run_dir).as_posix()
            size = src.stat().st_size
            if _is_device_log(rel) and size > tail_bytes:
                with src.open("rb") as f:
                    f.seek(size - tail_bytes)
                    z.writestr(rel, f.read())
                notes.append(
                    f"{literal(rel)} truncated to its last {tail_bytes // 1024} KB in the zip."
                )
            else:
                z.write(src, rel)


def _apply_size_guard(
    files: list[Staged], limit: int, run_id: str, notes: list[str]
) -> tuple[list[Staged], list[str]]:
    if sum(f.size for f in files) <= limit:
        return files, notes
    zip_path = f"{run_id}.zip"
    kept = [f for f in files if f.path != zip_path]
    notes.append(f"{literal(zip_path)} skipped: the upload exceeds {limit // (1024 * 1024)} MB.")
    for f in [f for f in kept if f.size > limit]:
        kept.remove(f)
        notes.append(f"{literal(f.path)} skipped: {f.size // (1024 * 1024)} MB exceeds the limit.")
    return kept, notes


def find_release(gh: GitHub, target: Target) -> dict[str, Any] | None:
    """The evidence release for `target`, matched by exact tag name.

    Releases are listed rather than fetched by tag, because the tag endpoint does not return
    drafts, and a draft has no tag yet.
    """
    tag = release_tag(target.number)
    return next(
        (r for r in gh.paged(f"/repos/{target.slug}/releases") if r.get("tag_name") == tag), None
    )


def tag_workflows(gh: GitHub, target: Target) -> tuple[list[str], str]:
    """Workflows in the target repository whose triggers mention a release, a push, or a tag.

    A plain text check, not a YAML parse: it errs toward listing a workflow, because the plan's
    job is to warn before publishing fires something. The second value says why the list could not
    be completed, so the plan never reads "none found" for a search that did not happen.
    """
    try:
        entries = gh.request("GET", f"/repos/{target.slug}/contents/.github/workflows")
    except PostError as e:
        return [], "" if e.status == 404 else str(e)
    found: list[str] = []
    unread = 0
    for entry in entries if isinstance(entries, list) else []:
        name = str(entry.get("name", "")) if isinstance(entry, dict) else ""
        if not name.endswith((".yml", ".yaml")):
            continue
        try:
            # The contents API in raw form, so the token goes to the API host alone.
            text = gh.request("GET", str(entry.get("url")), accept="application/vnd.github.raw")
        except PostError:
            unread += 1
            continue
        if re.search(r"\b(release|push|create)\b|\btags\s*:", text):
            found.append(name)
    return found, f"{unread} workflow file(s) could not be read" if unread else ""


def upload_assets(
    gh: GitHub, target: Target, plan: Plan, run_id: str, publish: bool
) -> dict[str, str]:
    """Create or reuse the release, upload the staged files, and return each file's link."""
    release = plan.release
    if release is None:
        release = gh.request(
            "POST",
            f"/repos/{target.slug}/releases",
            {
                "tag_name": plan.tag,
                "name": f"bajutsu-evidence #{target.number}",
                "body": f"Evidence that scripts/post_evidence.py uploaded for #{target.number}.",
                "draft": not publish,
                "prerelease": True,
                "make_latest": "false",
            },
        )
    elif publish and release.get("draft"):
        release = gh.request(
            "PATCH",
            f"/repos/{target.slug}/releases/{release['id']}",
            {"draft": False, "make_latest": "false"},
        )
    if not isinstance(release, dict):
        raise PostError("GitHub returned no release")
    existing = {str(a.get("name")): a for a in _dicts(release.get("assets"))}
    upload_base = str(release.get("upload_url", "")).split("{", 1)[0]
    if not upload_base:
        raise PostError(f"release {release.get('id')} has no upload_url")
    links: dict[str, str] = {}
    for f in plan.files:
        name = asset_name(run_id, f.path)
        asset = existing.get(name)
        if asset is not None and asset.get("state", "uploaded") != "uploaded":
            raise PostError(
                f"asset {name} on the release is stuck in state {asset.get('state')!r}; "
                "delete it on GitHub and run the script again"
            )
        if asset is None:
            query = urllib.parse.urlencode({"name": name})
            try:
                payload = f.source.read_bytes()
            except OSError as e:
                raise PostError(f"cannot read {f.source}: {e}") from e
            asset = gh.request(
                "POST",
                f"{upload_base}?{query}",
                data=payload,
                content_type="application/octet-stream",
            )
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if not url:
            raise PostError(f"the upload of {f.path} came back with no download URL")
        existing[name] = asset
        links[f.path] = str(url)
    return links


# --- The plan and the post ---------------------------------------------------------------------


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def format_plan(plan: Plan, *, publish: bool, base_url: str | None) -> str:
    """The plan printed before any write."""
    t = plan.target
    lines = [
        f"Target: {t.slug}#{t.number} (repository visibility: {plan.visibility})",
        f"Sink: {plan.sink}",
    ]
    if plan.sink == "none":
        lines.append(
            f"Evidence links: {base_url}" if base_url else "Evidence links: none (paths only)"
        )
    else:
        lines += _release_plan_lines(plan, publish=publish)
        lines.append("Files:")
        lines += [f"  {f.path}  {_mb(f.size)}" for f in plan.files]
        lines.append(f"Total: {_mb(sum(f.size for f in plan.files))}")
    lines += [f"Note: {n}" for n in plan.notes]
    return "\n".join(lines)


def _release_plan_lines(plan: Plan, *, publish: bool) -> list[str]:
    release = plan.release
    if not plan.remote_checked:
        lines = [
            "Remote state not read (no token): release reuse, repository visibility, and the "
            "workflows a tag would fire are unknown."
        ]
        if publish:
            lines.append(f"  Publishing would create tag {plan.tag!r}.")
        return lines
    if release is None:
        state = "published" if publish else "draft"
        lines = [f"Release: create {plan.tag!r} as a {state} prerelease"]
    else:
        state = "draft" if release.get("draft") else "published"
        lines = [f"Release: reuse {release.get('html_url', plan.tag)} ({state})"]
        if publish and release.get("draft"):
            names = [str(a.get("name")) for a in _dicts(release.get("assets"))]
            lines.append(
                "  --publish makes these existing assets public too: "
                + (", ".join(names) or "none")
            )
    creates_tag = publish and (release is None or bool(release.get("draft")))
    if creates_tag:
        lines.append(f"  Publishing creates tag {plan.tag!r}; workflows that may fire on it:")
        lines += [f"    {w}" for w in plan.workflows]
        if plan.workflows_error:
            lines.append(f"    could not list every workflow: {plan.workflows_error}")
        elif not plan.workflows:
            lines.append("    none found")
    elif release is not None and not release.get("draft"):
        lines.append(
            "  This release is published: its assets are public to everyone who can see the repository."
        )
    elif not publish:
        lines.append("  A draft release's links open only for people with write access.")
    return lines


def confirm(stdin: TextIO, out: TextIO) -> bool:
    """Ask the developer to confirm; a non-interactive stdin is a refusal, never a yes."""
    if not stdin.isatty():
        print("stdin is not a terminal; pass --yes to post without asking.", file=out)
        return False
    print("Post this? [y/N] ", end="", file=out, flush=True)
    return stdin.readline().strip().lower() in {"y", "yes"}


def build_parser() -> argparse.ArgumentParser:
    """The command-line interface."""
    p = argparse.ArgumentParser(
        prog="post_evidence.py",
        description="Post a finished Bajutsu run's evidence to a pull request or an issue.",
    )
    p.add_argument("run", help="a run directory, or a run id under --runs")
    p.add_argument("--to", required=True, help="owner/repo#NUMBER, or a pull request or issue URL")
    p.add_argument("--runs", default="runs", help="where a run id is looked up (default: runs)")
    p.add_argument("--sink", choices=("none", "release"), default="none")
    p.add_argument("--publish", action="store_true", help="publish the release (release sink)")
    p.add_argument("--evidence-base-url", help="where a hosted copy of the run directory lives")
    p.add_argument("--key", default=DEFAULT_KEY, help="the sticky comment's key (default: local)")
    p.add_argument("--author", help="the comment author's login, for a token with no user")
    p.add_argument("--max-failures", type=int, default=10)
    p.add_argument("--log-tail-kb", type=int, default=256)
    p.add_argument("--size-warn-mb", type=int, default=100)
    p.add_argument("--dry-run", action="store_true", help="print the plan and the comment only")
    p.add_argument("--yes", action="store_true", help="post without asking")
    return p


@dataclass(frozen=True)
class Io:
    """The streams and environment `main` uses, injectable for tests."""

    env: Mapping[str, str]
    stdin: TextIO
    out: TextIO
    err: TextIO
    run_gh: Callable[[], str | None] | None = None


def _base_links(run: RunView, base_url: str | None, max_failures: int) -> dict[str, str]:
    if not base_url:
        return {}
    return {
        p: evidence_url(base_url, p)
        for f in run.failures[: max(max_failures, 0)]
        for p in (*f.screenshots, *f.videos)
    }


def _post(args: argparse.Namespace, io: Io, staging: Path) -> str | None:
    target = parse_target(args.to)
    key = check_key(args.key)
    run_dir = resolve_run_dir(args.run, Path(args.runs))
    run = load_run(run_dir)
    check_host(target, io.env.get(API_URL_ENV) or DEFAULT_API_URL)
    plan = Plan(target=target, sink=args.sink)
    if args.sink == "release":
        try:
            plan.files, plan.notes = stage(
                run_dir,
                run,
                staging,
                max_failures=args.max_failures,
                log_tail_kb=args.log_tail_kb,
                size_warn_mb=args.size_warn_mb,
            )
        except OSError as e:
            raise PostError(f"cannot stage evidence from {run_dir}: {e}") from e
    plan.notes = [*run.notes, *plan.notes]
    token = resolve_token(io.env, io.run_gh)
    gh = GitHub(token or "", io.env.get(API_URL_ENV) or DEFAULT_API_URL)
    if token is None and not args.dry_run:
        raise PostError("no token: set GITHUB_TOKEN or GH_TOKEN, or sign in with `gh auth login`")
    if token is not None:
        _read_remote_state(gh, plan, publish=args.publish)
    print(format_plan(plan, publish=args.publish, base_url=args.evidence_base_url), file=io.out)
    links = _base_links(run, args.evidence_base_url, args.max_failures)
    if args.dry_run:
        body = render(run, key=key, links=links, max_failures=args.max_failures, notes=plan.notes)
        print("\n--- comment (dry run: nothing was written) ---\n" + body, file=io.out)
        return None
    if not args.yes and not confirm(io.stdin, io.out):
        raise PostError("not confirmed; nothing was posted")
    author = args.author or gh.login()
    run_link = None
    if args.sink == "release":
        links = upload_assets(gh, target, plan, run.run_id, args.publish)
        run_link = links.pop(f"{run.run_id}.zip", None)
    body = render(
        run,
        key=key,
        links=links,
        max_failures=args.max_failures,
        notes=plan.notes,
        run_link=run_link,
    )
    return upsert_comment(gh, target, key, author, body)


def _read_remote_state(gh: GitHub, plan: Plan, *, publish: bool) -> None:
    repo = gh.request("GET", f"/repos/{plan.target.slug}")
    if isinstance(repo, dict):
        plan.visibility = str(
            repo.get("visibility") or ("private" if repo.get("private") else "public")
        )
    if plan.sink == "release":
        plan.release = find_release(gh, plan.target)
        if publish:
            plan.workflows, plan.workflows_error = tag_workflows(gh, plan.target)
    plan.remote_checked = True


def main(argv: Sequence[str] | None = None, io: Io | None = None) -> int:
    """Run the script; the exit status says whether the post succeeded, never the run's verdict."""
    io = io or Io(env=os.environ, stdin=sys.stdin, out=sys.stdout, err=sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        check_key(args.key)
        parse_target(args.to)
    except PostError as e:
        print(f"post_evidence: {e}", file=io.err)
        return EXIT_USAGE
    try:
        with tempfile.TemporaryDirectory(prefix="bajutsu-evidence-") as tmp:
            url = _post(args, io, Path(tmp))
    except (PostError, OSError) as e:
        print(f"post_evidence: {e}", file=io.err)
        return EXIT_POST_FAILED
    if url:
        print(f"Posted: {url}", file=io.out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
