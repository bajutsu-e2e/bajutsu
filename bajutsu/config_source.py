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
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_CONFIG = "bajutsu.config.yaml"

# `github:owner/repo[@ref][:path]` — the headline shorthand. owner/repo exclude the @ and : delimiters.
_GITHUB_RE = re.compile(
    r"^github:(?P<owner>[^/@:]+)/(?P<repo>[^/@:]+)(?:@(?P<ref>[^:]+))?(?::(?P<path>.+))?$"
)
# `git+https://host/owner/repo(.git)[@ref][#path]` — the general form (any Git host).
_GIT_URL_RE = re.compile(
    r"^git\+https://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/@#]+?)(?:\.git)?(?:@(?P<ref>[^#]+))?(?:#(?P<path>.+))?$"
)
# A full 40-hex commit SHA — already immutable, so it needs no commits-API resolution.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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
    """
    if m := _GITHUB_RE.match(value):
        return GitConfigSpec("github.com", m["owner"], m["repo"], m["ref"], m["path"])
    if m := _GIT_URL_RE.match(value):
        return GitConfigSpec(m["host"], m["owner"], m["repo"], m["ref"], m["path"])
    return None


class Transport(Protocol):
    """The Git-host calls materialization makes — injected so the logic tests offline."""

    def commit_sha(self, spec: GitConfigSpec, ref: str) -> str: ...

    def tarball_bytes(self, spec: GitConfigSpec, sha: str) -> bytes: ...


def github_token() -> str | None:
    """A GitHub token for private repos: `GITHUB_TOKEN` / `GH_TOKEN`, else `gh auth token`, else None."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
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


class _GitHubTransport:
    """The real transport: GitHub's commits API (ref → SHA) and tarball endpoint, over urllib."""

    def __init__(self, token: str | None) -> None:
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _get(self, url: str, accept: str) -> bytes:
        req = urllib.request.Request(url, headers={**self._headers, "Accept": accept})  # noqa: S310 — https GitHub API URL
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return bytes(resp.read())

    def commit_sha(self, spec: GitConfigSpec, ref: str) -> str:
        # The `…+sha` media type makes the commits endpoint return the bare SHA as the body.
        url = f"https://api.github.com/repos/{spec.owner}/{spec.repo}/commits/{ref}"
        return self._get(url, "application/vnd.github.sha").decode().strip()

    def tarball_bytes(self, spec: GitConfigSpec, sha: str) -> bytes:
        url = f"https://api.github.com/repos/{spec.owner}/{spec.repo}/tarball/{sha}"
        return self._get(url, "application/vnd.github+json")


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


def _default_cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bajutsu" / "gitsrc"


def materialize(
    spec: GitConfigSpec,
    *,
    transport: Transport | None = None,
    cache_root: Path | None = None,
) -> Materialized:
    """Check out `spec`'s subtree at its resolved commit SHA into a content-addressed cache.

    The ref is resolved to an immutable SHA (the determinism anchor), then the tree is fetched once
    and extracted under `<cache>/<host>/<owner>/<repo>/<sha>/`. Because the directory is keyed by the
    SHA, a cache hit is always valid and a pinned-SHA run is offline after the first fetch.
    """
    if transport is None:
        # The general `git+https://…` spec form is parsed (the door is open for other hosts) but only
        # GitHub is implemented today — fail clearly rather than silently hitting github.com.
        if spec.host != "github.com":
            raise ValueError(f"only github.com is supported today, got host {spec.host!r}")
        transport = _GitHubTransport(github_token())
    cache_root = cache_root or _default_cache_root()

    # A pinned full SHA is already the immutable id — use it directly so a cache hit is fully offline
    # (the determinism anchor the design promises). A branch/tag is resolved to its SHA; no ref ⇒ the
    # default branch, which "HEAD" resolves to on every Git host.
    sha = (
        spec.ref
        if spec.ref and _FULL_SHA_RE.match(spec.ref)
        else transport.commit_sha(spec, spec.ref or "HEAD")
    )
    root = cache_root / spec.host / spec.owner / spec.repo / sha
    config_path = root / (spec.path or DEFAULT_CONFIG)
    # The spec's components and in-repo path become filesystem paths, so refuse any (`..`, an absolute
    # `:path`) that escapes the cache before fetching or reading anything.
    cache_resolved = cache_root.resolve()
    if not root.resolve().is_relative_to(
        cache_resolved
    ) or not config_path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"git spec resolves outside the cache: {spec}")
    if not root.exists():
        _extract_into(transport.tarball_bytes(spec, sha), root)
    return Materialized(config_path, root, sha)


def _extract_into(tarball: bytes, root: Path) -> None:
    """Extract a GitHub tar.gz into `root`, stripping its single `<owner>-<repo>-<sha>/` wrapper dir.

    Extracts to a sibling temp dir and renames into place, so a concurrent run never sees a partial
    tree (the rename is atomic; a loser simply finds the directory already present).
    """
    root.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root.parent, prefix=f".{root.name}.tmp-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
            _extract_stripped(tar, tmp)
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
