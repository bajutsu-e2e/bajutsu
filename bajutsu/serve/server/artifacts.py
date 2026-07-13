"""An object-storage ArtifactStore for the hosted backend (BE-0015 server phase).

`LocalArtifactStore` reads run artifacts from the filesystem. `ObjectStorageArtifactStore` keeps
the same `ArtifactStore` contract but serves them from S3-compatible object storage (Cloudflare R2,
MinIO, …): `get` hands back a **signed-URL redirect** (`Artifact.redirect`, which the handler
already 302s to) instead of inlining bytes, `open_bytes` fetches an object (e.g. a visual baseline
for Approve), and `list_runs` summarizes the runs from their stored ``manifest.json`` objects.

The object-store client is **injected** (the `ObjectStore` slice from `object_store`), so this
module imports no S3 SDK: it's unit-tested with an in-memory fake, the gate needs no boto3/bucket,
and the default path stays server-free (#117 import guard). Endpoint/credentials (R2 vs MinIO) live
in the injected client, so the same store serves either — what BE-0016 Tier B needs.
"""

from __future__ import annotations

import json
import mimetypes
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from bajutsu.report.archive import zip_tree
from bajutsu.serve.artifacts import Artifact
from bajutsu.serve.helpers import crawl_run_summary, valid_run_id
from bajutsu.serve.server.object_store import ObjectStore

# A soft-deleted run is marked by a tombstone object at ``<run_id>/.deleted`` whose body is the
# deletion time (ISO-8601). Object storage has no filesystem `.trash/` to move a tree into, so the
# tombstone is the store-side "this run is trashed" signal `list_runs`/`list_crawl_runs` skip on and
# the retention sweep reads a timestamp from (BE-0239) — the mirror of `LocalArtifactStore`'s trash.
_TOMBSTONE = ".deleted"


class ObjectStorageArtifactStore:
    """Serves run artifacts from object storage via signed-URL redirects (the ArtifactStore seam).

    *prefix* is prepended to every run-relative path (e.g. a tenant prefix ``artifacts/<org>/``),
    so one bucket can hold many projects' runs.
    """

    def __init__(self, store: ObjectStore, *, prefix: str = "") -> None:
        self._store = store
        self._prefix = prefix

    def _key(self, rel: str) -> str | None:
        """The object key for run-relative *rel*, or None if *rel* escapes the run tree.

        Matches the seam's containment (like `LocalArtifactStore`): an empty, absolute, or
        ``..``-traversing *rel* is treated as missing, so a client can't coax a signed redirect to
        a key outside the prefix."""
        if not rel or rel.startswith("/") or ".." in PurePosixPath(rel).parts:
            return None
        return self._prefix + rel

    def get(self, rel: str) -> Artifact | None:
        key = self._key(rel)
        if key is None or not self._store.exists(key):
            return None
        ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        return Artifact(content_type=ctype, redirect=self._store.presigned_url(key))

    def open_bytes(self, rel: str) -> bytes | None:
        key = self._key(rel)
        return self._store.get_bytes(key) if key is not None else None

    def render_report(self, run_id: str) -> Artifact | None:
        # Render-on-view (BE-0068) is a filesystem path for now: it loads the run dir's model with
        # the local renderer. The object store keeps the run in storage, not on disk, so serve the
        # baked report.html (via `get`) here — dynamic render on the hosted backend is BE-0015 scope.
        return None

    def archive(self, run_id: str) -> Artifact | None:
        # Zip the run's objects on read (the same confinement as `_key`); entries are rooted under
        # `<run_id>/` exactly like the filesystem store, so the two surfaces produce the same zip.
        # A run is a single id segment, so `r1/demo` (a nested prefix) is rejected, not zipped.
        if not valid_run_id(run_id):
            return None
        prefix = self._key(f"{run_id}/")
        if prefix is None:
            return None
        files = [
            (key[len(self._prefix) :], data)
            for key in self._store.list_keys(prefix)
            if (data := self._store.get_bytes(key)) is not None
        ]
        if not files:
            return None
        return Artifact(content_type="application/zip", body=zip_tree(files))

    def list_runs(self) -> list[dict[str, Any]]:
        rels = [k[len(self._prefix) :] for k in self._store.list_keys(self._prefix)]
        present = set(rels)
        deleted = self._tombstoned(rels)  # soft-deleted runs drop out of the history (BE-0239)
        run_ids = sorted(
            {r.split("/")[0] for r in rels if "/" in r} - deleted, reverse=True
        )  # newest first
        out: list[dict[str, Any]] = []
        for run_id in run_ids:
            raw = self._store.get_bytes(f"{self._prefix}{run_id}/manifest.json")
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):  # a manifest that isn't a JSON object is skipped
                continue
            scenarios = [s for s in (data.get("scenarios") or []) if isinstance(s, dict)]
            out.append(
                {
                    "id": run_id,
                    "ok": bool(data.get("ok")),
                    "report": f"{run_id}/report.html" in present,
                    "scenarios": [str(s.get("scenario", "")) for s in scenarios],
                    "passed": sum(1 for s in scenarios if s.get("ok")),
                    "total": len(scenarios),
                }
            )
        return out

    def list_crawl_runs(self) -> list[dict[str, Any]]:
        # The crawl-history counterpart to list_runs, keyed on screenmap.json (BE-0190). The prefix is
        # this store's org prefix, so `list_keys` only ever returns this org's keys — the listing is
        # tenant-safe by construction, and no run id from another org is reachable.
        rels = [k[len(self._prefix) :] for k in self._store.list_keys(self._prefix)]
        # One pass over the keys (like list_runs' single `present` scan): collect the runs that
        # streamed a screen map, and index each run's direct crashes/*.yaml and flows/*.yaml names —
        # a nested key or a non-yaml is excluded, matching the local scan's glob (no dead links).
        crawl_ids: set[str] = set()
        deleted = self._tombstoned(rels)  # soft-deleted crawl runs drop out too (BE-0239)
        files: dict[tuple[str, str], list[str]] = {}
        for rel in rels:
            run_id, _, rest = rel.partition("/")
            if not valid_run_id(run_id) or run_id in deleted:
                continue  # a corrupt/hostile key (`../screenmap.json`, empty segment) never lists
            if rest == "screenmap.json":
                crawl_ids.add(run_id)
                continue
            sub, _, name = rest.partition("/")
            if sub in ("crashes", "flows") and name.endswith(".yaml") and "/" not in name:
                files.setdefault((run_id, sub), []).append(name)
        out: list[dict[str, Any]] = []
        for run_id in sorted(crawl_ids, reverse=True):  # ids are timestamps → newest first
            raw = self._store.get_bytes(f"{self._prefix}{run_id}/screenmap.json")
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):  # a screen map that isn't a JSON object is skipped
                continue
            out.append(
                crawl_run_summary(
                    run_id,
                    data,
                    sorted(files.get((run_id, "crashes"), [])),
                    sorted(files.get((run_id, "flows"), [])),
                )
            )
        return out

    def _tombstoned(self, rels: list[str]) -> set[str]:
        """The run ids soft-deleted (a ``<id>/.deleted`` tombstone present) among *rels* (BE-0239)."""
        return {
            run_id
            for rel in rels
            if (run_id := rel.split("/")[0])
            and valid_run_id(run_id)
            and rel == f"{run_id}/{_TOMBSTONE}"
        }

    def _tombstone_key(self, run_id: str) -> str | None:
        """The tombstone object key for *run_id*, or None if *run_id* isn't a single safe segment."""
        return None if not valid_run_id(run_id) else self._key(f"{run_id}/{_TOMBSTONE}")

    def soft_delete_run(self, run_id: str) -> bool:
        key = self._tombstone_key(run_id)
        if key is None or self._store.exists(key):  # bad id, or already trashed
            return False
        # A run exists iff it has at least one object under its prefix; refuse to tombstone a run id
        # with no bytes (a typo), matching `LocalArtifactStore` refusing an absent directory.
        prefix = self._key(f"{run_id}/")
        if prefix is None or not self._store.list_keys(prefix):
            return False
        self._store.put_bytes(
            key, datetime.now(UTC).isoformat().encode(), content_type="text/plain"
        )
        return True

    def restore_run(self, run_id: str) -> bool:
        key = self._tombstone_key(run_id)
        if key is None or not self._store.exists(key):
            return False
        self._store.delete_key(key)
        return True

    def purge_run(self, run_id: str) -> bool:
        if not valid_run_id(run_id):
            return False
        prefix = self._key(f"{run_id}/")
        if prefix is None:
            return False
        keys = self._store.list_keys(prefix)  # every object under the run — evidence + tombstone
        if not keys:
            return False
        self._store.delete_keys(keys)
        return True

    def list_trashed_runs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key in self._store.list_keys(self._prefix):
            rel = key[len(self._prefix) :]
            run_id = rel.split("/")[0]
            if not valid_run_id(run_id) or rel != f"{run_id}/{_TOMBSTONE}":
                continue
            raw = self._store.get_bytes(key)
            deleted_at = raw.decode(errors="replace") if raw else None
            out.append({"id": run_id, "deletedAt": deleted_at})
        out.sort(key=lambda r: r["deletedAt"] or "", reverse=True)
        return out
