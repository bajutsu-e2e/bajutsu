"""Receive a config + scenarios + app-binary bundle as an uploaded zip and materialize it (BE-0073).

An uploaded ``.zip`` is treated as "a tree to materialize" — the same self-contained checkout a
local ``bajutsu run`` already consumes (``bajutsu.config.yaml`` + its scenario tree + the built
``appPath`` binary), just delivered over the wire. This module owns the security-sensitive
extraction: every entry is validated to land **strictly under** the extraction root (zip-slip), and
resource bounds (entry count, total uncompressed size, per-entry compression ratio) abort a
zip-bomb the moment a bound is crossed, rather than after filling the disk. The decompressed bytes
are counted as they stream — a lying ``file_size`` header can't slip a bomb past the size cap.

Pure packaging/plumbing: no device, no AI, no effect on the verdict — the deterministic ``run``
happens downstream against the materialized tree. Sits on the serve hardening in BE-0051 (token
auth + path confinement): extraction extends the same "confine to a root" invariant serve enforces
for config/baseline paths (`_confined_config_path`) to archive entries.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Resource bounds (zip-bomb defense). Extraction aborts the moment one is crossed. Sized for a real
# bundle — a config + a scenario tree + a built ``.app``/``.ipa`` (an app bundle holds many small
# files: frameworks, assets) — with headroom, not for arbitrary archives.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB on the wire (the compressed upload)
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB total uncompressed
MAX_ENTRIES = 100_000  # number of members in the archive
MAX_RATIO = 200  # per-entry uncompressed / compressed (ignored for tiny entries below)
_RATIO_FLOOR = (
    4096  # skip the ratio check for entries this small (a few bytes inflate misleadingly)
)
_CHUNK = 1024 * 1024  # stream entries in 1 MiB chunks so a huge member never loads into memory

# The config file a bundle must contain at its root (or one level down, see find_bundle_config).
_CONFIG_NAMES = ("bajutsu.config.yaml", "bajutsu.config.yml")

# Top-level entries that aren't the bundle's real nesting folder: the `__MACOSX/` dir macOS Archive
# Utility adds beside the zipped folder. Dot-prefixed dirs (`.git/`, …) are skipped separately, so a
# legitimately-named folder (even one starting with `__`) is never mistaken for cruft.
_CRUFT_DIRS = frozenset({"__MACOSX"})


class BundleError(ValueError):
    """An uploaded bundle is rejected: a malformed zip, a zip-slip entry, or a crossed resource
    bound. A ``ValueError`` subclass so callers' ``except ValueError`` covers it. The message names
    the violated rule and is safe to surface to the uploader — it never leaks a host path."""


class UploadTooLarge(Exception):
    """The streamed upload crossed its byte cap mid-transfer — distinct from a rejected
    ``Content-Length`` header, since a lying or chunked-without-length request can't be caught
    before reading."""


class BoundedZipReceiver:
    """Streams an uploaded zip's bytes into a confined temp file, capped and hashed as they arrive
    (BE-0073) — the transport-agnostic core the stdlib handler's blocking ``rfile.read`` loop and the
    FastAPI async ``request.stream()`` loop both drive, so the bound + hash logic lives once instead
    of drifting between the two backends the way BE-0073's route itself once did. A caller pushes
    each chunk via ``write`` (raising ``UploadTooLarge`` the instant the cap is crossed), calls
    ``digest`` to finalize and get the hex sha256, and ``cleanup`` in a ``finally`` to remove the temp
    file — safe to call whether or not ``digest`` ran, and whether or not the caller consumed it."""

    def __init__(self, *, cap: int | None = None) -> None:
        # Read the module constant here, not as a parameter default — a default is bound once at
        # class-definition time, which would freeze the very first (real) value and put it out of a
        # test's monkeypatch reach forever.
        self._cap = MAX_UPLOAD_BYTES if cap is None else cap
        self._digest = hashlib.sha256()
        self.received = 0
        fd, name = tempfile.mkstemp(suffix=".zip")
        self._file = open(fd, "wb")  # noqa: SIM115
        self.path = Path(name)

    def write(self, chunk: bytes) -> None:
        self.received += len(chunk)
        if self.received > self._cap:
            raise UploadTooLarge(f"upload too large (max {self._cap} bytes)")
        self._digest.update(chunk)
        self._file.write(chunk)

    def digest(self) -> str:
        """Close the write handle and return the hex sha256 of everything written so far."""
        self._file.close()
        return self._digest.hexdigest()

    def cleanup(self) -> None:
        """Remove the temp file. Idempotent — safe after `digest()` or on a failure path instead."""
        self._file.close()
        self.path.unlink(missing_ok=True)


@dataclass
class Upload:
    """A bundle extracted and bound as the active config (BE-0073). ``dir`` is the sha256-keyed
    extraction cache entry under `state.uploads_dir` (BE-0243) — independent of this one bind, so
    unbinding leaves it in place for reuse; ``config`` is the located bundle config, whose parent is
    the bundle root every run/record/crawl off it uses as its working directory.
    ``filename``/``sha256``/``size`` are the upload's provenance, recorded into each run's manifest so
    "what did this run execute?" stays answerable (DESIGN §2)."""

    dir: Path  # the sha256-keyed extraction cache entry (BE-0243); outlives this bind
    config: Path  # the bundle's bajutsu.config.yaml (its parent is the runs' cwd)
    filename: str
    sha256: str
    size: int
    # The org that bound this bundle (BE-0015 multi-tenancy). The single `default` org for local serve.
    org: str
    actor: str | None = None

    @property
    def root(self) -> Path:
        """The bundle root — the config's directory, used as the runs' working directory so the
        config's relative entries (appPath / scenarios / baselines / build) resolve against it."""
        return self.config.parent

    @property
    def provenance(self) -> dict[str, str]:
        """The ``provenance`` block recorded into a run's manifest for a run off this bundle: the
        uploaded file name + zip sha256 + size, so the run's source is answerable after the sandbox
        is gone (DESIGN §2). Sizes are stringified to keep the block all-strings, like the audit log."""
        return {
            "source": "upload",
            "filename": self.filename,
            "sha256": self.sha256,
            "size": str(self.size),
        }


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """Whether *info* is a symlink entry. A symlink could point outside the extraction root, so it
    is rejected outright (mirroring how the run-dir archiver skips symlinks, BE-0060)."""
    return stat.S_ISLNK(info.external_attr >> 16)


def _safe_target(dest_root: Path, name: str) -> Path:
    """Resolve archive entry *name* to a path **strictly under** *dest_root*, or raise BundleError.

    Rejects absolute paths and ``..`` traversal: resolving first normalizes any ``..`` so the
    containment check is sound (the same reasoning as serve's `_confined_config_path`). A backslash
    is treated as a separator too, so a Windows-style ``..\\`` can't sneak past."""
    cleaned = name.replace("\\", "/")
    pure = PurePosixPath(cleaned)
    if not cleaned or "\x00" in cleaned or pure.is_absolute() or ".." in pure.parts:
        raise BundleError(f"unsafe entry path: {name!r}")
    target = (dest_root / cleaned).resolve()
    if target != dest_root and dest_root not in target.parents:
        raise BundleError(f"entry escapes the bundle root: {name!r}")
    return target


def _check_ratio(info: zipfile.ZipInfo) -> None:
    """Reject an entry whose declared compression ratio screams zip-bomb. A fast header pre-check
    before streaming; the streamed byte count (in extract_bundle) is the real defense."""
    if (
        info.file_size > _RATIO_FLOOR
        and info.compress_size > 0
        and info.file_size / info.compress_size > MAX_RATIO
    ):
        raise BundleError(f"entry compression ratio too high: {info.filename!r}")


def extract_bundle(zip_path: Path, dest: Path) -> None:
    """Extract the validated bundle at *zip_path* into *dest* (which must already exist).

    Every entry is confined under *dest* (zip-slip), symlink entries are rejected, and the
    decompressed bytes are counted as they stream so a zip-bomb is stopped the instant it crosses a
    bound — never after filling the disk. Raises ``BundleError`` on any violation; the caller is
    expected to remove *dest* on failure (a partial extraction is meaningless)."""
    dest_root = dest.resolve()
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BundleError(f"not a valid zip archive: {e}") from e
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise BundleError(f"too many entries ({len(infos)} > {MAX_ENTRIES})")
        written = 0
        for info in infos:
            target = _safe_target(dest_root, info.filename)
            if _is_symlink(info):
                raise BundleError(f"symlink entries are not allowed: {info.filename!r}")
            _check_ratio(info)
            # Treat any per-entry failure as a bad bundle (400 + cleanup), not an uncaught 500: a
            # malformed archive (a file entry, then a path *under* it) makes mkdir/open raise a bare
            # OSError, and a corrupt/CRC-bad member raises BadZipFile *mid-read* (not at open, and it
            # is neither OSError nor BundleError, so it would otherwise escape every catch).
            try:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as out:
                    while chunk := src.read(_CHUNK):
                        written += len(chunk)
                        if written > MAX_TOTAL_BYTES:
                            raise BundleError(
                                f"bundle exceeds {MAX_TOTAL_BYTES} bytes uncompressed (zip-bomb?)"
                            )
                        out.write(chunk)
            except (OSError, zipfile.BadZipFile) as e:
                raise BundleError(f"could not extract {info.filename!r}: {e}") from e


def materialize_bundle(
    zip_path: Path,
    uploads_dir: Path,
    sha256: str,
    *,
    validate: Callable[[Path], None] | None = None,
) -> Path:
    """Resolve *sha256*'s content-addressed extraction under *uploads_dir*, extracting only on a
    cache miss (BE-0243) — a hit reuses the existing tree with no re-extraction and no re-*validate*,
    the same trust boundary `config_source.materialize` already gives a cached Git checkout for its
    resolved SHA: this replica proved this exact content once, so it need not prove it again.

    On a miss, extracts *zip_path* into a sibling temp dir (so a concurrent miss for the same
    *sha256* never observes a partial tree), runs *validate* against it if given — its exception
    propagates and the temp dir is discarded, leaving no partial cache entry — then renames into
    place. The rename is atomic (mirrors `config_source._extract_into`): a losing concurrent call
    either finds the directory already there, or has its own rename fail because the winner's landed
    first, and discards its copy rather than treating that as an error (both extractions are
    byte-identical for the same key).

    The returned directory is never deleted by this function once it exists — the cache has no
    concept of "this caller owns it and may remove it", since any number of binds, on this replica
    or another, may already depend on it by the time a caller's own next step fails. A caller with a
    later failure of its own (e.g. an object-store write) must fail without touching this directory.
    """
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / sha256
    if dest.exists():
        return dest
    tmp = Path(tempfile.mkdtemp(dir=uploads_dir, prefix=f".{sha256}.tmp-"))
    try:
        extract_bundle(zip_path, tmp)
        if validate is not None:
            validate(tmp)
        try:
            tmp.rename(dest)
        except OSError:
            # A concurrent call won the rename; its tree is valid (same sha256), so drop ours.
            if not dest.exists():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        # Covers every failure above, including a genuine (non-lost-race) rename failure: the inner
        # `raise` re-enters here, so `tmp` is cleaned up exactly once regardless of which step failed
        # (mirrors `config_source._extract_into`'s own outer try/except).
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def find_bundle_config(root: Path) -> Path | None:
    """Locate the bundle's config: ``bajutsu.config.yaml`` at the extraction *root*, or — for a zip
    that wraps everything in a single top-level folder — one level down. Returns the config path (its
    parent is the bundle root the run runs from), or None if it's absent or the layout is ambiguous
    (no single nesting folder)."""
    for name in _CONFIG_NAMES:
        if (root / name).is_file():
            return root / name
    # A zip made from a folder nests everything under one dir. Ignore macOS / VCS cruft (the
    # `__MACOSX/` folder Archive Utility adds, dot-dirs like `.git/`) so the one real top folder is
    # still found — but only the *known* cruft, so a real folder named e.g. `__suite/` is not skipped.
    subdirs = [
        d
        for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in _CRUFT_DIRS
    ]
    if len(subdirs) == 1:
        for name in _CONFIG_NAMES:
            if (subdirs[0] / name).is_file():
                return subdirs[0] / name
    return None
