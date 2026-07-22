"""Locate and materialize the wheel-bundled generic XCUITest Simulator runner (BE-0292).

The XCUITest runner is app-agnostic (BE-0019): one built ``.xctestrun`` plus its products drives
whatever app a run targets. Shipping that runner as wheel package data lets ``xcuitest.testRunner``
be optional — a Simulator run that names no runner resolves to the bundled one here. The products
are inert on any non-macOS install (the runner only ever runs against a Simulator), so the base
wheel stays pure-Python; the bytes ride along as unused data.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

import bajutsu

# The packaged products directory: ``.xctestrun`` plus the test bundles beside it, populated by the
# release build step (``make runner-bundle``) and force-included via pyproject ``artifacts``. Absent
# in a plain source checkout and on a Linux wheel, so callers treat "no bundle" as a normal state.
_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "_xcuitest_runner"
_RUNNER_NAME = "BajutsuRunner.xctestrun"
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
_digest_cache: dict[Path, tuple[object, str]] = {}


def bundled_products_dir() -> Path | None:
    """Return the packaged runner products directory if this build ships one, else ``None``."""
    return _BUNDLE_DIR if (_BUNDLE_DIR / _RUNNER_NAME).is_file() else None


def _cache_root() -> Path:
    """The per-user cache root for the materialized runner, honoring ``XDG_CACHE_HOME``."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "bajutsu" / "xcuitest-runner"


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
            os.replace(tmp, dest)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            # A concurrent winner may have already materialized this digest onto *dest* (the
            # rename onto a non-empty directory then fails); take the winner's copy and swallow.
            if runner.is_file():
                return runner
            raise
    return runner
