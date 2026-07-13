"""The ArtifactStore seam: how a run's artifacts are read back (BE-0015 local/server parity).

A run writes its tree under ``runs/<id>/`` (report.html, screenshots, manifest.json, …). Serving
those back is the one point where local and server hosting diverge: the local store reads files
**confined to ``runs_dir``** (`LocalArtifactStore`), while a server store would fetch from object
storage or hand back a signed-URL redirect. Keeping the path-containment in one place means a
crafted ``rel`` can never escape ``runs_dir``, and a server store gets the same guarantee by never
touching the filesystem at all.
"""

from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from bajutsu.report.archive import archive_run_dir
from bajutsu.report.load import rerender_html
from bajutsu.serve.helpers import list_crawl_runs, list_runs, valid_run_id

# Where a soft-deleted run's tree is parked, relative to ``runs_dir`` (BE-0239). A single hidden
# segment under ``runs_dir`` keeps trashed runs inside the same path-containment guarantee as live
# ones, and out of `list_runs`/`list_crawl_runs` (which scan only ``runs_dir``'s direct children).
_TRASH = ".trash"


@dataclass
class Artifact:
    content_type: str
    body: bytes | None = None  # inline bytes (local filesystem)
    redirect: str | None = None  # a signed URL to 302 to (object storage)


class ArtifactStore(Protocol):
    """Reads back the artifacts a run produced."""

    def get(self, rel: str) -> Artifact | None:
        """Serve run-relative path *rel*, or None if it's missing or escapes the run tree."""

    def open_bytes(self, rel: str) -> bytes | None:
        """Raw bytes for run-relative path *rel* (e.g. a visual baseline), or None."""

    def list_runs(self) -> list[dict[str, Any]]:
        """Past runs, newest first, each summarized for the history list."""

    def list_crawl_runs(self) -> list[dict[str, Any]]:
        """Past crawl runs, newest first, each summarized from its screenmap.json (BE-0180/BE-0190).

        The crawl-history counterpart to `list_runs`: keyed on screenmap.json (the artifact every
        crawl streams) instead of manifest.json (a crawl has no pass/fail verdict). Each entry is the
        `helpers.crawl_run_summary` shape the Crawl tab consumes."""

    def render_report(self, run_id: str) -> Artifact | None:
        """Render *run_id*'s report.html **on view** from its stored model with the current template
        (BE-0068). None when the report can't be rendered — the run is missing, has no manifest, the
        model can't be loaded (malformed manifest/scenario), or this store doesn't render on view —
        so the caller falls back to the baked file. Returning fresh HTML means an upgraded serve
        refreshes every report with no per-run re-bake; the baked file is then a cache/export."""

    def archive(self, run_id: str) -> Artifact | None:
        """A zip of the whole run *run_id* (rooted under `<run_id>/`), or None if it's missing
        or *run_id* escapes the run tree — the download/export half of the report (BE-0060)."""

    def soft_delete_run(self, run_id: str) -> bool:
        """Move *run_id* to the trash so it drops out of the history lists but stays restorable
        within the retention window (BE-0239). True when a live run was trashed, False when there
        was none (a bad/absent id). Not destructive — `purge_run` is the irreversible step."""

    def restore_run(self, run_id: str) -> bool:
        """Undo `soft_delete_run` for *run_id*, returning it to the history lists (BE-0239). True
        when a trashed run was restored, False when none was trashed (or a live run already holds
        the id)."""

    def purge_run(self, run_id: str) -> bool:
        """Permanently remove *run_id*'s bytes — trashed or live (the ``?purge=true`` immediate
        path) — the one irreversible step (BE-0239). True when anything was removed."""

    def list_trashed_runs(self) -> list[dict[str, Any]]:
        """Soft-deleted runs as ``{"id", "deletedAt"}`` (``deletedAt`` an ISO-8601 UTC string, or
        None if unknown), for the retention sweep (BE-0239). Newest-deleted first."""


class LocalArtifactStore:
    """Reads run artifacts from the filesystem, confined to ``runs_dir`` — the default for serve."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir
        self._trash_dir = runs_dir / _TRASH

    def _confined(self, rel: str) -> Path | None:
        """Resolve *rel* under ``runs_dir`` to an existing file, or None if it escapes / is absent."""
        target = self._resolve(rel)
        return target if target is not None and target.is_file() else None

    def _confined_dir(self, rel: str) -> Path | None:
        """Resolve *rel* under ``runs_dir`` to an existing directory, or None if it escapes / is absent."""
        target = self._resolve(rel)
        return target if target is not None and target.is_dir() else None

    def _resolve(self, rel: str) -> Path | None:
        """Resolve *rel* under ``runs_dir``, or None if it escapes the tree (containment in one place)."""
        base = self._runs_dir.resolve()
        target = (self._runs_dir / rel).resolve()
        if base not in target.parents:
            return None
        # A trashed run stays restorable but must not be reachable through `/runs/<rel>`: keep the
        # ``.trash/`` subtree out of every read path (get / archive / render), so soft-delete really
        # removes the run from view, not just from the listings (BE-0239).
        trash = self._trash_dir.resolve()
        return None if target == trash or trash in target.parents else target

    def open_bytes(self, rel: str) -> bytes | None:
        target = self._confined(rel)
        return target.read_bytes() if target is not None else None

    def get(self, rel: str) -> Artifact | None:
        target = self._confined(rel)
        if target is None:
            return None
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Artifact(content_type=ctype, body=target.read_bytes())

    def render_report(self, run_id: str) -> Artifact | None:
        # Render from the stored model (manifest.json + scenario.yaml) with the current template, so
        # serving the report reflects template/feature changes without a re-bake (BE-0068). A run is a
        # single id segment (reject `r1/sub`), confined to runs_dir; a dir with no manifest → None.
        if not valid_run_id(run_id):
            return None
        run_dir = self._confined_dir(run_id)
        if run_dir is None or not (run_dir / "manifest.json").is_file():
            return None
        try:
            html = rerender_html(run_dir)
        except (OSError, ValueError):
            return None  # a manifest with a missing/corrupt scenario.yaml — fall back to the baked file
        return Artifact(content_type="text/html", body=html.encode("utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        return list_runs(self._runs_dir)

    def list_crawl_runs(self) -> list[dict[str, Any]]:
        return list_crawl_runs(self._runs_dir)

    def archive(self, run_id: str) -> Artifact | None:
        # A run is a single id segment, so reject `r1/demo` (a subdir) — it would otherwise zip the
        # subtree rather than a whole run, diverging from how run ids are used elsewhere in serve.
        if not valid_run_id(run_id):
            return None
        target = self._confined_dir(run_id)
        if target is None:  # missing or escapes runs_dir
            return None
        return Artifact(content_type="application/zip", body=archive_run_dir(target))

    def soft_delete_run(self, run_id: str) -> bool:
        if not valid_run_id(run_id):  # a single safe segment; ``.trash`` itself never qualifies
            return False
        live = self._confined_dir(run_id)
        if live is None:  # no such live run (already trashed, or a bad id)
            return False
        self._trash_dir.mkdir(parents=True, exist_ok=True)
        trashed = self._trash_dir / run_id
        if trashed.exists():
            # A prior trashed copy under this id (restored, re-run, re-deleted): the newest delete
            # wins, so drop the stale copy rather than letting `move` nest it inside the old dir.
            shutil.rmtree(trashed, ignore_errors=True)
        shutil.move(str(live), str(trashed))
        return True

    def restore_run(self, run_id: str) -> bool:
        if not valid_run_id(run_id):
            return False
        trashed = self._trash_dir / run_id
        if not trashed.is_dir():
            return False
        live = self._runs_dir / run_id
        if live.exists():  # a live run already holds the id — don't clobber it
            return False
        shutil.move(str(trashed), str(live))
        return True

    def purge_run(self, run_id: str) -> bool:
        if not valid_run_id(run_id):
            return False
        # Remove whichever copies exist: the ``?purge=true`` path purges a live run outright, while
        # the retention sweep purges an already-trashed one — either way the bytes are gone.
        removed = False
        for path in (self._trash_dir / run_id, self._runs_dir / run_id):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed = True
        return removed

    def list_trashed_runs(self) -> list[dict[str, Any]]:
        if not self._trash_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for d in self._trash_dir.iterdir():
            if not (d.is_dir() and valid_run_id(d.name)):
                continue
            # The directory's mtime is when soft-delete moved it here — the retention clock's start.
            deleted_at = datetime.fromtimestamp(d.stat().st_mtime, tz=UTC).isoformat()
            out.append({"id": d.name, "deletedAt": deleted_at})
        out.sort(key=lambda r: r["deletedAt"], reverse=True)
        return out
