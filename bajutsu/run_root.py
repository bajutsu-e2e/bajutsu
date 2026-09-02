"""The run directory's write provider (BE-0331).

One function derives the filesystem root of a run directory, and only the run-artifact sink may
reach it. That restriction is what makes the redaction boundary enforceable rather than advisory: a
path into a run directory is writable, so a module that can derive one can call `write_text` on it
and reach the directory without crossing redaction. Withholding the derivation — not merely
reviewing each new writer — is what covers a writer nobody has written yet.

Two mechanical checks pin it down, both decidable from source rather than from a runtime path value:

- an import contract (`lint-imports`) forbidding every module but `bajutsu.common.evidence.sink` from
  importing this one, so the property holds over the whole import graph;
- a literal check failing a run-root path literal outside `bajutsu.run_files`, closing the remaining
  way to rebuild the path without importing the provider.

Reading is unrestricted and lives in `bajutsu.run_files`: `serve`, the evidence readers, `export`
and the comparison commands all need to read a run, and none of those operations can create an
artifact. So does the runs root's *name*, which is a string rather than a writable handle — a flag
default every command needs, and one the contract could not hand out from here without opening the
single allowed edge to every importer of that name.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.artifact_perms import make_run_dir


def run_dir_for_write(runs_dir: str | Path, run_id: str) -> Path:
    """Create and return the writable filesystem path of one run directory.

    The sole writable handle into a run directory. It creates the directory owner-only (BE-0131) so
    a caller never has to, and returns it only to the sink, which is the only importer the contract
    allows.
    """
    return make_run_dir(Path(runs_dir) / run_id)
