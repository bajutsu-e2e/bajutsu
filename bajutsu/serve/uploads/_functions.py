"""Extract an uploaded bundle safely: no symlink, no zip-slip, no crossed cap."""

from __future__ import annotations

import shutil
import stat
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from bajutsu.common.config import load_config, resolve

from .bundle_error import BundleError

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


# Resource bounds for a scenario-only zip (BE-0340) — sized for a handful of scenario YAML files (a
# few KB to tens of KB apiece), far smaller than the whole-bundle bounds above, which exist for app
# binaries and asset frameworks.
MAX_SCENARIO_ZIP_ENTRIES = 500
MAX_SCENARIO_ENTRY_BYTES = 2 * 1024 * 1024  # 2 MiB per scenario file
MAX_SCENARIO_ZIP_TOTAL_BYTES = 20 * 1024 * 1024  # 20 MiB total uncompressed


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


def _scenario_entry_name(name: str) -> str | None:
    """The flat top-level ``*.yaml`` basename for archive entry *name*, or None to skip it silently
    (a directory entry's own path component, known packaging cruft — ``__MACOSX/``, a dot-file —
    or a nested non-scenario file). Raises ``BundleError`` for anything else that isn't safe to
    treat as a scenario entry: a path-traversal attempt, a nested ``*.yaml``/``*.yml`` — a scenario
    scope has no subdirectory concept (BE-0340 *Alternatives considered*), so a nested scenario is
    reported rather than silently dropped — or a top-level ``*.yml``: the scenario model only ever
    recognizes ``*.yaml`` (`valid_scenario_ref`, `LocalScenarioScope.runnable`'s glob), so silently
    dropping a ``.yml`` entry the same way as unrelated cruft would leave a mixed zip reporting
    success while quietly never adding the file the caller actually meant to upload — the same
    reasoning that makes an unparseable entry abort the whole upload instead of a partial,
    surprising result. A nested *non*-scenario entry (an asset, a stray editor cache) doesn't share
    that reasoning — it was never a candidate for upload — so it is skipped like its top-level
    counterpart below rather than failing an otherwise-valid flat batch."""
    cleaned = name.replace("\\", "/")
    pure = PurePosixPath(cleaned)
    if not cleaned or "\x00" in cleaned or pure.is_absolute() or ".." in pure.parts:
        raise BundleError(f"unsafe entry path: {name!r}")
    if len(pure.parts) > 1:
        if pure.parts[0] in _CRUFT_DIRS or pure.suffix.lower() not in (".yaml", ".yml"):
            return None
        raise BundleError(f"nested entry path is not supported: {name!r}")
    if pure.name.startswith("."):
        return None
    if pure.suffix != ".yaml":
        # A case or spelling variant of the scenario suffix (.yml, .Yaml, .YAML, ...) is reported
        # rather than silently skipped like unrelated cruft — `valid_scenario_ref` requires the
        # exact lowercase `.yaml`, so this entry could never be saved even if it were let through.
        if pure.suffix.lower() in (".yaml", ".yml"):
            raise BundleError(f"scenario files use a lowercase .yaml extension: {name!r}")
        return None
    return pure.name


def _read_scenario_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Stream one scenario entry, refusing it past `MAX_SCENARIO_ENTRY_BYTES` before it is buffered."""
    try:
        with archive.open(info) as src:
            chunks = []
            size = 0
            while chunk := src.read(_CHUNK):
                size += len(chunk)
                if size > MAX_SCENARIO_ENTRY_BYTES:
                    raise BundleError(
                        f"scenario entry too large: {info.filename!r} "
                        f"(max {MAX_SCENARIO_ENTRY_BYTES} bytes)"
                    )
                chunks.append(chunk)
    except (OSError, RuntimeError, zlib.error, zipfile.BadZipFile) as e:
        # Wider than BadZipFile alone (like `extract_bundle` above): `ZipFile.open` raises
        # RuntimeError for a password-encrypted entry and NotImplementedError (a RuntimeError
        # subclass) for an unsupported compression method; a corrupted DEFLATE stream raises
        # zlib.error mid-read, independent of the CRC check that raises BadZipFile. All three are
        # driven by client-controlled entry data — a rejected zip is a 400, never a 500.
        raise BundleError(f"could not read {info.filename!r}: {e}") from e
    return b"".join(chunks)


def read_scenario_zip(zip_path: Path) -> dict[str, str]:
    """Read a ``.zip``'s flat top-level ``*.yaml`` entries into ``{name: text}`` (BE-0340).

    Entirely in memory — a scenario scope has no subdirectory concept and its files are text, so
    nothing here is extracted to disk the way ``extract_bundle`` extracts an app bundle to disk.
    Every entry is screened for zip-slip / a symlink / an oversized entry / an oversized total before
    any text is decoded, the same defenses ``extract_bundle`` applies at a bundle's larger scale.
    Raises ``BundleError`` on any violation, or if the zip holds no ``*.yaml`` entry at all — an
    upload that adds nothing is a caller mistake worth reporting, not a silent no-op."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BundleError(f"not a valid zip archive: {e}") from e
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_SCENARIO_ZIP_ENTRIES:
            raise BundleError(f"too many entries ({len(infos)} > {MAX_SCENARIO_ZIP_ENTRIES})")
        out: dict[str, str] = {}
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            if _is_symlink(info):
                raise BundleError(f"symlink entries are not allowed: {info.filename!r}")
            name = _scenario_entry_name(info.filename)
            if name is None:
                continue
            if name in out:
                # Two entries mapping to one scenario name: dropping the loser silently is the same
                # "reported success, file never added" outcome the rejections above exist to prevent.
                raise BundleError(f"duplicate scenario entry: {name!r}")
            # No ratio pre-check (unlike `extract_bundle`): a scenario entry is text whose absolute
            # size the streamed `MAX_SCENARIO_ENTRY_BYTES` / total checks below already bound, so the
            # bundle-sized 200:1 bound would only turn a legitimately repetitive scenario away.
            data = _read_scenario_entry(archive, info)
            total += len(data)
            if total > MAX_SCENARIO_ZIP_TOTAL_BYTES:
                raise BundleError(f"zip exceeds {MAX_SCENARIO_ZIP_TOTAL_BYTES} bytes uncompressed")
            try:
                out[name] = data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise BundleError(f"{info.filename!r} is not valid UTF-8 text: {e}") from e
    if not out:
        raise BundleError("zip contains no *.yaml scenario files")
    return out


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


def validate_bundle_config(root: Path) -> None:
    """Confine every target's path fields to *root* and confirm it has a loadable config — the same
    guard the Git source applies to a fetched checkout (BE-0051): a config pointing
    appPath/scenarios/baselines at an absolute or `..` path outside the tree is rejected here, so
    serve's resolution only ever sees in-bundle paths. Raises `BundleError` (no config file) or a
    `load_config`/`rebased` failure (`OSError` / `ValueError` / `yaml.YAMLError`) — both are
    `ValueError` subclasses, so one `except ValueError` at the call site covers either.

    Shared by `bind_upload_config`'s legacy single-zip bind and `materialize_composition`'s
    triple-artifact bind (BE-0268) — both hand it a materialized tree to validate the same way."""
    config_path = find_bundle_config(root)
    if config_path is None:
        raise BundleError("has no bajutsu.config.yaml")
    cfg = load_config(config_path.read_text(encoding="utf-8"))
    for name in cfg.targets:
        resolve(cfg, name).rebased(config_path.parent, confine=True)
