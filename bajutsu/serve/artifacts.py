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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bajutsu.report.archive import archive_run_dir
from bajutsu.report.load import rerender_html
from bajutsu.serve.helpers import list_runs, valid_run_id


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

    def render_report(self, run_id: str) -> Artifact | None:
        """Render *run_id*'s report.html **on view** from its stored model with the current template
        (BE-0068), or None if the run is missing / has no manifest. Returning fresh HTML means an
        upgraded serve refreshes every report with no per-run re-bake; the baked file is a cache."""

    def archive(self, run_id: str) -> Artifact | None:
        """A zip of the whole run *run_id* (rooted under `<run_id>/`), or None if it's missing
        or *run_id* escapes the run tree — the download/export half of the report (BE-0060)."""


class LocalArtifactStore:
    """Reads run artifacts from the filesystem, confined to ``runs_dir`` — the default for serve."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir

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
        return target if base in target.parents else None

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

    def archive(self, run_id: str) -> Artifact | None:
        # A run is a single id segment, so reject `r1/demo` (a subdir) — it would otherwise zip the
        # subtree rather than a whole run, diverging from how run ids are used elsewhere in serve.
        if not valid_run_id(run_id):
            return None
        target = self._confined_dir(run_id)
        if target is None:  # missing or escapes runs_dir
            return None
        return Artifact(content_type="application/zip", body=archive_run_dir(target))
