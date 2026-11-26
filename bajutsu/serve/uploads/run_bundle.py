"""Check and unpack the zip of one finished run that a script uploads (BE-0510).

The run was produced elsewhere, so every byte here is the client's. serve serves a run's files from
its own origin, and a hosted store answers with a signed URL that carries no header serve sets, so
what is allowed in at this point is the whole defence: passive file types only, artifact paths that
stay inside the run, and report files regenerated here rather than taken from the upload.

Pure packaging: no device, no AI, no effect on any verdict.
"""

from __future__ import annotations

import errno
import json
import os
import re
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.report.load import load_run, rebake
from bajutsu.common.run_meta.files import RUN_TOMBSTONE
from bajutsu.serve.helpers import valid_run_id

from ._functions import MAX_ENTRIES, MAX_TOTAL_BYTES, extract_bundle
from .bundle_error import BundleError

# Twice the bundle cap: screenshots and videos barely compress.
MAX_RUN_IMPORT_BYTES = 2 * 1024 * 1024 * 1024

# The re-render builds `report.html` in memory. It embeds an artifact's file once per reference, and
# a scenario's plan once per result, so a small zip could otherwise grow without bound.
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_EMBEDDED_BYTES = 512 * 1024 * 1024
# The artifact kinds the report only links. The renderer picks what to embed by kind, whatever the
# file is called, so every other kind counts toward the bound.
_LINKED_ONLY_KINDS = frozenset({"screenshot", "video"})

# The file types that can be served from serve's origin without running anything.
_PASSIVE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".json", ".txt", ".log"}
    | {".yaml", ".yml"}
)
# Written by every run, and always replaced by the server's own rendering.
_REGENERATED = ("report.html", "junit.xml", "ctrf.json")

# Errors that say the server failed, not the upload.
_SERVER_ERRNOS = frozenset(
    {errno.ENOSPC, errno.EDQUOT, errno.EACCES, errno.EPERM, errno.EROFS, errno.EIO}
)

# A URL scheme, as a browser reads one: anything before the first ":" of the first path segment.
_SCHEME = re.compile(r"^[^/]*:")


@dataclass(frozen=True)
class ImportedRun:
    """A run unpacked and re-rendered under the staging directory, ready to be placed."""

    run_id: str
    run_dir: Path
    manifest: dict[str, Any]
    dropped: list[str]


def unpack_run(zip_path: Path, staging: Path) -> ImportedRun:
    """Validate the run zip at *zip_path*, extract it under *staging*, and re-render its reports.

    The stored manifest gains ``provenance.source: import``, so a reader can tell that the client,
    not a worker, asserted the verdict. *staging* must exist and be empty; the caller removes it,
    on failure and on success alike.

    Raises:
        BundleError: The zip or the run in it breaks a rule; the message is safe to show the client.
        OSError: The server could not write the run, such as on a full disk.
    """
    run_id, files = _check_entries(zip_path)
    _extract(zip_path, staging)
    run_dir = staging / run_id
    for name in ("manifest.json", "scenario.yaml"):
        path = run_dir / name
        if path.is_file() and path.stat().st_size > MAX_MANIFEST_BYTES:
            raise BundleError(f"the run is too large: {name} is over {MAX_MANIFEST_BYTES} bytes")
    manifest = _read_manifest(run_dir, run_id)
    if not (run_dir / "scenario.yaml").is_file():
        raise BundleError("the run has no scenario.yaml")
    _refuse_yaml_aliases(run_dir / "scenario.yaml")
    _check_all_artifact_paths(run_dir, manifest, files)
    dropped = _drop_active_files(run_dir)
    _mark_imported(run_dir, manifest)
    try:
        _check_render_size(run_dir, manifest)
        rebake(run_dir)
    except BundleError:
        raise
    except Exception as e:
        # Anything: the loader and renderer read client data, and a huge number alone raises an
        # `OverflowError` neither documents.
        _raise_server_error(e)
        raise BundleError(f"the run's report cannot be rendered ({type(e).__name__})") from e
    return ImportedRun(run_id=run_id, run_dir=run_dir, manifest=manifest, dropped=dropped)


def _raise_server_error(error: BaseException | None) -> None:
    if isinstance(error, OSError) and error.errno in _SERVER_ERRNOS:
        raise error


def _extract(zip_path: Path, staging: Path) -> None:
    try:
        extract_bundle(zip_path, staging)
    except BundleError as e:
        _raise_server_error(e.__cause__)
        raise
    # An encrypted entry, an unknown compression method, or a corrupt stream, none of which
    # `extract_bundle` converts.
    except (RuntimeError, NotImplementedError, zlib.error) as e:
        raise BundleError(f"could not extract the zip ({type(e).__name__})") from e


def _check_all_artifact_paths(
    run_dir: Path, manifest: dict[str, Any], files: frozenset[str]
) -> None:
    # Checked against every entry the zip holds, before the type filter, so an artifact the filter
    # drops is a dead link in the report rather than a rejection.
    try:
        _check_artifact_paths(manifest, files)
        for result in run_dir.rglob("result.json"):
            rel = result.relative_to(run_dir)
            # A `<sid>/result.json` names its files from the directory holding `<sid>`, which is
            # the run itself, or `<engine>/` in a `--browsers` run (BE-0076).
            base = "".join(f"{part}/" for part in rel.parts[:-2])
            if result.stat().st_size > MAX_MANIFEST_BYTES:
                raise BundleError(f"the run is too large: {rel.as_posix()} is over the size limit")
            _check_artifact_paths(_read_json(result, rel.as_posix()), files, base=base)
    except RecursionError as e:
        raise BundleError("the run's JSON is nested too deeply") from e


def _check_entries(zip_path: Path) -> tuple[str, frozenset[str]]:
    """The run id and the run-relative file paths, from the entry list alone, before extraction."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as e:
        raise BundleError(f"not a valid zip archive: {e}") from e
    if len(infos) > MAX_ENTRIES:
        raise BundleError(f"too many entries ({len(infos)} > {MAX_ENTRIES})")
    # Said in the run's terms: a large run of logs and JSON crosses this without being a zip bomb.
    if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES:
        raise BundleError(f"the run is too large: over {MAX_TOTAL_BYTES} bytes uncompressed")
    tops: set[str] = set()
    files: set[str] = set()
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if "\\" in name or name.startswith("/") or ".." in parts or not parts:
            raise BundleError(f"unsafe entry path: {name!r}")
        if any(part.casefold() == RUN_TOMBSTONE for part in parts):
            raise BundleError(f"a {RUN_TOMBSTONE} entry marks a trashed run: {name!r}")
        if len(parts) == 1 and not info.is_dir():
            raise BundleError(f"every file must sit under the run's directory: {name!r}")
        tops.add(parts[0])
        if not info.is_dir():
            files.add(PurePosixPath(*parts[1:]).as_posix())
    if len(tops) != 1:
        raise BundleError("the zip must hold exactly one top-level directory, named for the run id")
    run_id = tops.pop()
    if not valid_run_id(run_id):
        raise BundleError(f"not a run id: {run_id!r}")
    return run_id, frozenset(files)


def _read_json(path: Path, shown: str) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError) as e:
        raise BundleError(f"{shown} is missing or is not readable JSON") from e


def _read_manifest(run_dir: Path, run_id: str) -> dict[str, Any]:
    manifest = _read_json(run_dir / "manifest.json", "manifest.json")
    if not (
        isinstance(manifest, dict)
        and isinstance(manifest.get("ok"), bool)
        and isinstance(manifest.get("scenarios"), list)
    ):
        raise BundleError("manifest.json needs a boolean `ok` and a list `scenarios`")
    if "runId" in manifest and manifest["runId"] != run_id:
        raise BundleError("manifest.json names a different run than its directory")
    return manifest


def _refuse_yaml_aliases(path: Path) -> None:
    """Refuse an alias before anything loads the file: a few hundred bytes of nested aliases expand
    to gigabytes once validated or re-dumped. A run never writes one."""
    try:
        events = yaml.parse(path.read_bytes(), Loader=yaml.SafeLoader)
        aliased = any(isinstance(event, yaml.AliasEvent) for event in events)
    except (yaml.YAMLError, ValueError, RecursionError) as e:
        raise BundleError("scenario.yaml is not readable YAML") from e
    if aliased:
        raise BundleError("scenario.yaml may not use YAML aliases")


def _linked_paths(node: Any) -> Iterator[Any]:
    """Every value under *node* that the report puts into an `href` or a `src`.

    Found by key at any depth, so a list of artifacts nested somewhere new is checked too: each
    `artifacts[].name`, a visual assertion's three images, and a hook command's `log`.
    """
    if isinstance(node, list):
        for item in node:
            yield from _linked_paths(item)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "artifacts" and isinstance(value, list):
            yield from (a.get("name") if isinstance(a, dict) else a for a in value)
        elif key == "visual" and isinstance(value, dict):
            yield from (
                value[k] for k in ("actual", "baseline", "diff") if value.get(k) is not None
            )
        elif key == "log" and value is not None:
            yield value
        yield from _linked_paths(value)


def _check_artifact_paths(document: Any, files: frozenset[str], *, base: str = "") -> None:
    for path in _linked_paths(document):
        if not (
            isinstance(path, str)
            # A browser strips these before it reads a URL, which would hide a scheme.
            and path.isprintable()
            and path == path.strip()
            and "\\" not in path
            and not path.startswith("/")
            and not _SCHEME.match(path)
            and ".." not in path.split("/")
        ):
            raise BundleError(f"not a relative artifact path: {path!r}")
        if base + path not in files:
            raise BundleError(f"an artifact the zip does not hold: {path!r}")


def _artifacts(node: Any) -> Iterator[dict[str, Any]]:
    """Every entry of every `artifacts` list under *node*."""
    if isinstance(node, list):
        for item in node:
            yield from _artifacts(item)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "artifacts" and isinstance(value, list):
                yield from (a for a in value if isinstance(a, dict))
            yield from _artifacts(value)


def _embedded_sizes(run_dir: Path, manifest: dict[str, Any]) -> Iterator[int]:
    """The size of everything the report embeds, once for each time it embeds it."""
    # Called after the path check and the type filter, so a name is a file of this run, or one the
    # filter dropped and the report cannot read.
    for artifact in _artifacts(manifest):
        kind, name = artifact.get("kind"), artifact.get("name")
        # A kind that is no string is nothing the report knows, so it counts like any other.
        if not isinstance(name, str) or (isinstance(kind, str) and kind in _LINKED_ONLY_KINDS):
            continue
        if (run_dir / name).is_file():
            yield (run_dir / name).stat().st_size
    model = load_run(run_dir)
    plans = [
        len(source or "") + len(json.dumps(definition, default=str))
        for source, definition in zip(model.sources, model.definitions, strict=True)
    ]
    # The renderer gives result `i` the plan at `i % len(plans)` (`html_report`).
    for i in range(len(model.results) if plans else 0):
        yield plans[i % len(plans)]


def _check_render_size(run_dir: Path, manifest: dict[str, Any]) -> None:
    total = 0
    for size in _embedded_sizes(run_dir, manifest):
        total += size
        if total > MAX_EMBEDDED_BYTES:
            raise BundleError(
                f"the run is too large: its report would embed over {MAX_EMBEDDED_BYTES} bytes"
            )


def _drop_active_files(run_dir: Path) -> list[str]:
    """Delete the report files to be regenerated and every non-passive file, listing the latter."""
    for name in _REGENERATED:
        if (run_dir / name).is_file():
            (run_dir / name).unlink()
    dropped: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(run_dir):
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in _PASSIVE_SUFFIXES:
                dropped.append(path.relative_to(run_dir).as_posix())
                path.unlink()
    return sorted(dropped)


def _mark_imported(run_dir: Path, manifest: dict[str, Any]) -> None:
    existing = manifest.get("provenance")
    manifest["provenance"] = {
        **(existing if isinstance(existing, dict) else {}),
        "source": "import",
    }
    # The producing machine redacted its text as it wrote it (BE-0331), and serve holds none of the
    # run's secret values, so only the sink's pattern backstop applies here.
    RunArtifactWriter(run_dir, Redactor(None)).write_json("manifest.json", manifest)
