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

from bajutsu.serve.helpers import list_runs


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


class LocalArtifactStore:
    """Reads run artifacts from the filesystem, confined to ``runs_dir`` — the default for serve."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir

    def _confined(self, rel: str) -> Path | None:
        """Resolve *rel* under ``runs_dir`` to an existing file, or None if it escapes / is absent."""
        base = self._runs_dir.resolve()
        target = (self._runs_dir / rel).resolve()
        if base not in target.parents or not target.is_file():
            return None
        return target

    def open_bytes(self, rel: str) -> bytes | None:
        target = self._confined(rel)
        return target.read_bytes() if target is not None else None

    def get(self, rel: str) -> Artifact | None:
        target = self._confined(rel)
        if target is None:
            return None
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Artifact(content_type=ctype, body=target.read_bytes())

    def list_runs(self) -> list[dict[str, Any]]:
        return list_runs(self._runs_dir)
