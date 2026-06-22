"""Bundle a finished run into a single portable zip (BE-0060).

A run's directory (`report.html`, `manifest.json`, `junit.xml`, the executed scenario, and the
evidence tree) is complete and self-describing, but `report.html` references its evidence by
*relative* link, so only the whole directory — laid out unchanged — is a working report. This
packages exactly that: every file under `runs/<id>/`, rooted under a single `<id>/` folder, so
unzipping yields `<id>/report.html` with its links resolving offline.

Pure packaging: no device, no AI, no effect on the verdict. The archive carries strictly what is
already on disk (so it inherits the run's secret-scrubbing) and never reaches outside the run dir.
The directory walk is sorted and entry timestamps are pinned, so the same run dir yields a
reproducible zip — a nicety, not a contract.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from pathlib import Path

# A fixed timestamp for every entry so the same tree zips to identical bytes (zip stores mtimes,
# which would otherwise vary run to run). The value is arbitrary; only its constancy matters.
_PINNED_TIME = (1980, 1, 1, 0, 0, 0)


def zip_tree(files: Iterable[tuple[str, bytes]]) -> bytes:
    """Build a deterministic zip from `(entry_name, content)` pairs.

    Entries are written in sorted name order with a pinned timestamp, so the same set of files
    yields byte-identical output regardless of input order. The shared zip builder behind the
    filesystem and object-store archivers."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in sorted(files, key=lambda f: f[0]):
            info = zipfile.ZipInfo(name, date_time=_PINNED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content)
    return buffer.getvalue()


def archive_run_dir(run_dir: Path) -> bytes:
    """Zip a run directory's whole tree, rooted under its `<id>/` folder.

    Walks strictly inside `run_dir` (never above it), so a sibling `.env` or another run is never
    pulled in. Entry names are `<run_dir.name>/<path-relative-to-run_dir>`, preserving the layout
    `report.html`'s relative links depend on."""
    root = run_dir.name
    # Skip symlinks: `is_file()` follows them, so a symlink in the run dir pointing outside would
    # otherwise read its target into the zip — escaping the run dir. (rglob's `**` does not recurse
    # symlinked directories.) A real run never contains symlinks, so this only ever drops an escape.
    files = (
        (f"{root}/{path.relative_to(run_dir).as_posix()}", path.read_bytes())
        for path in run_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    return zip_tree(files)
