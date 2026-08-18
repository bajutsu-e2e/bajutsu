"""Path-free reads of a run directory (BE-0331).

The boundary this item enforces governs writing alone, because the run directory's largest consumer
only reads it: `serve` lists runs and their artifacts, the evidence readers load a manifest, `export`
archives a tree, and the comparison commands read across runs. A contract that forbade every module
but the write sink from resolving a run path would break all of that, and this item's own rejection
of encryption rests on those files staying plainly readable.

So the run directory is reached through two providers, and only one of them writes. This is the
unrestricted half. It never returns a `Path`: a path into a run directory is writable, so handing one
out would let any importer call `write_text` on it and reach the directory without the sink — the
import contract would hold on paper while the boundary leaked.
"""

from __future__ import annotations

from pathlib import Path


class RunArtifactReader:
    """Answers read questions about one run: which artifacts exist, and what they hold.

    None of these operations can create an artifact, so this side is not import-restricted.
    """

    def __init__(self, run_dir: Path) -> None:
        self._dir = run_dir

    @property
    def run_id(self) -> str:
        """The run's identifier — its directory name."""
        return self._dir.name

    def exists(self, name: str) -> bool:
        return self._resolve(name).exists()

    def names(self, pattern: str = "*") -> list[str]:
        """Artifact names matching a glob, relative to the run directory, in sorted order."""
        root = self._dir
        return sorted(str(p.relative_to(root)) for p in root.glob(pattern) if p.is_file())

    def read_text(self, name: str) -> str:
        return self._resolve(name).read_text(encoding="utf-8")

    def read_bytes(self, name: str) -> bytes:
        return self._resolve(name).read_bytes()

    def _resolve(self, name: str) -> Path:
        root = self._dir.resolve()
        path = (root / name).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"artifact name escapes the run directory: {name!r}")
        return path
