"""Acquire a config (and its scenario tree) from a Git source (BE-0063).

`--config` keeps accepting a local path; in addition it accepts a Git spec
(`github:<owner>/<repo>[@<ref>][:<path>]`, or `git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]`).
The spec is materialized at an immutable commit SHA into a content-addressed cache, and the config's
relative paths resolve against that checkout root. Only *acquisition* changes — the schema, runner,
drivers, and the deterministic gate are untouched (DESIGN §6.5: git holds the history).

The GitHub transport (commits API + tarball endpoint) is the one external dependency; it is a small
injectable seam so the materialization logic tests offline against a fake.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Protocol

DEFAULT_CONFIG = "bajutsu.config.yaml"

# The env var serve materializes a UI-entered Git credential into (BE-0224). Deliberately *not*
# `GITHUB_TOKEN`: an operator commonly exports that themselves, so aliasing it would let the UI's
# "clear" pop their ambient token and let its "is set?" read report a token the UI never stored.
# This bajutsu-owned var is checked first, so a UI credential wins over an ambient one, and clearing
# it never touches the operator's `GITHUB_TOKEN` / `GH_TOKEN`.
GIT_CONFIG_TOKEN_ENV = "BAJUTSU_GIT_CONFIG_TOKEN"  # noqa: S105 — an env var name, not a secret

# owner/repo constrained to GitHub's real charset (BE-0124): an owner is alphanumeric + hyphen (a
# username/org — no dot, so it can never be a `.`/`..` traversal token), a repo also allows `_`/`.`
# but a bare `.`/`..` segment is rejected in `parse_config_spec`. Neither admits `%`, so a
# percent-encoded segment simply fails to match — it never reaches the API URL or the cache path.
# These are single-character classes; each regex applies its own quantifier (`+`, or `+?` for the
# git-url repo so a trailing `.git` is stripped rather than folded into the name).
_OWNER = r"[A-Za-z0-9-]"
_REPO = r"[A-Za-z0-9._-]"
# `github:owner/repo[@ref][:path]` — the headline shorthand.
_GITHUB_RE = re.compile(
    rf"^github:(?P<owner>{_OWNER}+)/(?P<repo>{_REPO}+)(?:@(?P<ref>[^:]+))?(?::(?P<path>.+))?$"
)
# `git+https://host/owner/repo(.git)[@ref][#path]` — the general form (any Git host).
_GIT_URL_RE = re.compile(
    rf"^git\+https://(?P<host>[^/]+)/(?P<owner>{_OWNER}+)/(?P<repo>{_REPO}+?)(?:\.git)?(?:@(?P<ref>[^#]+))?(?:#(?P<path>.+))?$"
)
# A full 40-hex commit SHA — already immutable, so it needs no commits-API resolution.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_full_sha(ref: str | None) -> bool:
    """Whether `ref` is a full 40-hex commit SHA — the only ref that is an immutable, offline pin."""
    return bool(ref and _FULL_SHA_RE.match(ref))


@dataclass(frozen=True)
class GitConfigSpec:
    """A parsed Git config source: which repo subtree, at which ref, to load the config from."""

    host: str
    owner: str
    repo: str
    ref: str | None  # branch / tag / SHA; None = the repo's default branch
    path: str | None  # config path within the repo; None = DEFAULT_CONFIG at the root


@dataclass(frozen=True)
class Materialized:
    """A repo subtree checked out at an immutable SHA: the config file, the checkout root, and the SHA."""

    config_path: Path
    root: Path
    sha: str  # the resolved commit SHA (the determinism anchor / cache key)


def parse_config_spec(value: str) -> GitConfigSpec | None:
    """Parse a `--config` value into a `GitConfigSpec`, or None when it is an ordinary local path.

    A value with no recognized Git scheme is a local path, so every existing invocation is unchanged.
    A repo of exactly `.` or `..` is a same-segment traversal (BE-0124) and is refused here, so it
    fails the same way an unparseable value does rather than reaching the cache path.
    """
    if m := _GITHUB_RE.match(value):
        return _spec("github.com", m) if m["repo"] not in (".", "..") else None
    if m := _GIT_URL_RE.match(value):
        return _spec(m["host"], m) if m["repo"] not in (".", "..") else None
    return None


def _spec(host: str, m: re.Match[str]) -> GitConfigSpec:
    return GitConfigSpec(host, m["owner"], m["repo"], m["ref"], m["path"])


def source_from_config(config: str) -> dict[str, object]:
    """A config-source record (`kind` + `locator`) for *config* (BE-0225).

    A Git spec becomes a `git` source, anything else a local `file` source — the discriminated shape
    the project registry stores and `serve` auto-registers (`launch_project_identity`), so a config
    registered from the CLI or the API round-trips back through the run/bind path.
    """
    spec = parse_config_spec(config)
    if spec is None:
        return {"kind": "file", "locator": {"path": str(config)}}
    locator: dict[str, str] = {"host": spec.host, "owner": spec.owner, "repo": spec.repo}
    if spec.ref:
        locator["ref"] = spec.ref
    if spec.path:
        locator["path"] = spec.path
    return {"kind": "git", "locator": locator}


def config_from_source(source: object) -> str:
    """Reconstruct a `--config` spec from a stored config-source record (BE-0225).

    The inverse of `source_from_config`, so a registered project drives the ordinary run/bind path. A
    `git` source rebuilds the `github:` / `git+https://` spec, preferring the resolved `sha` (an
    immutable pin the launch auto-register stamps) over a moving `ref`. A `file` source is its path.

    Raises:
        ValueError: the record is malformed (not a record, no locator, a `file` with no path, a `git`
            missing a required field) or names a source kind with no spec form — an `upload` bundle
            has no local checkout to point `--config` at.
    """
    if not isinstance(source, dict):
        raise ValueError(f"config source is not a record: {source!r}")
    kind = source.get("kind")
    locator = source.get("locator")
    if not isinstance(locator, dict):
        raise ValueError(f"config source has no locator: {source!r}")
    if kind == "file":
        path = locator.get("path")
        if path is None:
            raise ValueError(f"config source has no path: {source!r}")
        return str(path)
    if kind == "git":
        return _git_spec(locator)
    raise ValueError(f"cannot bind a {kind!r} config source (only git or file)")


def _git_spec(locator: dict[str, object]) -> str:
    """A `github:` / `git+https://` spec from a git locator, pinning `sha` when present."""
    missing = [k for k in ("host", "owner", "repo") if k not in locator]
    if missing:
        raise ValueError(f"git config source locator is missing {missing}: {locator!r}")
    host, owner, repo = locator["host"], locator["owner"], locator["repo"]
    ref = locator.get("sha") or locator.get("ref")
    path = locator.get("path")
    if host == "github.com":
        spec = f"github:{owner}/{repo}"
        if ref:
            spec += f"@{ref}"
        if path:
            spec += f":{path}"
        return spec
    spec = f"git+https://{host}/{owner}/{repo}"
    if ref:
        spec += f"@{ref}"
    if path:
        spec += f"#{path}"
    return spec


class Transport(Protocol):
    """The Git-host calls materialization makes — injected so the logic tests offline."""

    def commit_sha(self, spec: GitConfigSpec, ref: str) -> str: ...

    def tarball_bytes(self, spec: GitConfigSpec, sha: str) -> bytes: ...


class GitHubAccessError(ValueError):
    """A private-repo credential could not be acquired, or a GitHub API call was rejected (BE-0224).

    Covers both a rejected API call (auth / rate-limit / SSO / not-found) and a failure to *mint* a
    credential (a broken GitHub App config, a missing key file, the `githubapp` extra absent). A
    `ValueError` so callers already catching fetch failures (`serve.bind_git_config`, the `--config`
    diagnostics) surface its message unchanged instead of a raw traceback.
    """


def github_http_error_message(status: int, headers: Message, spec: GitConfigSpec) -> str:
    """Turn a GitHub `HTTPError` into an actionable message that names the *real* cause (BE-0224).

    The rate-limit and SSO sub-types are checked before the catch-all so neither is misreported as
    missing repository access — more `Contents: read` permission fixes neither. The 404/other-403
    fall-through names the most-likely-missing grant.
    """
    where = f"{spec.owner}/{spec.repo}"
    # A rate limit is a 429, or a 403 with the rate-limit signals (primary limit → `X-RateLimit-
    # Remaining: 0`; secondary/abuse limit → a `Retry-After`). Check it before the 403 catch-all.
    if status == 429 or (
        status == 403
        and (headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After"))
    ):
        return (
            f"GitHub rate limit reached fetching {where}: wait and retry, or authenticate to raise "
            f"the limit — more repository permission does not lift a rate limit."
        )
    if status == 403 and headers.get("X-GitHub-SSO"):
        return (
            f"the credential for {where} needs SAML single sign-on (SSO) authorization: authorize "
            f"this token for the organization's SSO, then retry."
        )
    if status == 401:
        return f"the GitHub token was rejected fetching {where} (401): it is invalid or expired."
    if status in (403, 404):
        # A private repo the caller can't see returns 404 on github.com, so this is deliberately "not
        # found *or* access not granted" rather than blaming either one alone.
        return (
            f"cannot access {where} ({status}): repository not found, or access not granted — provide "
            f"a credential with Contents: read for {where}."
        )
    # Any other status (a 5xx outage, an unexpected 422) is not an access problem — don't misdirect
    # the operator toward granting permission that was never missing.
    return f"GitHub returned an unexpected {status} fetching {where}."


def github_token() -> str | None:
    """A GitHub token for private repos, else `gh auth token`, else None.

    Checks the bajutsu-owned `BAJUTSU_GIT_CONFIG_TOKEN` (a serve-entered credential) first, then
    `GITHUB_TOKEN` / `GH_TOKEN`, so a UI credential wins over an ambient token (BE-0224).
    """
    for var in (GIT_CONFIG_TOKEN_ENV, "GITHUB_TOKEN", "GH_TOKEN"):
        if tok := os.environ.get(var):
            return tok
    try:
        out = subprocess.run(
            ["gh", "auth", "token"],  # noqa: S607 — gh resolved on PATH; any failure → None below
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None  # a logged-out `gh` can exit 0 with blank stdout → no token


def resolve_github_credential(spec: GitConfigSpec) -> str | None:
    """The bearer token for `spec`, in documented precedence order (BE-0224).

    A configured **GitHub App installation** first (a short-lived, per-installation, service-identity
    token — the answer for an unattended host), then the `GITHUB_TOKEN` / `GH_TOKEN` / `gh auth token`
    chain of `github_token()`, else anonymous. Resolved **per acquisition** (the transport is built
    per `materialize`), so a rotated secret takes effect without a serve restart.
    """
    return _github_app_credential(spec) or github_token()


def _github_app_credential(spec: GitConfigSpec) -> str | None:
    """A GitHub App installation token when the App env is configured, else None (BE-0224).

    Opt-in and lazy: only an `BAJUTSU_GITHUB_APP_ID` gates the App path — the key is read (and
    `bajutsu.github_app`, plus `cryptography`, imported) *only* when the id is set, so a stale
    `…_PRIVATE_KEY_FILE` left in the environment without an id never triggers App auth or a file read.
    With an id but no key, this falls through to `github_token()` rather than half-attempting the App.
    """
    app_id = os.environ.get("BAJUTSU_GITHUB_APP_ID")
    if not app_id:
        return None
    private_key = _github_app_private_key()
    if not private_key:
        return None
    from bajutsu.github_app import installation_token

    return installation_token(
        app_id,
        private_key,
        spec,
        installation_id=os.environ.get("BAJUTSU_GITHUB_APP_INSTALLATION_ID"),
    )


def _github_app_private_key() -> str | None:
    """The App private key from `BAJUTSU_GITHUB_APP_PRIVATE_KEY`, else the file at `…_PRIVATE_KEY_FILE`.

    Raises:
        GitHubAccessError: the key file is configured but cannot be read — a legible message rather
            than a raw `OSError`, since the App path was explicitly requested by setting the id.
    """
    if key := os.environ.get("BAJUTSU_GITHUB_APP_PRIVATE_KEY"):
        return key
    if path := os.environ.get("BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE"):
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as e:
            raise GitHubAccessError(
                f"cannot read the GitHub App private key at BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE={path!r}: {e}"
            ) from e
    return None


class _GitHubTransport:
    """The real transport: GitHub's commits API (ref → SHA) and tarball endpoint, over urllib."""

    def __init__(self, token: str | None) -> None:
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _get(self, url: str, accept: str, spec: GitConfigSpec) -> bytes:
        req = urllib.request.Request(url, headers={**self._headers, "Accept": accept})  # noqa: S310 — https GitHub API URL
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return bytes(resp.read())
        except urllib.error.HTTPError as e:
            # Map the status (and 403 sub-type) to a cause-naming message instead of letting a raw
            # HTTPError — a bare "404" that really means "no access" — reach the operator (BE-0224).
            raise GitHubAccessError(github_http_error_message(e.code, e.headers, spec)) from e

    def commit_sha(self, spec: GitConfigSpec, ref: str) -> str:
        # The `…+sha` media type makes the commits endpoint return the bare SHA as the body.
        url = f"https://api.github.com/repos/{spec.owner}/{spec.repo}/commits/{ref}"
        return self._get(url, "application/vnd.github.sha", spec).decode().strip()

    def tarball_bytes(self, spec: GitConfigSpec, sha: str) -> bytes:
        url = f"https://api.github.com/repos/{spec.owner}/{spec.repo}/tarball/{sha}"
        return self._get(url, "application/vnd.github+json", spec)


def source_provenance(spec: GitConfigSpec, mat: Materialized) -> dict[str, str]:
    """The run-provenance stamp for a Git config source: repo + the ref it was asked for + the SHA.

    Recording the resolved `sha` makes a branch-based run reproducible after the fact (BE-0063) — it
    states the exact commit executed, not just the moving branch.
    """
    return {
        "host": spec.host,
        "owner": spec.owner,
        "repo": spec.repo,
        "ref": spec.ref or "(default)",
        "sha": mat.sha,
    }


def _bajutsu_cache_root() -> Path:
    """The shared cache root every Bajutsu-managed local cache nests under (BE-0243).

    Resolves under ``XDG_CACHE_HOME``, falling back to ``~/.cache``. A hosted deployment that
    writes here already needs `HOME`/`XDG_CACHE_HOME` set (the same precondition the Git source's
    cache root has always had); consolidating every local cache — Git checkouts and
    uploaded-bundle extracts alike — under one root keeps a hosted deployment's writable-path
    allowlist to one entry instead of two.
    """
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bajutsu"


def _default_cache_root() -> Path:
    return _bajutsu_cache_root() / "gitsrc"


def materialize(
    spec: GitConfigSpec,
    *,
    transport: Transport | None = None,
    cache_root: Path | None = None,
    offline: bool = False,
) -> Materialized:
    """Check out `spec`'s subtree at its resolved commit SHA into a content-addressed cache.

    The ref is resolved to an immutable SHA (the determinism anchor), then the tree is fetched once
    and extracted under `<cache>/<host>/<owner>/<repo>/<sha>/`. Because the directory is keyed by the
    SHA, a cache hit is always valid and a pinned-SHA run is offline after the first fetch.

    With `offline` (the `--config-offline` switch) it never touches the network: the ref must already
    be a full SHA (a branch/tag can't be resolved offline) and that SHA must already be cached.
    """
    if transport is None and spec.host != "github.com":
        # The general `git+https://…` spec form is parsed (the door is open for other hosts) but only
        # GitHub is implemented today — fail clearly rather than silently hitting github.com.
        raise ValueError(f"only github.com is supported today, got host {spec.host!r}")
    cache_root = cache_root or _default_cache_root()

    def _transport() -> Transport:
        # Built — and the credential resolved — only when a network op is actually needed, so a
        # pinned-SHA cache hit (and `--config-offline`) never resolves a credential, let alone mints
        # a GitHub App token (BE-0224). Resolved at most once per `materialize`.
        nonlocal transport
        if transport is None:
            transport = _GitHubTransport(resolve_github_credential(spec))
        return transport

    pinned = is_full_sha(spec.ref)
    if offline and not pinned:
        ref_label = repr(spec.ref) if spec.ref else "the default branch"
        raise ValueError(
            f"--config-offline needs a pinned commit SHA; cannot resolve {ref_label} without the network"
        )
    # A pinned full SHA is already the immutable id — use it directly so a cache hit is fully offline
    # (the determinism anchor the design promises). A branch/tag is resolved to its SHA; no ref ⇒ the
    # default branch, which "HEAD" resolves to on every Git host.
    sha = spec.ref if pinned else _transport().commit_sha(spec, spec.ref or "HEAD")
    assert sha is not None  # `pinned` implies spec.ref is set
    root = cache_root / spec.host / spec.owner / spec.repo / sha
    config_path = root / (spec.path or DEFAULT_CONFIG)
    # The spec's components and in-repo path become filesystem paths, so refuse any (`..`, an absolute
    # `:path`) that escapes the cache before fetching or reading anything.
    cache_resolved = cache_root.resolve()
    if not root.resolve().is_relative_to(
        cache_resolved
    ) or not config_path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"git spec resolves outside the cache: {spec}")
    if offline and not root.exists():
        raise ValueError(f"--config-offline: {spec.owner}/{spec.repo}@{sha} is not in the cache")
    if not root.exists():
        _extract_into(_transport().tarball_bytes(spec, sha), root)
    return Materialized(config_path, root, sha)


def _extract_into(tarball: bytes, root: Path) -> None:
    """Extract a GitHub tar.gz into `root`, stripping its single `<owner>-<repo>-<sha>/` wrapper dir.

    Extracts to a sibling temp dir and renames into place, so a concurrent run never sees a partial
    tree (the rename is atomic; a loser simply finds the directory already present).
    """
    root.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root.parent, prefix=f".{root.name}.tmp-"))
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
                _extract_stripped(tar, tmp)
        except tarfile.TarError as e:
            # A truncated/corrupt/non-tar body (a rate-limit page, a proxy interstitial) — present it
            # as a ValueError so callers' fetch-error handling catches it, not a bare traceback.
            raise ValueError(f"could not read the repository tarball: {e}") from e
        try:
            tmp.rename(root)
        except OSError:
            # A concurrent run won the rename; its tree is valid (same SHA), so drop ours.
            if not root.exists():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _extract_stripped(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract every member into `dest`, dropping the archive's single leading path component."""
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        parts = Path(member.name).parts
        if len(parts) <= 1:
            continue  # the wrapper directory entry itself
        rel = Path(*parts[1:])
        target = (dest / rel).resolve()
        # Refuse a member that escapes dest (a tar path-traversal), per Python's own guidance.
        if not target.is_relative_to(dest_resolved):
            raise ValueError(f"unsafe path in tarball: {member.name}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isreg():
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is not None:
                target.write_bytes(extracted.read())
