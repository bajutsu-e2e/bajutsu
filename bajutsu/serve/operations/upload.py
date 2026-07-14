"""Config-bundle upload serve operations (BE-0073, split out in BE-0127).

BE-0243 adds durable, cross-replica resolution on top of the local extraction sandbox: when
`state.object_store` is configured (the hosted `server` backend), the raw zip is persisted
content-addressed by its sha256 so `activate_project` can fetch-and-extract it on any replica
instead of refusing an `upload`-kind project with a `409`. Local `serve` (no object store) is
unchanged — the bundle stays exactly as ephemeral as BE-0073 shipped it.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from bajutsu.serve.authz import _record_audit
from bajutsu.serve.helpers import list_targets
from bajutsu.serve.operations.composition import materialize_composition
from bajutsu.serve.server.object_store import org_prefix, upload_prefix
from bajutsu.serve.state import ServeState
from bajutsu.serve.upload_artifacts import (
    ARTIFACT_KINDS,
    ArtifactKind,
    artifact_store_key,
    local_artifact_dir,
    materialize_artifact,
)
from bajutsu.serve.uploads import (
    Upload,
    find_bundle_config,
    materialize_bundle,
    validate_bundle_config,
)

# The kinds beyond `config` a composed triple's `artifacts` locator may name — `config` is the only
# always-required leg (see `_activate_composed_project`); `scenarios`/`binary` are required only when
# the config itself needs them (`materialize_composition`'s coherence check). Derived from
# `ARTIFACT_KINDS` (the authoritative closed set) rather than hand-duplicated, so a future fourth
# kind can't silently drift between the two.
_COMPOSED_KINDS: tuple[ArtifactKind, ...] = tuple(k for k in ARTIFACT_KINDS if k != "config")

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


def _artifacts_dir(state: ServeState) -> Path:
    """The local cache root for independently-uploaded artifacts (BE-0268), nested under
    `state.uploads_dir` alongside the legacy bundle cache: `artifacts/<kind>/<org>/<sha256>` never
    collides with a legacy bundle's bare `<sha256>`-named directory."""
    return state.uploads_dir / "artifacts"


def _compositions_dir(state: ServeState) -> Path:
    """The local cache root for materialized artifact-triple compositions (BE-0268), nested under
    `state.uploads_dir` alongside the legacy bundle cache and the per-artifact cache above."""
    return state.uploads_dir / "compositions"


def _materialize_or_error(zip_path: Path, uploads_dir: Path, sha256: str) -> tuple[Any, int]:
    """`materialize_bundle`, wrapped in the `(payload, status)` convention `bind_git_config`/
    `bind_config` already use: `(dest, 200)` on success, or an "invalid bundle" 400."""
    try:
        return materialize_bundle(
            zip_path, uploads_dir, sha256, validate=validate_bundle_config
        ), 200
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid bundle: {e}"}, 400


def _locate_config_or_heal(dest: Path) -> tuple[Any, int]:
    """`find_bundle_config(dest)`, wrapped the same way: `(config_path, 200)`, or a 500 — only
    reachable for a corrupted/tampered entry (`materialize_bundle` only ever returns a cache hit for
    a directory `validate_bundle_config` already approved). *dest* is left alone even here:
    `materialize_bundle` never deletes a directory once it exists, since another bind (this org, this
    replica or another) may already depend on it, and this rare branch is no exception — an operator
    inspects/clears the entry rather than a caller silently pulling it out from under a possible
    concurrent user."""
    config_path = find_bundle_config(dest)
    if config_path is None:
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
        try:
            if not state.object_store.exists(key):
                state.object_store.put_file(key, zip_path, content_type="application/zip")
        except Exception as e:  # SDK-specific errors vary by backend (S3 vs GCS), same broad catch
            # object_store.py's upload_tree already uses around a store write; exists() itself can
            # raise too (e.g. S3ObjectStore.exists re-raises a non-"not found" ClientError), so it
            # shares this try rather than crashing the request with an unhandled exception.
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


def bind_artifact(
    state: ServeState, kind: ArtifactKind, src_path: Path, *, sha256: str, actor: str | None = None
) -> tuple[Any, int]:
    """Store one independently-uploaded artifact (BE-0268): persist it to the object store when one
    is configured (mirrors `bind_upload_config`'s own store write) and cache it locally
    (`materialize_artifact`). *sha256* is the digest the handler computed while streaming the upload
    to *src_path*, same as `bind_upload_config`.

    This does not bind anything as the active config — one artifact alone is not runnable; a project
    binds a triple, composed lazily by `activate_uploaded_project` (BE-0268 widens its `upload`
    source locator from one bundle sha to `{"config", "scenarios", "binary"}` shas)."""
    size = src_path.stat().st_size
    org = state.org_of(actor)
    if state.object_store is not None:
        key = artifact_store_key(state.object_store_prefix, org, kind, sha256)
        try:
            if not state.object_store.exists(key):
                state.object_store.put_file(key, src_path, content_type="application/octet-stream")
        except Exception as e:  # SDK-specific errors vary by backend (S3 vs GCS), same broad catch
            return {"error": f"could not persist the {kind} artifact: {e}"}, 400
    try:
        materialize_artifact(src_path, _artifacts_dir(state), org, kind, sha256)
    except OSError as e:
        return {"error": f"could not cache the {kind} artifact: {e}"}, 400
    _record_audit(state, actor, org, f"artifact:{kind}", sha256, {"sha256": sha256})
    return {"ok": True, "kind": kind, "sha256": sha256, "size": size}, 200


def artifact_exists(
    state: ServeState, kind: str | None, sha256: str | None, *, actor: str | None = None
) -> tuple[Any, int]:
    """Whether a *kind*/*sha256* artifact is already stored for this actor's org (BE-0268) — lets a
    client skip re-uploading bytes it already sent, whether or not an object store is configured.
    *kind*/*sha256* are raw query-string values (untrusted), validated the same way any other
    artifact sha is before it is ever turned into a path or object-store key."""
    if kind not in ARTIFACT_KINDS:
        return {"error": f"unknown artifact kind: {kind!r}"}, 400
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        return {"error": "sha256 must be a full lowercase hex digest"}, 400
    org = state.org_of(actor)
    if state.object_store is not None:
        try:
            exists = state.object_store.exists(
                artifact_store_key(state.object_store_prefix, org, kind, sha256)
            )
        except Exception:  # a transient store error reads as "not confirmed present", not a crash
            exists = False
    else:
        # No object store: consult the local content-addressed cache. Rather than join the
        # untrusted *sha256* onto a path and stat it (a filesystem read driven by client input),
        # list the kind's cache directory — whose path derives only from the allowlisted *kind* —
        # and test *sha256* as a plain string against the entry names. The sha never reaches a
        # path expression, and a name with a separator simply matches nothing.
        cache_dir = local_artifact_dir(_artifacts_dir(state), org, kind)
        try:
            exists = sha256 in {entry.name for entry in cache_dir.iterdir()}
        except OSError:  # cache dir not created yet ⇒ nothing stored for this kind/org
            exists = False
    return {"exists": exists}, 200


def _composition_id(shas: dict[str, str]) -> str:
    """A deterministic cache key for a `(config, scenarios, binary)` triple, so composing the same
    combination twice is a cache hit (`materialize_composition`). Built from a fixed kind order
    (not `shas`' iteration order, which is whatever the untrusted source dict happened to use) so
    the same triple always yields the same id; an omitted leg joins as an empty segment, which can't
    collide with a present leg (every real sha256 is exactly 64 hex characters, never empty)."""
    parts = [shas.get(kind, "") for kind in ("config", "scenarios", "binary")]
    return hashlib.sha256(":".join(parts).encode("ascii")).hexdigest()


def _fetch_artifact(
    state: ServeState, org: str, kind: ArtifactKind, sha256: str
) -> tuple[Path, int] | tuple[dict[str, Any], int]:
    """Resolve *kind*'s *sha256* artifact to a local path for composition: a cache hit reuses it
    as-is, a miss fetches it from the object store. The composed-triple sibling of
    `activate_uploaded_project`'s own fetch-or-cache shape, generalized to a single artifact file
    instead of a whole bundle tree. Only called once the caller has confirmed an object store is
    configured."""
    dest = local_artifact_dir(_artifacts_dir(state), org, kind) / sha256
    if dest.exists():
        return dest, 200
    store = state.object_store
    assert store is not None  # the caller checks this before calling
    try:
        data = store.get_bytes(artifact_store_key(state.object_store_prefix, org, kind, sha256))
    except Exception as e:  # a transient store error must not read as "artifact absent"
        return {"error": f"could not fetch the {kind} artifact: {e}"}, 400
    if data is None:
        return {"error": f"{kind} artifact {sha256!r} is not available"}, 404
    fd, tmp_name = tempfile.mkstemp(prefix=f"{kind}-")
    tmp = Path(tmp_name)
    try:
        with open(fd, "wb") as f:
            f.write(data)
        dest = materialize_artifact(tmp, _artifacts_dir(state), org, kind, sha256)
    except OSError as e:  # disk full, permission error, etc. — a clean 400 like the sibling paths
        # (`bind_artifact`, `_materialize_or_error`) give the same failure mode, not an unhandled 500.
        return {"error": f"could not cache the {kind} artifact: {e}"}, 400
    finally:
        tmp.unlink(missing_ok=True)
    return dest, 200


def _activate_composed_project(
    state: ServeState,
    source: dict[str, Any],
    artifacts: dict[str, Any],
    *,
    org: str,
    actor: str | None = None,
) -> tuple[Any, int] | None:
    """Fetch-and-compose fallback for reactivating a triple-bound `upload`-kind project (BE-0268) —
    the composed-triple sibling of `activate_uploaded_project`'s legacy single-sha path.

    Returns ``None`` when there is nothing to restore from — no object store configured, or the
    `config` leg (the triple's only always-required artifact) is missing or not a valid sha — so the
    caller falls back to the existing `409`. Every leg in *artifacts* is untrusted (a stored
    `register_project` record a client shapes), so each present sha is validated with `_SHA256_RE`
    before it is ever turned into a path or object-store key, the same reasoning
    `activate_uploaded_project`'s legacy path already applies to its own single `sha256`."""
    config_sha = artifacts.get("config")
    if (
        state.object_store is None
        or not isinstance(config_sha, str)
        or not _SHA256_RE.fullmatch(config_sha)
    ):
        return None
    shas: dict[str, str] = {"config": config_sha}
    for kind in _COMPOSED_KINDS:
        sha = artifacts.get(kind)
        if sha is None:
            continue
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            return {"error": f"invalid {kind} artifact sha"}, 400
        shas[kind] = sha

    composition_id = _composition_id(shas)
    compositions_dir = _compositions_dir(state) / org_prefix("", org)
    paths: dict[str, Path] = {}
    if not (compositions_dir / composition_id).exists():
        # Only resolve each leg (local cache hit or object-store fetch) when this exact triple
        # hasn't been composed on this replica before — `materialize_composition` itself no-ops on
        # a cache hit without ever reading these paths, so a replica that's already composed this
        # triple (the common case on reactivation) skips every per-artifact fetch entirely.
        for kind in ARTIFACT_KINDS:
            sha = shas.get(kind)
            if sha is None:
                continue
            fetched, status = _fetch_artifact(state, org, kind, sha)
            if status != 200:
                return fetched, status
            assert isinstance(fetched, Path)  # `status == 200` only ever pairs with a resolved path
            paths[kind] = fetched

    try:
        dest = materialize_composition(
            paths.get("config", Path()),
            paths.get("scenarios"),
            paths.get("binary"),
            compositions_dir=compositions_dir,
            composition_id=composition_id,
        )
    except (OSError, ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid composition: {e}"}, 400
    config_path = dest / "bajutsu.config.yaml"
    filename = source.get("filename")
    size = source.get("size")
    upload = Upload(
        dir=dest,
        config=config_path,
        filename=_safe_filename(filename if isinstance(filename, str) else ""),
        sha256=composition_id,
        size=size if isinstance(size, int) else 0,
        org=org,
        actor=actor,
        artifact_shas=shas,
    )
    state.bind_upload(upload)
    return {"ok": True, "config": str(config_path)}, 200


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
    ``None`` fallback.

    A `source["artifacts"]` dict (BE-0268's composed-triple locator, `{"config", "scenarios",
    "binary"}` shas) branches to `_activate_composed_project` instead — the legacy single-`sha256`
    path below never runs for it, and vice versa; the two locator shapes are mutually exclusive."""
    artifacts = source.get("artifacts")
    if isinstance(artifacts, dict):
        return _activate_composed_project(state, source, artifacts, org=org, actor=actor)
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
        try:
            data = state.object_store.get_bytes(_upload_store_key(state, org, sha256))
        except Exception as e:  # a transient store error (not "key absent") must not be folded
            # into the None/409 fallback — that would misreport a real infra failure as "nothing
            # to restore from".
            return {"error": f"could not fetch the uploaded bundle: {e}"}, 400
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
