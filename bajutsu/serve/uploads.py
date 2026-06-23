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

import stat
import zipfile
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


class BundleError(ValueError):
    """An uploaded bundle is rejected: a malformed zip, a zip-slip entry, or a crossed resource
    bound. A ``ValueError`` subclass so callers' ``except ValueError`` covers it. The message names
    the violated rule and is safe to surface to the uploader — it never leaks a host path."""


@dataclass
class Upload:
    """A materialized bundle awaiting its run. ``dir`` is the extraction sandbox (deleted after the
    run); ``config`` is the located bundle config, whose parent is the bundle root the run uses as
    its working directory. ``filename``/``sha256``/``size`` are the upload's provenance, recorded
    into the run's manifest so "what did this run execute?" stays answerable (DESIGN §2)."""

    id: str
    dir: Path  # the extraction sandbox to remove after the run
    config: Path  # the bundle's bajutsu.config.yaml (its parent is the run's cwd)
    filename: str
    sha256: str
    size: int
    created: float  # time.monotonic() at registration, for the age-based sweep of orphans
    # The org that owns this upload (BE-0015 multi-tenancy). Required (not defaulted) so every upload
    # is owned — `take_upload` refuses a mismatching org. The single `default` org for local serve.
    org: str
    actor: str | None = None

    @property
    def root(self) -> Path:
        """The bundle root — the config's directory, used as the run's working directory so the
        config's relative entries (appPath / scenarios / baselines / build) resolve against it."""
        return self.config.parent


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


def find_bundle_config(root: Path) -> Path | None:
    """Locate the bundle's config: ``bajutsu.config.yaml`` at the extraction *root*, or — for a zip
    that wraps everything in a single top-level folder — one level down. Returns the config path (its
    parent is the bundle root the run runs from), or None if it's absent or the layout is ambiguous
    (no single nesting folder)."""
    for name in _CONFIG_NAMES:
        if (root / name).is_file():
            return root / name
    # A zip made from a folder nests everything under one dir. Ignore macOS / VCS cruft (a
    # `__MACOSX/` folder Archive Utility adds, a `.git/`) so the one real top folder is still found.
    subdirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))]
    if len(subdirs) == 1:
        for name in _CONFIG_NAMES:
            if (subdirs[0] / name).is_file():
                return subdirs[0] / name
    return None
