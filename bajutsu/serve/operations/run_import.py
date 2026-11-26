"""Store a run that a script uploaded, and record it in the run history (BE-0510).

The id is reserved in the history before any file is placed, because a run id has only second
granularity and the history keys it globally. The row stays ``importing``, which no history reader
lists, until every file is in place. Nothing here judges the run: the verdict is the one the
uploaded manifest asserts, and the stored provenance says it was imported.
"""

from __future__ import annotations

import errno
import logging
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from bajutsu.common.run_meta.object_store import ObjectStore, upload_tree
from bajutsu.serve import oplog
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.jobs import record_run_row
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.state import ServeState
from bajutsu.serve.uploads import BundleError
from bajutsu.serve.uploads.run_bundle import ImportedRun, unpack_run

_logger = logging.getLogger("bajutsu.serve.operations")

# A reservation, or a staging directory, this old belongs to an import that died.
_STALE_AFTER = timedelta(hours=1)

# The same body whichever organization holds the id, so the answer discloses nothing.
_TAKEN = ({"error": "a run with this id already exists"}, 409)
_NOT_STORED = ({"error": "could not store the run"}, 500)


class _Taken(Exception):
    """Another writer placed the run id first."""


class _Placement(Protocol):
    def taken(self, run_id: str) -> bool:
        """Whether *run_id* already holds anything, a trashed run included."""

    def place(self, run_dir: Path) -> None:
        """Put the run in place. All or nothing: a failure leaves nothing of this call behind,
        unless the cleanup fails too, which raises `_LeftBehind`."""

    def discard(self, run_id: str) -> None:
        """Remove every file of *run_id*."""


class _LeftBehind(Exception):
    """A failed placement whose partial files could not be removed."""


class _RunsTree:
    """The runs directory itself: placement is one rename."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs = runs_dir

    def taken(self, run_id: str) -> bool:
        return (self._runs / run_id).exists() or (self._runs / ".trash" / run_id).exists()

    def place(self, run_dir: Path) -> None:
        self._runs.mkdir(parents=True, exist_ok=True)
        target = self._runs / run_dir.name
        try:
            run_dir.rename(target)
        except OSError as e:
            # The runs directory can be a mounted volume of its own, apart from the staging beside it.
            if e.errno == errno.EXDEV:
                self._copy(run_dir, target)
                return
            # A rename onto a non-empty directory fails, so a concurrent writer is never overwritten.
            if target.exists():
                raise _Taken from e
            raise

    def _copy(self, run_dir: Path, target: Path) -> None:
        # Creating the directory claims the id. The manifest goes last, so a half-copied run never
        # reads as finished.
        try:
            target.mkdir()
        except FileExistsError as e:
            raise _Taken from e
        try:
            shutil.copytree(
                run_dir,
                target,
                ignore=lambda directory, _names: (
                    ["manifest.json"] if Path(directory) == run_dir else []
                ),
                dirs_exist_ok=True,
            )
            shutil.copyfile(run_dir / "manifest.json", target / "manifest.json")
        except Exception:
            try:
                self.discard(target.name)
            except Exception as e:
                raise _LeftBehind from e
            raise

    def discard(self, run_id: str) -> None:
        # Errors are raised, so a reservation is never freed while its files remain.
        if (self._runs / run_id).exists():
            shutil.rmtree(self._runs / run_id)


class _Objects:
    """The organization's artifact prefix in an object store."""

    def __init__(self, store: ObjectStore, prefix: str) -> None:
        self._store = store
        self._prefix = prefix

    def taken(self, run_id: str) -> bool:
        return bool(self._store.list_keys(f"{self._prefix}{run_id}/"))

    def place(self, run_dir: Path) -> None:
        # The manifest goes last, so a half-copied run never reads as finished.
        manifest = run_dir.parent / "manifest.json"
        (run_dir / "manifest.json").rename(manifest)
        try:
            self._upload(run_dir, manifest)
        except Exception:
            try:
                self.discard(run_dir.name)
            except Exception as e:
                raise _LeftBehind from e
            raise

    def _upload(self, run_dir: Path, manifest: Path) -> None:
        summary = upload_tree(self._store, run_dir, self._prefix)
        # `upload_tree` collects its failures instead of raising them.
        if summary.failures:
            key, error = summary.failures[0]
            raise OSError(f"{len(summary.failures)} uploads failed, first {key}: {error}")
        self._store.put_file(
            f"{self._prefix}{run_dir.name}/manifest.json",
            manifest,
            content_type="application/json",
        )

    def discard(self, run_id: str) -> None:
        self._store.delete_keys(self._store.list_keys(f"{self._prefix}{run_id}/"))


def _placement(state: ServeState, org: str) -> _Placement:
    """Where *org*'s runs live. A local start mounts the runs tree for the default org alone."""
    if state.object_store is None or (state.local_file_store and org == DEFAULT_ORG):
        return _RunsTree(state.runs_dir)
    # Lazy: only a server backend has an object store, and the import guard keeps `server` out of
    # the default serve path.
    from bajutsu.serve.server.object_store import artifact_prefix, org_prefix

    return _Objects(state.object_store, artifact_prefix(org_prefix(state.object_store_prefix, org)))


def _staging_root(state: ServeState) -> Path:
    # Beside the runs tree, so the final rename stays on one filesystem, and outside it, so nothing
    # under `/runs/` can serve a run that is not accepted yet.
    return state.runs_dir.parent / f".{state.runs_dir.name}-import"


def import_run(
    state: ServeState, zip_path: Path, *, actor: str | None, machine_org: str | None = None
) -> tuple[Any, int]:
    """Check the run zip at *zip_path*, store the run, and record it for the caller's organization.

    Returns:
        ``200`` with the run's id, verdict, report and archive paths, and the entries dropped for
        their type; ``400`` for a zip or run that breaks a rule; ``409`` when the run id is taken;
        ``500`` when storing it failed, with nothing left in the history.
    """
    org = state.org_for(actor, machine_org)
    root = _staging_root(state)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root, ignore_cleanup_errors=True) as staging:
            try:
                run = unpack_run(zip_path, Path(staging))
            except BundleError as e:
                return {"error": str(e)}, 400
            return _store_run(state, run, org=org, actor=actor)
    except OSError:
        # The server's own fault, such as a full disk: the operator needs the cause, the client
        # does not.
        _logger.warning("failed to stage an imported run under %s", root, exc_info=True)
        return _NOT_STORED


def _store_run(
    state: ServeState, run: ImportedRun, *, org: str, actor: str | None
) -> tuple[Any, int]:
    run_id = run.run_id
    ok = bool(run.manifest["ok"])
    repo = state.repository
    placement = _placement(state, org)
    # Before the collision checks: a leftover of a crashed import would otherwise fail every retry.
    _purge_stale_reservation(state, run_id, org)
    if placement.taken(run_id):
        return _TAKEN
    reserved_at = datetime.now(UTC)
    if repo is not None:
        # Only the insert closes the race between two concurrent imports.
        from bajutsu.serve.server.db import IMPORTING_STATUS

        if not record_run_row(
            repo,
            run_id=run_id,
            org=org,
            actor=actor,
            label=None,
            ok=ok,
            manifest=run.manifest,
            created_at=reserved_at,
            if_absent=True,
            status=IMPORTING_STATUS,
        ):
            return _TAKEN
    try:
        placement.place(run.run_dir)
    except _LeftBehind:
        # The reservation stays, so the stale purge removes both the files and the row later.
        _logger.warning("import of %s left files behind", run_id, exc_info=True)
        return _NOT_STORED
    except _Taken:
        _release(state, run_id, org, reserved_at)
        return _TAKEN
    except Exception:
        _logger.warning("failed to store imported run %s", run_id, exc_info=True)
        _release(state, run_id, org, reserved_at)
        return _NOT_STORED
    if repo is not None:
        try:
            finished = repo.finish_import(run_id, org_id=org, reserved_at=reserved_at)
        except Exception:
            # Whether the flip landed is unknown, so the files stay; a row still `importing` is
            # purged with them once stale.
            _logger.warning("failed to finish the import of %s", run_id, exc_info=True)
            return _NOT_STORED
        if not finished:
            _logger.warning("import of %s lost its reservation before it finished", run_id)
            if repo.get_run(run_id) is None:
                placement.discard(run_id)
            return _NOT_STORED
    try:
        _record_audit(state, actor, org, "run.import", run_id, {})
    except Exception:
        # The run is already listed; an error here would tell the caller to retry into a 409.
        _logger.error("imported run %s but could not audit it", run_id, exc_info=True)
    oplog.log_event(_logger, "run.imported", "run imported", run_id=run_id, org=org, actor=actor)
    return {
        "runId": run_id,
        "ok": ok,
        # Paths, not URLs: serve knows only its own origin, and the caller knows the address it used.
        "url": f"/runs/{run_id}/report.html",
        "archive": f"/runs/{run_id}/archive.zip",
        "dropped": run.dropped,
    }, 200


def _release(state: ServeState, run_id: str, org: str, reserved_at: datetime) -> None:
    """Free this request's own reservation. Logged rather than raised, so the caller's answer
    stands; a row left behind is purged once stale."""
    if state.repository is None:
        return
    try:
        state.repository.release_import(run_id, org_id=org, reserved_at=reserved_at)
    except Exception:
        _logger.warning("failed to release the reservation of %s", run_id, exc_info=True)


def _purge_stale_reservation(state: ServeState, run_id: str, org: str) -> None:
    """Free *run_id* when *org*'s own import of it died. `get_run` spans organizations, so another
    organization's row is checked for and left alone."""
    if state.repository is None:
        return
    from bajutsu.serve.server.db import IMPORTING_STATUS

    row = state.repository.get_run(run_id)
    if row is None or row.org_id != org or row.status != IMPORTING_STATUS or row.created_at is None:
        return
    created = row.created_at
    # SQLite hands the timestamp back without its zone; it was written in UTC.
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if datetime.now(UTC) - created < _STALE_AFTER:
        return
    _drop_reservation(state, run_id, org, created)


def _drop_reservation(state: ServeState, run_id: str, org: str, reserved_at: datetime) -> None:
    assert state.repository is not None  # both callers found the row in it
    # Claim before removing anything: of two purges of one row, only one may touch its files, or the
    # slower would delete what a retry has placed since.
    claimed_at = datetime.now(UTC)
    if not state.repository.claim_import(
        run_id, org_id=org, reserved_at=reserved_at, claimed_at=claimed_at
    ):
        return
    # Files before the row: one that outlives a failed removal is found again once stale.
    _placement(state, org).discard(run_id)
    state.repository.release_import(run_id, org_id=org, reserved_at=claimed_at)
    oplog.log_event(_logger, "run.import_purged", "stale run import purged", run_id=run_id, org=org)


def sweep_stale_imports(state: ServeState) -> None:
    """Clear what crashed imports left: every organization's stale reservations with their files,
    and old staging directories. Run at server start; one failure never stops the rest, nor the
    start."""
    cutoff = datetime.now(UTC) - _STALE_AFTER
    if state.repository is not None:
        try:
            rows = state.repository.list_stale_imports(before=cutoff)
        except Exception:
            _logger.warning("failed to list stale run imports", exc_info=True)
            rows = []
        for row in rows:
            try:
                assert row.created_at is not None  # the query selected on it
                _drop_reservation(state, row.id, row.org_id, row.created_at)
            except Exception:
                _logger.warning(
                    "failed to purge the stale import %s of org %s",
                    row.id,
                    row.org_id,
                    exc_info=True,
                )
    root = _staging_root(state)
    try:
        leftovers = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        _logger.warning("failed to list import staging directories in %s", root, exc_info=True)
        return
    for staging in leftovers:
        try:
            if staging.lstat().st_mtime < cutoff.timestamp():
                shutil.rmtree(staging)
        except OSError:
            _logger.warning("failed to remove the import staging %s", staging, exc_info=True)
