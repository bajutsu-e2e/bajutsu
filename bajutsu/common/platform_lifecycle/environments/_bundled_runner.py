"""Locate and materialize the wheel-bundled generic XCUITest Simulator runner (BE-0292).

The XCUITest runner is app-agnostic (BE-0019): one built ``.xctestrun`` plus its products drives
whatever app a run targets. Shipping that runner as wheel package data lets ``xcuitest.testRunner``
be optional — a Simulator run that names no runner resolves to the bundled one here. The products
are inert on any non-macOS install (the runner only ever runs against a Simulator), so the base
wheel stays pure-Python; the bytes ride along as unused data.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import bajutsu
from bajutsu.common.backend_cli import simctl

# The packaged products directory: ``.xctestrun`` plus the test bundles beside it, populated by the
# release build step (``make runner-bundle``) and force-included via pyproject ``artifacts``. Absent
# in a plain source checkout and on a Linux wheel, so callers treat "no bundle" as a normal state.
_BUNDLE_DIR = Path(__file__).resolve().parents[3] / "_xcuitest_runner"
_RUNNER_NAME = "BajutsuRunner.xctestrun"
# The toolchain metadata `make runner-bundle` records beside the products: the Xcode and Simulator
# SDK versions the runner was built against, so `doctor` can warn when the host toolchain differs
# from them rather than letting the mismatch surface as an opaque `xcodebuild` launch failure.
_BUILD_INFO_NAME = "build-info.json"
# Marks an in-flight copy's temp directory; the sweep below matches on it and never on a real
# cache directory (whose name is ``{version}-{digest}`` and carries no such marker).
_PARTIAL_MARKER = ".partial-"
# A copytree of the runner products (a handful of small files) never legitimately runs this long;
# a partial older than this was abandoned by a crash, not left by an in-flight concurrent copy.
_STALE_PARTIAL_AGE_SECONDS = 5 * 60
# Gates the expensive full-content hash in `_products_digest`: a cheap per-file (size, mtime) stat
# is enough to detect that a source tree is unchanged since the last call in this process, which is
# the common case — the device pool calls `materialize()` once per simulator lane against the same
# bundled products.
_DigestSignature = tuple[tuple[str, int, int], ...]
_digest_cache: dict[Path, tuple[_DigestSignature, str]] = {}
# Serializes the check-compute-store in `_products_digest`. `materialize()` runs once per simulator
# lane, fanned out concurrently (`ThreadPoolExecutor` in the runner pipeline, a thread per udid in
# serve jobs), so on a cold cache every lane would otherwise race the empty dict and each pay the
# full SHA-256 read. Holding the lock across the read lets the first lane populate the cache and the
# rest reuse it — the redundant hashing the cache exists to avoid, avoided on the first call too.
_digest_lock = threading.Lock()
# Serializes `ensure_bundled_runner_fresh`'s check-then-rebuild, for the same reason `_digest_lock`
# serializes `_products_digest`: the device pool calls it once per simulator lane, fanned out
# concurrently, and without a lock a cold/stale bundle would start one `xcodebuild` per lane instead
# of one for the whole process.
_runner_build_lock = threading.Lock()


def bundled_products_dir() -> Path | None:
    """Return the packaged runner products directory if this build ships one, else ``None``."""
    return _BUNDLE_DIR if (_BUNDLE_DIR / _RUNNER_NAME).is_file() else None


def bundled_runner_build_info() -> dict[str, str] | None:
    """The toolchain metadata recorded beside the bundled runner, or ``None`` when unavailable.

    Returns the ``{"xcode": ..., "sdk": ...}`` map ``make runner-bundle`` wrote (see
    ``_BUILD_INFO_NAME``). ``None`` covers every "nothing to compare" case a caller treats alike: no
    bundle, an older bundle built before this metadata was recorded, or a file that fails to parse.
    Values are coerced to strings so a malformed field can't reach the version comparison as a
    non-string.
    """
    products = bundled_products_dir()
    if products is None:
        return None
    try:
        raw = json.loads((products / _BUILD_INFO_NAME).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    # JSON object keys are always strings; coerce only the values so a malformed field (e.g. a number)
    # can't reach the version comparison as a non-string.
    return {k: str(v) for k, v in raw.items()}


# The source paths whose contents decide whether the staged bundle is stale (relative to the
# checkout root). `source_hash` below reproduces the shasum-of-shasums the shell script this
# replaced used (`scripts/xcuitest-runner-hash.sh`, removed in the same change) — matching the
# algorithm, not just the inputs, is what keeps a checkout mid-migration from seeing every
# already-fresh bundle flip to "stale" at once.
HASH_SOURCE_PATHS = (
    "Package.swift",
    "BajutsuKit/Sources",
    "BajutsuKit/Runner/Host",
    "BajutsuKit/Runner/Sources",
    "BajutsuKit/Runner/project.yml",
)


def repo_root() -> Path:
    """The checkout root one level above the installed ``bajutsu`` package, where ``BajutsuKit/`` lives."""
    return Path(__file__).resolve().parents[4]


def runner_source_present(*, root: Path | None = None) -> bool:
    """Whether this checkout ships BajutsuKit's own source, as opposed to a wheel install.

    ``pyproject.toml``'s ``packages = ["bajutsu"]`` never includes ``BajutsuKit/`` in a built wheel,
    so its presence reliably tells a git checkout (where the bundled runner can be rebuilt) apart from
    an installed distribution (where it can only ever be whatever the wheel shipped). *root* overrides
    the checkout root (tests inject a ``tmp_path``).
    """
    return ((root or repo_root()) / "BajutsuKit" / "Runner" / "project.yml").is_file()


def source_hash(*, root: Path | None = None) -> str:
    """Content hash of the sources that feed the bundled runner (BE-0292's freshness check).

    Reproduces the shasum-of-shasums the removed ``scripts/xcuitest-runner-hash.sh`` used: hash each
    file under ``HASH_SOURCE_PATHS``, then hash the concatenation of ``"<digest>  <relative
    path>\\n"`` lines (sorted by path), exactly as piping ``find | sort -z | xargs shasum -a 256 |
    shasum -a 256`` formatted them. Call only when ``runner_source_present()`` is true; a wheel
    install has nothing under these paths to hash. *root* overrides the checkout root (tests inject a
    ``tmp_path``).

    Raises ``FileNotFoundError`` if a listed path is neither a file nor a directory: silently
    dropping it would shrink the hashed set instead of failing loudly, so a future rename like
    ``BajutsuKit/Package.swift`` -> ``Package.swift`` (which this project's own history hit mid-port)
    would otherwise stop marking the bundle stale for edits under the moved path.
    """
    root = root or repo_root()
    files: list[Path] = []
    for rel in HASH_SOURCE_PATHS:
        target = root / rel
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(p for p in target.rglob("*") if p.is_file())
        else:
            raise FileNotFoundError(f"source_hash: expected path is missing: {target}")

    def _relative(path: Path) -> str:
        return path.relative_to(root).as_posix()

    lines = (
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {_relative(path)}\n"
        for path in sorted(files, key=_relative)
    )
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def bajutsu_cache_root() -> Path:
    """The per-user Bajutsu cache root, honoring ``XDG_CACHE_HOME`` (shared with the device build)."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "bajutsu"


def _cache_root() -> Path:
    """The per-user cache root for the materialized runner."""
    return bajutsu_cache_root() / "xcuitest-runner"


def _products_digest(source: Path) -> str:
    """Short content digest of *source*, so a rebuilt products tree keys a fresh cache directory.

    The version string is a static ``0.0.0`` placeholder pre-release (BE-0272), so it cannot detect
    that a wheel shipped updated runner products; digesting the tree can. Hashing each file's bytes
    (not just its path and size) also catches a rebuild that changes content at an unchanged size —
    e.g. a recompiled binary or a swapped ``Info.plist`` value of equal length. The cheap per-file
    (size, mtime) signature gates that hash, so a repeat call against an unchanged tree in this
    process skips re-reading every byte.
    """
    files = sorted(p for p in source.rglob("*") if p.is_file())
    # One stat() per file, not two: the (size, mtime) pair is the whole point of the cheap gate
    # above, so paying two syscalls per file to build it would undercut it.
    signature = tuple(
        (str(p.relative_to(source)), (st := p.stat()).st_size, st.st_mtime_ns) for p in files
    )
    # Under the lock so a cold-cache fan-out hashes once, not once per lane (see `_digest_lock`).
    with _digest_lock:
        cached = _digest_cache.get(source)
        if cached is not None and cached[0] == signature:
            return cached[1]

        h = hashlib.sha256()
        for path in files:
            h.update(str(path.relative_to(source)).encode())
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        digest = h.hexdigest()[:12]
        _digest_cache[source] = (signature, digest)
        return digest


def materialize(
    source: Path,
    *,
    version: str = bajutsu.__version__,
    cache_root: Path | None = None,
) -> Path:
    """Copy *source* products into a content-keyed writable cache and return the ``.xctestrun`` path.

    The installed wheel's package data is read-only, yet a run patches a copy of the ``.xctestrun``
    beside the products to inject its launch environment, so the runner must sit somewhere writable.
    The copy is keyed by a digest of *source*'s contents (see ``_products_digest``): updated runner
    products land in a fresh directory, and a warm cache with the same digest is reused without
    recopying. *version* rides along in the directory name but no longer drives freshness on its own.

    Args:
        source: The products directory to copy (typically ``bundled_products_dir()``).
        version: Included in the cache directory name; defaults to the installed Bajutsu version.
        cache_root: Override the cache location (tests inject a ``tmp_path``).

    Returns:
        The path to the materialized ``.xctestrun``.
    """
    root = cache_root or _cache_root()
    dest = root / f"{version}-{_products_digest(source)}"
    runner = dest / _RUNNER_NAME
    if not runner.is_file():
        # Copy into a unique per-process temp dir then atomically rename, so a crash mid-copy never
        # leaves a half-populated cache directory, and parallel runs on the same host + digest
        # (the device pool spans simulators) never clobber each other's in-flight copy.
        dest.parent.mkdir(parents=True, exist_ok=True)
        # A hard kill (SIGKILL/OOM) between mkdtemp and the except leaves a `.partial-*` sibling that
        # nothing else sweeps; drop leftovers best-effort before adding another. Only siblings with
        # the partial prefix match, so real cache directories are never touched. The age gate is what
        # keeps this from racing a concurrent lane's still-copying sibling: a copytree of these few
        # small files never legitimately takes _STALE_PARTIAL_AGE_SECONDS, so anything that old was
        # abandoned by a crash, not left mid-copy by another process.
        now = time.time()
        for stale in dest.parent.glob(f"*{_PARTIAL_MARKER}*"):
            try:
                age = now - stale.stat().st_mtime
            except OSError:
                continue  # a concurrent sweep or rename already removed it
            if age > _STALE_PARTIAL_AGE_SECONDS:
                shutil.rmtree(stale, ignore_errors=True)
        tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f"{dest.name}{_PARTIAL_MARKER}"))
        try:
            # symlinks=True: an Xcode build-for-testing product can embed a `.framework`'s
            # `Versions/Current`-style symlink; copying it as a symlink preserves that structure
            # instead of dereferencing (and potentially duplicating or failing on) its target.
            shutil.copytree(source, tmp, dirs_exist_ok=True, symlinks=True)
            tmp.replace(dest)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            # A concurrent winner may have already materialized this digest onto *dest* (the
            # rename onto a non-empty directory then fails); take the winner's copy and swallow.
            if runner.is_file():
                return runner
            raise
    return runner


def _bundle_matches(digest: str) -> bool:
    """Whether the currently staged bundle exists and was built from *digest*'s source tree."""
    info = bundled_runner_build_info()
    return (
        bundled_products_dir() is not None and info is not None and info.get("sourceHash") == digest
    )


def bundled_runner_is_stale() -> bool:
    """Whether a dev checkout's bundled runner needs a rebuild `ensure_bundled_runner_fresh` hasn't run yet.

    Disclosure only (`doctor`'s `xcuitest_runner_summary`) — never rebuilds. True whenever this
    checkout ships BajutsuKit's source (a wheel install has nothing to compare against, so it can
    never be "stale" in this sense) and the staged bundle — including "no bundle staged at all", on a
    fresh clone that has never run `make runner-bundle` — doesn't match it. `ensure_bundled_runner_fresh`
    handles both cases identically (a missing bundle is just a more extreme mismatch), so this
    disclosure must too: a caller that only flagged "staged but stale" would leave `doctor` telling a
    fresh clone to configure a `testRunner` it doesn't need, when the next run would simply build one.
    """
    if not runner_source_present():
        return False
    return not _bundle_matches(source_hash())


def ensure_bundled_runner_fresh() -> None:
    """Rebuild the wheel-bundled runner when this checkout's BajutsuKit source has moved past it.

    A Simulator run with no explicit ``xcuitest.testRunner`` falls back to this bundle (BE-0292); in
    a dev checkout, that fallback must track BajutsuKit's own source rather than whatever a past
    ``make serve`` or release build last staged. No-ops when ``runner_source_present()`` is false (a
    wheel install has no BajutsuKit source to compare against, so the shipped bundle is definitionally
    current) and when ``BAJUTSU_SKIP_RUNNER_BUNDLE=1`` is set, the same escape hatch
    ``scripts/serve.sh`` already offered. Raises rather than silently keeping a stale or absent
    bundle, since staying current is the whole point of calling this.
    """
    if not runner_source_present():
        return
    if os.environ.get("BAJUTSU_SKIP_RUNNER_BUNDLE") == "1":
        return

    digest = source_hash()
    if _bundle_matches(digest):
        return

    with _runner_build_lock:
        # A concurrent simulator lane may have rebuilt while this one waited for the lock.
        if _bundle_matches(digest):
            return
        _rebuild_bundle()
        if not _bundle_matches(digest):
            # `make runner-bundle` exited 0 without staging a bundle that matches *digest* — e.g. the
            # Makefile's `"$(uv run python -c '...')"` command substitution failed inside a `printf`
            # that itself still succeeds, stamping an empty sourceHash. Left unchecked, every later
            # call would see the same mismatch and silently pay another full rebuild instead of ever
            # raising, which is exactly the "silently keeping a stale bundle" this function exists to
            # rule out.
            raise simctl.DeviceError(
                "xcuitest bundled runner rebuild reported success but the staged bundle still does "
                "not match the current source — check bajutsu/_xcuitest_runner/build-info.json"
            )


def _rebuild_bundle() -> None:
    """Run ``make runner-bundle``, or raise naming whichever build tool is missing.

    Named after ``scripts/serve.sh``'s own tool check, so the two surfaces report an absent toolchain
    the same way.
    """
    missing = []
    if shutil.which("xcodebuild") is None:
        missing.append("Xcode (xcodebuild) — install Xcode")
    if shutil.which("xcodegen") is None:
        missing.append("xcodegen — run 'make deps'")
    if missing:
        raise simctl.DeviceError(
            "xcuitest bundled runner is stale and cannot be rebuilt: " + "; ".join(missing)
        )
    try:
        subprocess.run(
            ["make", "runner-bundle"],  # noqa: S607 — make resolved on PATH; argv list
            cwd=repo_root(),
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise simctl.DeviceError(f"xcuitest bundled runner rebuild failed: {exc}") from exc
