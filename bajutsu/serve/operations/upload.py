"""Config-bundle upload serve operations (BE-0073, split out in BE-0127).

BE-0243 adds durable, cross-replica resolution on top of the local extraction sandbox: when
`state.object_store` is configured (the hosted `server` backend), the raw zip is persisted
content-addressed by its sha256 so `activate_project` can fetch-and-extract it on any replica
instead of refusing an `upload`-kind project with a `409`. Local `serve` (no object store) is
unchanged — the bundle stays exactly as ephemeral as BE-0073 shipped it.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from bajutsu.config import load_config, resolve
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.helpers import list_targets
from bajutsu.serve.server.object_store import org_prefix, upload_prefix
from bajutsu.serve.state import ServeState
from bajutsu.serve.uploads import (
    BundleError,
    Upload,
    find_bundle_config,
    materialize_bundle,
)

# A full, lowercase hex sha256 digest — exactly what hashlib.sha256().hexdigest() produces, and the
# only shape `_upload_store_key`/`_org_uploads_dir` may safely turn into a path component or an
# object-store key. `bind_upload_config`'s own sha256 always matches (server-computed while
# streaming the upload); `activate_uploaded_project`'s comes from a stored project record a client
# can shape via `register_project` (BE-0225), so it is untrusted and must be checked before it ever
# reaches `uploads_dir / sha256` — an unchecked `../`-laden value would let a registered record walk
# a materialize call outside the cache root (mirrors the Git source's own `_FULL_SHA_RE` guard on a
# resolved commit SHA, `bajutsu/config_source.py`).
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_filename(name: str) -> str:
    """A display-safe basename for the uploaded zip (provenance only): strip any directory and
    non-printable characters, bound the length, and fall back to a default when nothing remains."""
    base = "".join(c for c in Path(name or "").name if c.isprintable()).strip()
    return (base or "bundle.zip")[:200]


def _validate_bundle_config(root: Path) -> None:
    """Confine every target's path fields to *root* and confirm it has a loadable config — the same
    guard the Git source applies to a fetched checkout (BE-0051): a config pointing
    appPath/scenarios/baselines at an absolute or `..` path outside the tree is rejected here, so
    serve's resolution only ever sees in-bundle paths. Raises `BundleError` (no config file) or a
    `load_config`/`rebased` failure (`OSError` / `ValueError` / `yaml.YAMLError`) — both are
    `ValueError` subclasses, so one `except ValueError` at the call site covers either."""
    config_path = find_bundle_config(root)
    if config_path is None:
        raise BundleError("has no bajutsu.config.yaml")
    cfg = load_config(config_path.read_text(encoding="utf-8"))
    for name in cfg.targets:
        resolve(cfg, name).rebased(config_path.parent)


def _upload_store_key(state: ServeState, org: str, sha256: str) -> str:
    """The object-store key a bundle's raw zip lives at for *org* (BE-0243): nested under the same
    per-org prefix every sibling store (artifacts/scenarios/baselines) already uses, so one org's
    upload can never dedupe against — or be resolved by — another org's identical-content upload."""
    return f"{upload_prefix(org_prefix(state.object_store_prefix, org))}{sha256}.zip"


def _org_uploads_dir(state: ServeState, org: str) -> Path:
    """*org*'s root for the local sha256-keyed extraction cache under `state.uploads_dir` (BE-0243).

    Mirrors `_upload_store_key`'s org-scoping with the same `org_prefix` helper: `state.uploads_dir`
    is one shared path across every org on the server backend (unlike the per-org object-store
    prefix), so without this an org could claim another org's `sha256` in a registered project's
    source record and cache-hit straight into that org's already-extracted tree, bypassing the
    object store's own org scoping entirely. `org_prefix("", "default")` is `""`, and
    `Path(...) / ""` is a no-op, so local (always-`default`-org) serve is unaffected."""
    return state.uploads_dir / org_prefix("", org)


def _materialize_or_error(zip_path: Path, uploads_dir: Path, sha256: str) -> tuple[Any, int]:
    """`materialize_bundle`, wrapped in the `(payload, status)` convention `bind_git_config`/
    `bind_config` already use: `(dest, 200)` on success, or an "invalid bundle" 400."""
    try:
        return materialize_bundle(
            zip_path, uploads_dir, sha256, validate=_validate_bundle_config
        ), 200
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid bundle: {e}"}, 400


def _locate_config_or_heal(dest: Path) -> tuple[Any, int]:
    """`find_bundle_config(dest)`, wrapped the same way: `(config_path, 200)`, or a 500 with *dest*
    removed so a retry gets a genuine re-extraction instead of repeating this error forever — only
    reachable for a corrupted/tampered entry (`materialize_bundle` only ever returns a cache hit for
    a directory `_validate_bundle_config` already approved)."""
    config_path = find_bundle_config(dest)
    if config_path is None:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": "cached bundle is missing its config (try re-uploading)"}, 500
    return config_path, 200


def bind_upload_config(
    state: ServeState, zip_path: Path, filename: str, *, sha256: str, actor: str | None = None
) -> tuple[Any, int]:
    """Bind an uploaded zip bundle as the active config (BE-0073) — a third source in the "Open
    config" UI, alongside the file browser and the Git picker.

    The zip is a self-contained checkout — a ``bajutsu.config.yaml``, its scenario tree, and the
    built ``appPath`` binary it names — delivered over the wire. *sha256* is the digest the handler
    computed while streaming the upload to *zip_path* (so the file is read once, not again to hash).
    We extract it into a serve-owned, sha256-keyed cache (`materialize_bundle`), persist the raw zip
    to the object store when one is configured (BE-0243, so another replica can fetch it later), then
    bind it exactly like the Git source binds a checkout (`bind_git_config`): `state.config` points at
    the bundle's config and `state.cwd` at the bundle root, so the config's relative
    `appPath`/`scenarios`/`baselines` resolve against the extracted tree and the Replay / Record /
    Crawl tabs all run from it. Every target's path fields are confined to the bundle at bind
    (`Effective.rebased`), so an uploaded config can't point serve's scenario/build logic at host
    paths outside the tree (BE-0051). Returns `{config, targets, source}` like the other sources. A
    validation failure never lets a partial/invalid cache entry survive (`materialize_bundle`'s own
    cleanup). An object-store write failure leaves the local cache entry alone — it is valid,
    reusable content regardless of whether this store write succeeds, and by the time it fails a
    concurrent bind of the same `sha256` may already depend on it — and returns a 4xx without ever
    binding."""
    size = zip_path.stat().st_size
    org = state.org_of(actor)
    dest, status = _materialize_or_error(zip_path, _org_uploads_dir(state, org), sha256)
    if status != 200:
        return dest, status
    config_path, status = _locate_config_or_heal(dest)
    if status != 200:
        return config_path, status
    if state.object_store is not None:
        key = _upload_store_key(state, org, sha256)
        if not state.object_store.exists(key):
            try:
                state.object_store.put_file(key, zip_path, content_type="application/zip")
            except Exception as e:  # SDK-specific errors vary by backend (S3 vs GCS), same broad
                # catch object_store.py's upload_tree already uses around a store write.
                return {"error": f"could not persist the uploaded bundle: {e}"}, 400
    upload = Upload(
        dir=dest,
        config=config_path,
        filename=_safe_filename(filename),
        sha256=sha256,
        size=size,
        org=org,
        actor=actor,
    )
    state.bind_upload(upload)
    _record_audit(state, actor, org, "upload", upload.filename, {"sha256": sha256})
    return {
        "ok": True,
        "config": str(config_path),
        "targets": list_targets(config_path),
        "source": {"kind": "upload", "filename": upload.filename, "sha256": sha256, "size": size},
    }, 200


def activate_uploaded_project(
    state: ServeState, source: dict[str, Any], *, org: str, actor: str | None = None
) -> tuple[Any, int] | None:
    """Fetch-and-extract fallback for reactivating an `upload`-kind project (BE-0243).

    Returns ``None`` when there is nothing to restore from — no object store configured, or *source*
    carries no usable `sha256` — so the caller (`activate_project`) falls back to its existing `409`.
    *source* is a stored project record a client shapes via `register_project` (BE-0225), so its
    `sha256` is untrusted: it must be a full lowercase hex digest (`_SHA256_RE`) before it is ever
    turned into a path (`uploads_dir / sha256`) or object-store key, the same way the Git source
    validates a resolved commit SHA before doing the same (`bajutsu/config_source.py`) — an
    unvalidated value could otherwise walk `materialize_bundle` outside the cache root (a `../`) or
    step into a different, already-extracted cache entry. Otherwise resolves the org-scoped
    sha256-keyed local cache (`materialize_bundle`, reused as-is on a hit — the same replica already
    validated this exact content), fetching the raw zip from the object store on a miss. A validation
    failure on a *fetched* bundle is a real error (the bytes existed but were corrupt), not a
    ``None`` fallback."""
    sha256 = source.get("sha256")
    if (
        state.object_store is None
        or not isinstance(sha256, str)
        or not _SHA256_RE.fullmatch(sha256)
    ):
        return None
    uploads_dir = _org_uploads_dir(state, org)
    dest = uploads_dir / sha256
    if not dest.exists():
        data = state.object_store.get_bytes(_upload_store_key(state, org, sha256))
        if data is None:
            return None
        fd, tmp_name = tempfile.mkstemp(suffix=".zip")
        tmp_zip = Path(tmp_name)
        try:
            with open(fd, "wb") as f:
                f.write(data)
            dest, status = _materialize_or_error(tmp_zip, uploads_dir, sha256)
        finally:
            tmp_zip.unlink(missing_ok=True)
        if status != 200:
            return dest, status
    config_path, status = _locate_config_or_heal(dest)
    if status != 200:
        return config_path, status
    filename = source.get("filename")
    size = source.get("size")
    upload = Upload(
        dir=dest,
        config=config_path,
        filename=_safe_filename(filename if isinstance(filename, str) else ""),
        sha256=sha256,
        size=size if isinstance(size, int) else 0,
        org=org,
        actor=actor,
    )
    state.bind_upload(upload)
    return {"ok": True, "config": str(config_path)}, 200
