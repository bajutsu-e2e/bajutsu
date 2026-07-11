"""The project registry seam (BE-0225 unit 2): list / get / add / remove / resolve-active over the
set of configs a `serve` deployment holds, plus the per-project run partition the sibling
cross-project dashboard (BE-0226) reads.

Two backends sit behind one `ProjectRegistry` accessor, wired in `_build_server_state` by whether a
`Repository` is present:

- `SqlProjectRegistry` delegates to the DB `Repository` (BE-0225 unit 1) and partitions runs by the
  `runs.project_id` column; the active project is a per-process choice held in memory.
- `LocalProjectRegistry` is the no-database default: a single JSON file beside serve's run directory,
  holding the project list, the active project, and a project→run-ids index — the local equivalent
  of the `project_id` column so a per-project run listing stays a lookup, not a scan.

Deregistering a project keeps its runs, matching the DB's ``ON DELETE SET NULL`` (unit 1): the runs
stay on disk, they just lose their project label.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from bajutsu.serve.server.db import ProjectRecord

if TYPE_CHECKING:
    # Annotation-only: keep it off the default serve/CLI import path (as state.py does for the same
    # type). ProjectRecord stays a real import — it is instantiated at runtime here.
    from bajutsu.serve.server.db import Repository

logger = logging.getLogger(__name__)


class ProjectRegistry(Protocol):
    """Reads and writes the projects a `serve` deployment holds, and partitions runs by project.

    `add` and `remove` are keyed by ``(org_id, name)`` — the human-facing identity — so callers
    never handle the generated id. `resolve_active` answers "which project owns a run started
    now?"; the launch config auto-registers as the active project (wired in `_build_server_state`).
    """

    def list_projects(self, *, org_id: str) -> list[ProjectRecord]:
        """The org's registered projects, ordered by name."""
        ...

    def get(self, *, org_id: str, name: str) -> ProjectRecord | None:
        """The org's project named *name*, or None if there is none."""
        ...

    def add(self, *, org_id: str, name: str, source: dict[str, object] | None) -> ProjectRecord:
        """Register ``(org_id, name)`` bound to *source*, or rebind *source* if it already exists.

        Idempotent by name: re-adding an existing name reuses its id and updates its source rather
        than duplicating or colliding, so a caller need not resolve the id first.
        """
        ...

    def remove(self, *, org_id: str, name: str) -> None:
        """Deregister the org's project named *name*; its runs are retained, just unlabeled."""
        ...

    def resolve_active(self, *, org_id: str) -> ProjectRecord | None:
        """The org's active project (the one owning newly started runs), or None if none is set."""
        ...

    def set_active(self, *, org_id: str, name: str) -> None:
        """Make the org's project named *name* active. A no-op target that does not exist is ignored."""
        ...

    def tag_run(self, *, org_id: str, project_id: str, run_id: str) -> None:
        """Associate *run_id* with *project_id* so a per-project run listing can find it."""
        ...

    def run_ids(self, *, org_id: str, project_id: str) -> list[str]:
        """The ids of *project_id*'s runs, newest first."""
        ...


def _new_id() -> str:
    return uuid.uuid4().hex


class _Store(TypedDict):
    """The on-disk JSON shape: the org→projects list, the org→active-name map, and the
    project→run-ids index (the local stand-in for the `runs.project_id` column)."""

    projects: dict[str, list[dict[str, Any]]]
    active: dict[str, str]
    run_ids: dict[str, list[str]]


class LocalProjectRegistry:
    """A `ProjectRegistry` backed by a single JSON file — the no-database local-serve shape.

    The file is a sibling of serve's run directory. Writes are atomic (temp file + ``os.replace``)
    so a crash mid-write never leaves a half-written store, mirroring `LocalProviderSettingsStore`.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data = self._load()

    def list_projects(self, *, org_id: str) -> list[ProjectRecord]:
        projects = [_from_json(p) for p in self._org(org_id)]
        return sorted(projects, key=lambda p: p.name)

    def get(self, *, org_id: str, name: str) -> ProjectRecord | None:
        return next((p for p in self.list_projects(org_id=org_id) if p.name == name), None)

    def add(self, *, org_id: str, name: str, source: dict[str, object] | None) -> ProjectRecord:
        rows = self._org(org_id)
        existing = next((r for r in rows if r["name"] == name), None)
        if existing is not None:
            # Only rebind when a source is supplied: a rename-only re-add (source=None) must not wipe
            # the stored binding, matching the DB backend's create_project no-clobber guard so the
            # two registries behind this seam agree on the same input.
            if source is not None:
                existing["source"] = source
            record = _from_json(existing)
        else:
            record = ProjectRecord(
                id=_new_id(), org_id=org_id, name=name, source=source, created_at=datetime.now(UTC)
            )
            rows.append(_to_json(record))
        self._save()
        return record

    def remove(self, *, org_id: str, name: str) -> None:
        target = self.get(org_id=org_id, name=name)
        self._data["projects"][org_id] = [r for r in self._org(org_id) if r["name"] != name]
        active = self._data["active"]
        if active.get(org_id) == name:
            del active[org_id]
        # Drop the project's run index so a per-project listing no longer surfaces the runs — the
        # local stand-in for the DB path's ON DELETE SET NULL. The runs themselves stay on disk.
        if target is not None:
            self._data["run_ids"].pop(target.id, None)
        self._save()

    def resolve_active(self, *, org_id: str) -> ProjectRecord | None:
        name = self._data["active"].get(org_id)
        return self.get(org_id=org_id, name=name) if name is not None else None

    def set_active(self, *, org_id: str, name: str) -> None:
        if self.get(org_id=org_id, name=name) is None:
            return
        self._data["active"][org_id] = name
        self._save()

    def tag_run(self, *, org_id: str, project_id: str, run_id: str) -> None:
        # Scope by org like the DB backend (whose list_runs filters org_id AND project_id): a run is
        # tagged only when the project actually belongs to this org, so a mismatched pair is a no-op
        # rather than tagging under a foreign org — parity ahead of unit 3 threading real orgs.
        if not self._project_in_org(org_id, project_id):
            return
        index = self._data["run_ids"].setdefault(project_id, [])
        # Front-insert, dropping any prior entry, so the list stays newest-first and idempotent.
        if run_id in index:
            index.remove(run_id)
        index.insert(0, run_id)
        self._save()

    def run_ids(self, *, org_id: str, project_id: str) -> list[str]:
        if not self._project_in_org(org_id, project_id):
            return []
        return list(self._data["run_ids"].get(project_id, []))

    def _org(self, org_id: str) -> list[dict[str, Any]]:
        return self._data["projects"].setdefault(org_id, [])

    def _project_in_org(self, org_id: str, project_id: str) -> bool:
        """Whether *project_id* is one of *org_id*'s projects — the local stand-in for the DB's
        ``WHERE org_id AND project_id``, since the run index is keyed by the (globally unique) id
        alone."""
        return any(r["id"] == project_id for r in self._org(org_id))

    def _load(self) -> _Store:
        empty: _Store = {"projects": {}, "active": {}, "run_ids": {}}
        if not self._path.exists():
            return empty
        # A malformed store falls back to empty rather than crashing the serve boot — the registry is
        # a convenience over the run tree, not the system of record, so a corrupt file must not wedge
        # startup (determinism-first: the operator re-registers rather than serve refusing to start).
        # Loud, not silent (as the sibling provider-settings store): log it, or the hub is wiped
        # quietly on every subsequent boot with no clue why.
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("ignoring the malformed project store %s", self._path, exc_info=True)
            return empty
        if not isinstance(raw, dict):
            logger.warning("ignoring the malformed project store %s: not a JSON object", self._path)
            return empty
        return {
            "projects": raw.get("projects") or {},
            "active": raw.get("active") or {},
            "run_ids": raw.get("run_ids") or {},
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name per call (mkstemp): serve is threaded, so a fixed suffix would let two
        # concurrent saves clobber each other before either os.replace lands (as LocalProviderSettingsStore).
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self._data, indent=2))
            os.replace(tmp_name, self._path)
        except OSError:
            os.unlink(tmp_name)
            raise


def _to_json(record: ProjectRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "org_id": record.org_id,
        "name": record.name,
        "source": record.source,
        "created_at": record.created_at.isoformat() if record.created_at is not None else None,
    }


def _from_json(row: dict[str, Any]) -> ProjectRecord:
    created = row.get("created_at")
    return ProjectRecord(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        name=str(row["name"]),
        source=row.get("source"),
        created_at=datetime.fromisoformat(created) if isinstance(created, str) else None,
    )


class SqlProjectRegistry:
    """A `ProjectRegistry` backed by the DB `Repository` (BE-0225 unit 1).

    The project list and run partition live in the `projects` / `runs` tables; the active project is
    a per-serve-process choice held in memory (there is no active-project column — "active" is a
    session notion, not durable state), initialized by auto-registering the launch config.
    """

    def __init__(self, repository: Repository) -> None:
        self._repo = repository
        self._active: dict[str, str] = {}

    def list_projects(self, *, org_id: str) -> list[ProjectRecord]:
        return self._repo.list_projects(org_id=org_id)

    def get(self, *, org_id: str, name: str) -> ProjectRecord | None:
        return self._repo.get_project(org_id=org_id, name=name)

    def add(self, *, org_id: str, name: str, source: dict[str, object] | None) -> ProjectRecord:
        # Resolve an existing (org, name) to its id first: create_project merges by id, so a fresh id
        # for an existing name would trip the (org_id, name) unique constraint (unit 1's contract).
        existing = self._repo.get_project(org_id=org_id, name=name)
        project_id = existing.id if existing is not None else _new_id()
        self._repo.create_project(
            ProjectRecord(id=project_id, org_id=org_id, name=name, source=source)
        )
        added = self._repo.get_project(org_id=org_id, name=name)
        assert added is not None  # just written
        return added

    def remove(self, *, org_id: str, name: str) -> None:
        self._repo.delete_project(org_id=org_id, name=name)
        if self._active.get(org_id) == name:
            del self._active[org_id]

    def resolve_active(self, *, org_id: str) -> ProjectRecord | None:
        name = self._active.get(org_id)
        return self._repo.get_project(org_id=org_id, name=name) if name is not None else None

    def set_active(self, *, org_id: str, name: str) -> None:
        if self._repo.get_project(org_id=org_id, name=name) is None:
            return
        self._active[org_id] = name

    def tag_run(self, *, org_id: str, project_id: str, run_id: str) -> None:
        # No-op: the DB path stamps runs.project_id when the run row is recorded (see jobs._persist_run),
        # so the partition is the column itself — there is no separate index to maintain here.
        return

    def run_ids(self, *, org_id: str, project_id: str) -> list[str]:
        # limit=None: the seam promises *all* of a project's runs (matching LocalProjectRegistry's
        # unbounded index), not list_runs' default 50-run page.
        return [
            r.id for r in self._repo.list_runs(org_id=org_id, project_id=project_id, limit=None)
        ]
