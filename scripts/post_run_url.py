"""Post a ``serve`` run's URL to a pull request, an issue, or a release (BE-0508).

The run is already on ``serve``; this script only tells reviewers where it is. It reads the run's
``manifest.json`` (or each ``<sid>/result.json`` when the run left no manifest) and writes one sticky
comment, or one delimited block in a release's notes, holding the verdict, each failure, and the
link. Running it again edits the same comment or block.

It uses the standard library alone and imports nothing from ``bajutsu``, so a team can copy this one
file out of the repository (which is why it does not reuse ``scripts/gh_cli.py``; see the BE-0508
alternatives). It runs after the verdict and never changes it: the exit status reports the post,
not the run.

Exit status: ``0`` posted (or ``--dry-run`` finished), ``1`` nothing was posted (an API or network
error, a missing release, a declined confirmation), ``2`` invalid arguments.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Every request goes here and nowhere else, so a pasted `--to` URL can never carry the token to
# another host. Tests point it at a local fake server.
API_BASE = "https://api.github.com"
WEB_PREFIX = "https://github.com/"

MAX_TEXT_CHARS = 500
# GitHub rejects a comment over 65,536 characters and a release body over 125,000; these leave room
# for the truncation note.
MAX_COMMENT_CHARS = 60_000
MAX_RELEASE_CHARS = 120_000
DEFAULT_MAX_FAILURES = 10

# A `serve` API key starts with this prefix; a marker is published in a hidden comment on a thread
# that may be public, so a confused user must not be able to put a key there.
_SECRET_PREFIX = "bajutsu_"  # noqa: S105 - a prefix to refuse, not a credential
# Capped so the two marker lines always fit in the room the length limits leave.
_MARKER_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
_REPO = r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/(?P<repo>[A-Za-z0-9._-]+)"
_TO_SHORT = re.compile(_REPO + r"#(?P<number>[0-9]+)")
_TO_URL = re.compile(
    re.escape(WEB_PREFIX) + _REPO + r"/(?:pull|issues)/(?P<number>[0-9]+)(?:[/?#].*)?"
)
_TO_RELEASE = re.compile(r"release:" + _REPO + r"@(?P<tag>[^\s]+)")
# Characters that would end or break the Markdown link the URL goes into.
_BAD_URL_CHARS = re.compile(r"[\s<>)]")


class UsageError(Exception):
    """An argument the script refuses; exit status 2."""


class PostError(Exception):
    """The post could not be made; exit status 1."""


# ---------------------------------------------------------------------------------------------
# The command: targets, URLs, and the run


@dataclass(frozen=True)
class Target:
    """Where the text goes: a pull request or issue thread, or a release's notes."""

    owner: str
    repo: str
    number: int | None = None
    tag: str | None = None

    @property
    def is_release(self) -> bool:
        """Whether the target is a release rather than a comment thread."""
        return self.tag is not None

    def describe(self) -> str:
        """The target as the plan prints it."""
        if self.tag is not None:
            return f"release {self.tag} of {self.owner}/{self.repo}"
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_target(value: str) -> Target:
    """Parse ``--to``: ``owner/repo#N``, a github.com pull request or issue URL, or a release."""
    value = value.strip()
    if re.fullmatch(r"#?[0-9]+", value):
        # In a fork workflow `origin` is the fork, so a guessed repository posts to the wrong thread.
        raise UsageError(f"--to {value!r}: name the repository, as owner/repo#NUMBER")
    m = _TO_RELEASE.fullmatch(value) or _TO_SHORT.fullmatch(value) or _TO_URL.fullmatch(value)
    if m is not None and m["repo"] in (".", ".."):
        # GitHub forbids these names, and `..` would normalize the API path onto another repository.
        raise UsageError(f"--to {value!r}: {m['repo']!r} is not a repository name")
    if (m := _TO_RELEASE.fullmatch(value)) is not None:
        return Target(m["owner"], m["repo"], tag=m["tag"])
    if (m := _TO_SHORT.fullmatch(value)) is not None or (m := _TO_URL.fullmatch(value)) is not None:
        number = int(m["number"])
        if number < 1:
            raise UsageError(f"--to {value!r}: the number must be positive")
        return Target(m["owner"], m["repo"], number=number)
    if "://" in value and not value.startswith(WEB_PREFIX):
        raise UsageError(f"--to {value!r}: only {WEB_PREFIX} URLs are accepted")
    raise UsageError(
        f"--to {value!r}: expected owner/repo#NUMBER, a {WEB_PREFIX} pull request or issue URL,"
        " or release:owner/repo@TAG"
    )


def check_link(value: str, flag: str) -> urllib.parse.SplitResult:
    """Refuse a URL that could break the comment's Markdown or publish a credential."""
    if _BAD_URL_CHARS.search(value):
        raise UsageError(f"{flag}: the URL holds whitespace, '<', '>', or ')'")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UsageError(f"{flag}: the URL must be http or https with a host")
    # Userinfo and a query string are where a pasted credential or signature lives; the thread may
    # be public, so neither is ever published.
    if "@" in parts.netloc:
        raise UsageError(f"{flag}: the URL carries user information (user:password@)")
    # A bare trailing `?` leaves `parts.query` empty, so test the raw value too.
    if parts.query or "?" in value:
        raise UsageError(f"{flag}: the URL carries a query string")
    return parts


def check_run_url(value: str, run_id: str) -> str:
    """Refuse a ``--url`` that is not this run's, so a verdict never links another run."""
    parts = check_link(value, "--url")
    if f"/runs/{run_id}/" not in parts.path:
        raise UsageError(f"--url: the path does not contain /runs/{run_id}/")
    return value


def check_marker(name: str) -> str:
    """Validate ``--marker``: a whole-value match of the allowed characters, never a key prefix."""
    if not _MARKER_NAME.fullmatch(name):
        raise UsageError("--marker: use 1 to 64 letters, digits, '.', '_', and '-'")
    if name.lower().startswith(_SECRET_PREFIX):
        raise UsageError(
            f"--marker: a value starting with {_SECRET_PREFIX!r} looks like an API key"
        )
    return name


@dataclass
class Scenario:
    """One scenario as the renderer needs it, whichever file it came from."""

    name: str
    ok: bool
    failure: str | None
    steps: list[dict[str, Any]]

    def failed_step(self) -> dict[str, Any] | None:
        """The first step that did not pass, or ``None`` when only the scenario failed."""
        return next((s for s in self.steps if s.get("ok") is False), None)


@dataclass
class Run:
    """The run the comment reports: its id, whether it completed, and its scenarios."""

    run_id: str
    complete: bool
    manifest_ok: bool | None
    scenarios: list[Scenario] = field(default_factory=list)
    unreadable: int = 0  # `result.json` files skipped because they did not parse
    not_applicable: int = 0  # runs no available device fell in a `runsOn` range for (BE-0452)

    @property
    def failed(self) -> list[Scenario]:
        """The scenarios that did not pass, in run order."""
        return [s for s in self.scenarios if not s.ok]

    @property
    def verdict(self) -> str:
        """``passed``, ``failed``, or ``incomplete`` when the run left no manifest."""
        if not self.complete:
            return "incomplete"
        return "failed" if self.failed or self.manifest_ok is False else "passed"


def _read_json(path: Path) -> dict[str, Any]:
    """A JSON object from ``path``; ``UsageError`` when it cannot be read or is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"{path}: unreadable ({exc})") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path}: not a JSON object")
    return data


def _scenario(raw: dict[str, Any]) -> Scenario:
    failure = raw.get("failure")
    return Scenario(
        name=str(raw.get("scenario") or raw.get("sid") or "(unnamed)"),
        ok=raw.get("ok") is True,
        failure=str(failure) if failure else None,
        steps=[s for s in raw.get("steps") or [] if isinstance(s, dict)],
    )


def resolve_run_dir(value: str, runs: Path) -> Path:
    """A run directory as given, or a run id looked up under ``--runs-dir``."""
    path = Path(value)
    if path.is_dir():
        return path
    if "/" in value or "\\" in value or value in ("", ".", ".."):
        raise UsageError(f"{value!r} is not a run directory")
    candidate = runs / value
    if not candidate.is_dir():
        raise UsageError(f"no run {value!r} under {runs}")
    return candidate


def read_run(path: Path) -> Run:
    """Read the manifest, or each ``<sid>/result.json`` when the run left none."""
    # The directory name is the run id `serve` keys the run by, and the one `--url` must name.
    run_id = path.resolve().name
    # A manifest that exists but does not parse is an error, never "the run did not complete": the
    # comment would otherwise publish a false verdict.
    if (path / "manifest.json").exists():
        manifest = _read_json(path / "manifest.json")
        ok, entries = manifest.get("ok"), manifest.get("scenarios")
        if not isinstance(ok, bool) or not isinstance(entries, list):
            raise UsageError(f"{path}/manifest.json: needs a boolean `ok` and a list `scenarios`")
        if not all(isinstance(e, dict) for e in entries):
            raise UsageError(f"{path}/manifest.json: a `scenarios` entry is not an object")
        skipped = manifest.get("notApplicable")
        return Run(
            run_id,
            True,
            ok,
            [_scenario(e) for e in entries],
            not_applicable=len(skipped) if isinstance(skipped, list) else 0,
        )
    # A run killed midway can leave a half-written `result.json`, so a broken one is skipped and
    # counted rather than refused; the comment says how many. Each holds the manifest's scenario
    # entry under `scenario`, beside `schemaVersion`.
    results: list[dict[str, Any]] = []
    unreadable = 0
    for p in sorted(path.glob("*/result.json")):
        try:
            entry = _read_json(p).get("scenario")
        except UsageError as exc:
            entry = None
            print(f"warning: {exc}", file=sys.stderr)
        if isinstance(entry, dict):
            results.append(entry)
        else:
            unreadable += 1
    if not results and not unreadable:
        raise UsageError(f"{path} holds no manifest.json and no <sid>/result.json")
    return Run(run_id, False, None, [_scenario(r) for r in results], unreadable=unreadable)


# ---------------------------------------------------------------------------------------------
# The renderer


def marker(name: str, *, closing: bool = False) -> str:
    """The hidden marker line that identifies this script's comment or release block."""
    return f"<!-- {'/' if closing else ''}bajutsu-run:{name} -->"


def is_marker_line(line: str, name: str, *, closing: bool = False) -> bool:
    """Whether a line is the marker, ignoring trailing whitespace and a carriage return."""
    return line.rstrip() == marker(name, closing=closing)


def _strip_comment_delimiters(text: str) -> str:
    # Repeat until none remain: removing one `<!--` can join `<!` and `--` into a new one.
    previous = None
    while previous != text:
        previous = text
        text = text.replace("<!--", "").replace("-->", "")
    return text


def code(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Wrap untrusted run text in a code span, or a fenced block when it spans lines.

    The delimiter is one backtick longer than the longest run inside, so the text cannot close it,
    and no mention, HTML, or second marker can escape into the comment.
    """
    text = _strip_comment_delimiters(text)
    if len(text) > limit:
        text = text[:limit] + "…"
    if not text.strip():
        text = "(empty)"
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    if "\n" in text or "\r" in text:
        fence = "`" * max(3, longest + 1)
        return f"\n{fence}\n{text}\n{fence}\n"
    delim = "`" * (longest + 1)
    # CommonMark would read a backtick at either edge as part of the delimiter.
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{delim}{pad}{text}{pad}{delim}"


def _failure_block(scenario: Scenario) -> str:
    lines = [f"#### {code(scenario.name)}"]
    step = scenario.failed_step()
    if step is not None:
        action = code(str(step.get("action") or "?"))
        reason = code(str(step.get("reason") or "no reason recorded"))
        lines.append(f"Failed at step {code(str(step.get('index', '?')))} {action}: {reason}")
    else:
        lines.append("Failed: " + code(scenario.failure or "no reason recorded"))
    return "\n".join(lines)


def render(
    run: Run,
    url: str,
    *,
    archive_url: str | None = None,
    max_failures: int = DEFAULT_MAX_FAILURES,
    limit: int = MAX_COMMENT_CHARS,
) -> str:
    """Render the text: the verdict and counts, the failure blocks, and the run's link.

    Failure blocks are dropped whole, never cut mid-span, when the body would pass ``limit``; a cut
    inside a code span would let the rest of the run's text render as Markdown.
    """
    failed = run.failed
    passed = len(run.scenarios) - len(failed)
    head = [f"### Bajutsu run {run.verdict}: {passed} passed, {len(failed)} failed", ""]
    head.append(f"Run: [{code(run.run_id)}]({url})")
    if archive_url:
        head.append(f"Archive: [the run as a zip]({archive_url})")
    if not run.complete:
        head += [
            "",
            "> **The run did not complete** (no `manifest.json`). Finished scenarios are listed.",
        ]
    if run.not_applicable:
        head += [
            "",
            f"> {run.not_applicable} run(s) were not applicable and did not execute: no available"
            " device fell in the `runsOn` range.",
        ]
    if run.complete and run.manifest_ok is False and not failed:
        head += ["", "> No scenario failed, but `manifest.json` records that the run did not pass."]
    if run.unreadable:
        head.append(
            f"> {run.unreadable} `result.json` file(s) could not be read and are not counted."
        )
    body = "\n".join(head)
    shown = 0
    truncated = False
    for scenario in failed[:max_failures]:
        block = "\n\n" + _failure_block(scenario)
        # Reserve room for the closing note so adding it never crosses the limit.
        if len(body) + len(block) > limit - 200:
            truncated = True
            break
        body += block
        shown += 1
    omitted = len(failed) - shown
    if omitted:
        body += f"\n\n…and {omitted} more failed scenario(s) not listed."
        if truncated:
            body += " The text was cut to fit GitHub's size limit."
    return body.rstrip() + "\n"


def render_comment(run: Run, url: str, name: str, **kwargs: Any) -> str:
    """The comment body: the marker line first, then the rendered text."""
    return marker(name) + "\n" + render(run, url, **kwargs)


def _marker_spans(body: str, name: str) -> tuple[int, int] | None:
    """Character offsets of an existing release block, markers included."""
    lines = body.splitlines(keepends=True)
    offset = 0
    start: int | None = None
    for line in lines:
        if start is None and is_marker_line(line, name):
            start = offset
        elif start is not None and is_marker_line(line, name, closing=True):
            return start, offset + len(line.rstrip("\r\n"))
        offset += len(line)
    if start is not None:
        raise PostError(
            f"the release notes open {marker(name)} without {marker(name, closing=True)};"
            " fix the notes by hand"
        )
    return None


def release_body(existing: str, block_text: str, name: str) -> str:
    """Replace this marker's block in the release notes, or append it at the end."""
    block = f"{marker(name)}\n{block_text.rstrip()}\n{marker(name, closing=True)}"
    span = _marker_spans(existing, name)
    if span is not None:
        return existing[: span[0]] + block + existing[span[1] :]
    kept = existing.rstrip()
    return f"{kept}\n\n{block}\n" if kept else block + "\n"


def release_text_limit(existing: str, name: str) -> int:
    """Room left for the block once the notes' other text is counted."""
    span = _marker_spans(existing, name)
    other = len(existing) - (span[1] - span[0] if span else 0)
    room = MAX_RELEASE_CHARS - other
    if room < 1_000:
        raise PostError("the release notes are too long to hold the run's block")
    return room


# ---------------------------------------------------------------------------------------------
# The GitHub client


def read_token() -> str:
    """``GH_TOKEN``, then ``GITHUB_TOKEN`` (the order ``gh`` uses), then ``gh auth token``."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    try:
        out = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PostError("no GH_TOKEN or GITHUB_TOKEN, and `gh auth token` could not run") from exc
    token = out.stdout.strip()
    if out.returncode != 0 or not token:
        why = f": {out.stderr.strip()}" if out.stderr.strip() else ""
        raise PostError(f"no GH_TOKEN or GITHUB_TOKEN, and `gh auth token` returned none{why}")
    return token


class GitHub:
    """A minimal REST client that only ever talks to ``API_BASE``."""

    def __init__(self, token: str) -> None:
        """Hold the token for the session; it is never written to disk or printed."""
        self._token = token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """Send one request and decode its JSON reply."""
        url = API_BASE + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - API_BASE only
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "bajutsu-post-run-url")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # Unredirected: urllib drops it on a redirect, so the token never follows one to another host.
        req.add_unredirected_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - API_BASE only
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise PostError(f"{method} {path}: HTTP {exc.code} {_api_message(exc)}") from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            raise PostError(f"{method} {path}: {exc}") from exc
        try:
            return json.loads(raw.decode() or "null")
        except ValueError as exc:
            if method != "GET":
                # The write already happened; an unparsable reply must not report "not posted".
                return None
            raise PostError(f"{method} {path}: the reply is not JSON") from exc

    def paged(self, path: str) -> list[dict[str, Any]]:
        """Every item of a listing endpoint, page by page."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{sep}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise PostError(f"GET {path}: expected a list")
            items += [b for b in batch if isinstance(b, dict)]
            if len(batch) < 100:
                return items
            page += 1

    def login(self) -> str:
        """The login of the token's user."""
        try:
            user = self.request("GET", "/user")
        except PostError as exc:
            # An installation token (Actions' GITHUB_TOKEN) has no user and gets 403 here.
            cause = exc.__cause__
            if isinstance(cause, urllib.error.HTTPError) and cause.code in (401, 403):
                raise PostError(
                    f"{exc}; for a token with no user, name the owner with --author"
                ) from exc
            raise
        login = user.get("login") if isinstance(user, dict) else None
        if not isinstance(login, str) or not login:
            raise PostError("the token has no user; name the comment's owner with --author")
        return login


def _api_message(exc: urllib.error.HTTPError) -> str:
    # GitHub's JSON `message` ("Bad credentials", "Resource not accessible by integration") says
    # why far better than the bare status reason.
    try:
        message = json.loads(exc.read().decode() or "null").get("message")
    except (OSError, http.client.HTTPException, ValueError, AttributeError):
        message = None
    return str(message) if message else str(exc.reason)


def _record_id(record: dict[str, Any], kind: str) -> int:
    value = record.get("id")
    if not isinstance(value, int) or isinstance(value, bool):
        raise PostError(f"unexpected {kind} record from GitHub: no numeric id")
    return value


def _repo_path(target: Target) -> str:
    return f"/repos/{target.owner}/{target.repo}"


def find_comment(gh: GitHub, target: Target, name: str, author: str) -> int | None:
    """The id of this script's comment on the thread: the marker first, posted by ``author``."""
    path = f"{_repo_path(target)}/issues/{target.number}/comments"
    for comment in gh.paged(path):
        first = (comment.get("body") or "").split("\n", 1)[0]
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        # GitHub logins are case-insensitive; a hand-typed `--author` in another case must still
        # match, or every rerun creates a second comment.
        same = isinstance(login, str) and login.lower() == author.lower()
        if same and is_marker_line(first, name):
            return _record_id(comment, "comment")
    return None


def find_release(gh: GitHub, target: Target) -> dict[str, Any]:
    """The release whose tag matches exactly; the listing, unlike the tag endpoint, holds drafts."""
    for release in gh.paged(f"{_repo_path(target)}/releases"):
        if release.get("tag_name") == target.tag:
            return release
    raise PostError(f"no release with tag {target.tag!r} in {target.owner}/{target.repo}")


# ---------------------------------------------------------------------------------------------
# The plan and the guards


@dataclass
class Plan:
    """What the script is about to write, printed before it writes."""

    target: Target
    action: str
    text: str
    comment_id: int | None = None
    release_id: int | None = None
    release_notes: str | None = None


def build_plan(gh: GitHub | None, args: argparse.Namespace, run: Run, target: Target) -> Plan:
    """Look up what exists and decide what to create or edit. ``gh`` is ``None`` offline."""
    kwargs: dict[str, Any] = {"archive_url": args.archive_url, "max_failures": args.max_failures}
    if target.is_release:
        if gh is None:
            text = render(run, args.url, limit=MAX_RELEASE_CHARS, **kwargs)
            return Plan(target, "write the block (release not looked up: no token)", text)
        release = find_release(gh, target)
        notes = str(release.get("body") or "")
        text = render(run, args.url, limit=release_text_limit(notes, args.marker), **kwargs)
        exists = _marker_spans(notes, args.marker) is not None
        action = "replace the block in the notes" if exists else "append a block to the notes"
        new_notes = release_body(notes, text, args.marker)
        return Plan(
            target, action, text, release_id=_record_id(release, "release"), release_notes=new_notes
        )
    text = render_comment(run, args.url, args.marker, **kwargs)
    if gh is None:
        return Plan(target, "create or edit a comment (not looked up: no token)", text)
    author = args.author or gh.login()
    comment_id = find_comment(gh, target, args.marker, author)
    if comment_id is None:
        return Plan(target, "create a comment", text)
    return Plan(target, f"edit comment {comment_id}", text, comment_id=comment_id)


def execute(gh: GitHub, plan: Plan) -> str:
    """Make the write the plan describes, and return the URL GitHub reports."""
    target = plan.target
    if plan.release_id is not None:
        path = f"{_repo_path(target)}/releases/{plan.release_id}"
        result = gh.request("PATCH", path, {"body": plan.release_notes})
    elif plan.comment_id is not None:
        path = f"{_repo_path(target)}/issues/comments/{plan.comment_id}"
        result = gh.request("PATCH", path, {"body": plan.text})
    else:
        path = f"{_repo_path(target)}/issues/{target.number}/comments"
        result = gh.request("POST", path, {"body": plan.text})
    return str(result.get("html_url") or "") if isinstance(result, dict) else ""


def print_plan(plan: Plan) -> None:
    """Show the target, the action, and the exact text before anything is written."""
    print(f"Target: {plan.target.describe()}")
    print(f"Action: {plan.action}")
    print("Text:")
    print("-" * 72)
    print(plan.text.rstrip())
    print("-" * 72)


def confirm() -> bool:
    """Ask before writing; a non-interactive caller must pass ``--yes``."""
    if not sys.stdin.isatty():
        print("stdin is not a terminal; pass --yes to post without asking", file=sys.stderr)
        return False
    try:
        answer = input("Post this? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(
        prog="post_run_url.py",
        description="Post a serve run's URL to a pull request, an issue, or a release.",
    )
    p.add_argument("run", help="a run directory, or a run id under --runs-dir")
    p.add_argument("--url", required=True, help="the run's URL on serve")
    p.add_argument(
        "--to",
        required=True,
        help="owner/repo#NUMBER, a pull request or issue URL, or release:owner/repo@TAG",
    )
    p.add_argument("--archive-url", help="a URL of the run's zip, linked on its own line")
    p.add_argument("--runs-dir", default="runs", help="where a run id is looked up (default: runs)")
    p.add_argument("--marker", default="local", help="the marker slot (default: local)")
    p.add_argument("--author", help="the comment's owner, for a token with no user")
    p.add_argument(
        "--max-failures",
        type=int,
        default=DEFAULT_MAX_FAILURES,
        help="failure blocks listed (default: 10)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan and the text; write nothing"
    )
    p.add_argument("--yes", action="store_true", help="post without asking")
    return p


def validate(args: argparse.Namespace) -> tuple[Target, Run]:
    """Check every argument and read the run, before any token is read."""
    target = parse_target(args.to)
    check_marker(args.marker)
    if args.max_failures < 0:
        raise UsageError("--max-failures must be zero or more")
    run = read_run(resolve_run_dir(args.run, Path(args.runs_dir)))
    check_run_url(args.url, run.run_id)
    if args.archive_url:
        check_link(args.archive_url, "--archive-url")
    return target, run


def main(argv: list[str] | None = None) -> int:
    """Validate everything, render, print the plan, confirm, and post."""
    args = build_parser().parse_args(argv)
    try:
        target, run = validate(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        return post(args, target, run)
    except PostError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def post(args: argparse.Namespace, target: Target, run: Run) -> int:
    """Read the token, plan, confirm, and write; ``PostError`` reports why nothing was posted."""
    # The token is read only after every argument passed, so a refused `--to` never reads it. A dry
    # run without one still prints the text, offline.
    gh: GitHub | None
    try:
        gh = GitHub(read_token())
    except PostError as exc:
        if not args.dry_run:
            raise
        print(f"note: {exc}; planning offline", file=sys.stderr)
        gh = None
    plan = build_plan(gh, args, run, target)
    print_plan(plan)
    if args.dry_run or gh is None:
        print("Dry run: nothing was written.")
        return 0
    if not args.yes and not confirm():
        print("Not posted.", file=sys.stderr)
        return 1
    url = execute(gh, plan)
    print(f"Posted: {url}" if url else "Posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
