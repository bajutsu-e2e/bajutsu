"""Path-free reads of a run directory (BE-0331).

The boundary this item enforces governs writing alone, because the run directory's largest consumer
only reads it: `serve` lists runs and their artifacts, the evidence readers load a manifest, `export`
archives a tree, and the comparison commands read across runs. A contract that forbade every module
but the write sink from resolving a run path would break all of that, and this item's own rejection
of encryption rests on those files staying plainly readable.

So the run directory is reached through two providers, and only one of them writes. This is the
unrestricted half. It never returns a `Path` into a run: a path into a run directory is writable, so
handing one out would let any importer call `write_text` on it and reach the directory without the
sink — the import contract would hold on paper while the boundary leaked.

The *name* of the runs root belongs on this side by the same test. It is a string, not a handle: every
command needs it for a flag default, and knowing where runs land grants no more than knowing that they
land somewhere. Deriving one run's writable directory is the restricted operation, and that lives in
`bajutsu.run_root`, which only the sink may import. Naming the root here rather than repeating the
literal is what gives the literal check exactly one place to allow.
"""

from __future__ import annotations

from pathlib import Path

#: The directory runs land under by default, relative to the working directory. Every CLI flag
#: default and every worker/serve path references this rather than repeating the literal.
DEFAULT_RUNS_DIR = "runs"


def runs_root(configured: str | Path | None = None) -> Path:
    """The directory runs land under — the configured one, or the default."""
    return Path(configured) if configured else Path(DEFAULT_RUNS_DIR)


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
        """Artifact names matching a glob, relative to the run directory, in sorted order.

        Symlinks are skipped: `is_file()` follows them, so one planted in a run directory and
        pointing outside would otherwise be listed — and then read — as if it belonged to the run.
        A real run never contains any, so this only ever drops an escape.
        """
        root = self._dir
        return sorted(
            p.relative_to(root).as_posix()
            for p in root.glob(pattern)
            if p.is_file() and not p.is_symlink()
        )

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
