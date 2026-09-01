"""Restoring an `upload`-kind config source from durable storage (BE-0243 / BE-0268).

The fetch-and-extract path a hosted replica takes when it holds no local copy of a bundle another
replica received. It used to be reached through `POST /api/projects/<name>/activate`; BE-0404
deleted that endpoint and re-homed the record onto the org row, so these drive
`restore_uploaded_config` directly — the seam both the legacy single-sha bundle and BE-0268's
composed triple share.

The locator reaches the server from a client at bind, so every sha is untrusted: the cases below pin
that a malformed one never becomes a path or an object-store key, and that one org can never
cache-hit into another's extracted tree.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path

from _shared import FakeObjectStore, project

from bajutsu import serve as srv
from bajutsu.serve.operations.upload import bind_composition, restore_uploaded_config


def _bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bajutsu.config.yaml", "targets: {}\n")
    return buf.getvalue()


def _scenarios_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("scenarios/smoke.yaml", "- name: a\n  steps: []\n")
    return buf.getvalue()


def _composed_config() -> bytes:
    return (
        b"defaults: { backend: [ios] }\n"
        b"targets:\n  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"
    )


def _state(tmp_path: Path, **kw: object) -> srv.ServeState:
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        uploads_dir=tmp_path / "uploads",
        **kw,  # type: ignore[arg-type]
    )


# --- the legacy single-sha bundle (BE-0243) ---


def test_a_stored_bundle_is_fetched_extracted_and_bound(tmp_path: Path) -> None:
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    state = _state(tmp_path, object_store=FakeObjectStore({f"uploads/{sha256}.zip": blob}))
    source = {"kind": "upload", "filename": "suite.zip", "sha256": sha256, "size": len(blob)}

    result = restore_uploaded_config(state, source, org="default")
    assert result is not None and result[1] == 200
    assert state.binding.config is not None and state.binding.config.name == "bajutsu.config.yaml"


def test_nothing_to_restore_from_answers_none(tmp_path: Path) -> None:
    # A store is configured, but this bundle's bytes were never persisted there (or the key was
    # evicted). `None` is "nothing to restore from", which the caller turns into its own error —
    # distinct from a real failure below.
    state = _state(tmp_path, object_store=FakeObjectStore())
    assert restore_uploaded_config(state, {"sha256": "a" * 64}, org="default") is None
    # No object store at all is the same answer, because nothing is cached here either.
    state.object_store = None
    assert restore_uploaded_config(state, {"sha256": "a" * 64}, org="default") is None


def test_a_store_fetch_failure_is_a_real_error_not_nothing_to_restore(tmp_path: Path) -> None:
    # A transient store error (not "key absent") must be reported as a real error, not folded into
    # the None fallback, which a caller would read as "no durable copy exists at all".
    store = FakeObjectStore()
    store.fail_with = ConnectionError("bucket unreachable")
    result = restore_uploaded_config(
        _state(tmp_path, object_store=store), {"sha256": "a" * 64}, org="default"
    )
    assert result is not None
    payload, status = result
    assert status == 400 and "could not fetch" in payload["error"]


def test_corrupt_fetched_bytes_are_a_400(tmp_path: Path) -> None:
    sha256 = "b" * 64
    state = _state(tmp_path, object_store=FakeObjectStore({f"uploads/{sha256}.zip": b"not a zip"}))
    result = restore_uploaded_config(state, {"sha256": sha256}, org="default")
    assert result is not None
    payload, status = result
    assert status == 400 and "invalid bundle" in payload["error"]


def test_a_local_cache_hit_never_touches_the_store(tmp_path: Path) -> None:
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    cached = tmp_path / "uploads" / sha256
    cached.mkdir(parents=True)
    (cached / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")

    class _PoisonedStore(FakeObjectStore):
        def get_bytes(self, key: str) -> bytes | None:
            raise AssertionError(f"should not touch the object store for a cache hit: {key}")

    state = _state(tmp_path, object_store=_PoisonedStore())
    result = restore_uploaded_config(state, {"sha256": sha256, "size": len(blob)}, org="default")
    assert result is not None and result[1] == 200


def test_a_malformed_sha256_never_becomes_a_path(tmp_path: Path) -> None:
    # The locator is client-shaped, so a sha that is not a well-formed hex digest must never reach a
    # filesystem path or an object-store key — treated as nothing to restore from.
    state = _state(tmp_path, object_store=FakeObjectStore({"uploads/../../../etc.zip": b"x"}))
    assert restore_uploaded_config(state, {"sha256": "../../../etc"}, org="default") is None


def test_one_org_never_cache_hits_into_another_s_extracted_tree(tmp_path: Path) -> None:
    # `uploads_dir` is one shared path across every org on the server backend, so without the
    # org-scoping org B could claim org A's sha256 and bind straight into org A's tree.
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    org_a = tmp_path / "uploads" / "orgA" / sha256
    org_a.mkdir(parents=True)
    (org_a / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")

    state = _state(tmp_path, object_store=FakeObjectStore())
    source = {"kind": "upload", "filename": "x.zip", "sha256": sha256, "size": len(blob)}
    assert restore_uploaded_config(state, source, org="orgB") is None
    result = restore_uploaded_config(state, source, org="orgA")
    assert result is not None and result[1] == 200


def test_the_org_scoping_holds_at_the_object_store_too(tmp_path: Path) -> None:
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    state = _state(tmp_path, object_store=FakeObjectStore({f"orgA/uploads/{sha256}.zip": blob}))
    source = {"kind": "upload", "filename": "x.zip", "sha256": sha256, "size": len(blob)}
    assert restore_uploaded_config(state, source, org="orgB") is None
    result = restore_uploaded_config(state, source, org="orgA")
    assert result is not None and result[1] == 200


# --- the composed triple (BE-0268) ---


def test_a_composed_triple_is_fetched_and_composed(tmp_path: Path) -> None:
    config_blob, scenarios_blob = _composed_config(), _scenarios_zip()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    scenarios_sha = hashlib.sha256(scenarios_blob).hexdigest()
    state = _state(
        tmp_path,
        object_store=FakeObjectStore(
            {
                f"uploads/config/{config_sha}": config_blob,
                f"uploads/scenarios/{scenarios_sha}": scenarios_blob,
            }
        ),
    )
    source = {
        "kind": "upload",
        "filename": "suite",
        "artifacts": {"config": config_sha, "scenarios": scenarios_sha},
        "size": len(config_blob),
    }

    result = restore_uploaded_config(state, source, org="default")
    assert result is not None and result[1] == 200
    assert state.binding.config is not None and state.binding.config.name == "bajutsu.config.yaml"


def test_a_composed_triple_answers_none_with_nothing_cached_and_no_store_or_a_bad_sha(
    tmp_path: Path,
) -> None:
    # Missing a store is only half the reason: with nothing composed and no leg cached either, there
    # is nothing local to fall back on (BE-0393 unit 5).
    state = _state(tmp_path)
    assert restore_uploaded_config(state, {"artifacts": {"config": "a" * 64}}, org="d") is None
    state.object_store = FakeObjectStore()
    assert restore_uploaded_config(state, {"artifacts": {"config": "not-hex"}}, org="d") is None


def test_absent_config_bytes_are_a_404(tmp_path: Path) -> None:
    # A well-formed config sha that is not actually stored anywhere is a real "not found", distinct
    # from the "nothing to restore from at all" `None` above.
    state = _state(tmp_path, object_store=FakeObjectStore())
    result = restore_uploaded_config(state, {"artifacts": {"config": "a" * 64}}, org="default")
    assert result is not None
    payload, status = result
    assert status == 404 and "not available" in payload["error"]


def test_an_invalid_leg_sha_is_a_400(tmp_path: Path) -> None:
    state = _state(tmp_path, object_store=FakeObjectStore())
    source = {"artifacts": {"config": "a" * 64, "scenarios": "../../../etc"}}
    result = restore_uploaded_config(state, source, org="default")
    assert result is not None
    payload, status = result
    assert status == 400 and "invalid scenarios" in payload["error"]


def test_a_triple_missing_a_leg_the_config_needs_is_a_400(tmp_path: Path) -> None:
    # The config declares a scenarios dir but the triple supplies no scenarios artifact — a real
    # error, not a silent partial bind.
    config_blob = _composed_config()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    state = _state(
        tmp_path, object_store=FakeObjectStore({f"uploads/config/{config_sha}": config_blob})
    )
    result = restore_uploaded_config(state, {"artifacts": {"config": config_sha}}, org="default")
    assert result is not None
    payload, status = result
    assert status == 400 and "invalid composition" in payload["error"]


def test_a_composed_cache_hit_never_touches_the_store(tmp_path: Path) -> None:
    config_blob, scenarios_blob = _composed_config(), _scenarios_zip()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    scenarios_sha = hashlib.sha256(scenarios_blob).hexdigest()

    class _PoisonedStore(FakeObjectStore):
        def get_bytes(self, key: str) -> bytes | None:
            raise AssertionError(f"should not touch the object store for a cache hit: {key}")

    state = _state(
        tmp_path,
        object_store=FakeObjectStore(
            {
                f"uploads/config/{config_sha}": config_blob,
                f"uploads/scenarios/{scenarios_sha}": scenarios_blob,
            }
        ),
    )
    source = {"artifacts": {"config": config_sha, "scenarios": scenarios_sha}}
    assert restore_uploaded_config(state, source, org="default") is not None
    # Composed and cached; a second restore must resolve locally.
    state.object_store = _PoisonedStore()
    result = restore_uploaded_config(state, source, org="default")
    assert result is not None and result[1] == 200


# --- restoring with no object store at all (BE-0393 unit 5) ---


def test_a_cached_bundle_is_restored_with_no_object_store(tmp_path: Path) -> None:
    """The extracted tree is keyed by content hash and survives a restart, so a replica that still
    holds it rebinds without a fetch. No shipping configuration reaches this branch yet — a
    deployment that keeps a config memory also has a store — so this pins the groundwork units 6 and
    7 build on rather than a recovery an operator performs today."""
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    cached = tmp_path / "uploads" / sha256
    cached.mkdir(parents=True)
    (cached / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    state = _state(tmp_path)
    assert state.object_store is None

    result = restore_uploaded_config(state, {"sha256": sha256, "size": len(blob)}, org="default")

    assert result is not None and result[1] == 200
    assert state.config is not None and state.config.name == "bajutsu.config.yaml"


def test_a_malformed_sha256_never_becomes_a_path_without_a_store_either(tmp_path: Path) -> None:
    """The validation guards the cache lookup this unit moved in front of the object store, so it
    has to run *before* the hash becomes a path component — a `../`-laden value is refused outright,
    not walked out of the cache root."""
    state = _state(tmp_path)

    for bad in ("../" * 8 + "etc", "A" * 64, "abc"):
        assert restore_uploaded_config(state, {"sha256": bad}, org="default") is None, bad
    # Nothing was created under the cache root by the refused lookups.
    assert not (tmp_path / "uploads").exists()


def test_a_composed_tree_is_restored_from_the_cache_with_no_object_store(tmp_path: Path) -> None:
    config_blob, scenarios_blob = _composed_config(), _scenarios_zip()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    scenarios_sha = hashlib.sha256(scenarios_blob).hexdigest()
    source = {"artifacts": {"config": config_sha, "scenarios": scenarios_sha}}
    state = _state(
        tmp_path,
        object_store=FakeObjectStore(
            {
                f"uploads/config/{config_sha}": config_blob,
                f"uploads/scenarios/{scenarios_sha}": scenarios_blob,
            }
        ),
    )
    # Compose once so the tree lands in the cache, then take the store away entirely.
    assert restore_uploaded_config(state, source, org="default") is not None
    state.object_store = None
    # Drop the per-leg cache too, so only the composed tree can answer. Without this the restore
    # could pass by recomposing from the legs, and a drifted composition id would go unnoticed.
    shutil.rmtree(tmp_path / "uploads" / "artifacts")

    result = restore_uploaded_config(state, source, org="default")

    assert result is not None and result[1] == 200


def test_a_salted_composition_is_rebuilt_from_its_cached_legs_with_no_store(
    tmp_path: Path,
) -> None:
    """A tree composed *with* a `scenarios` display name is cached under a salted id the restore
    path cannot reconstruct, since the stored record carries no such name. Its legs are still in the
    artifact cache, and composing from them needs no object store either."""
    config_blob, scenarios_blob = _composed_config(), b"- name: a\n  steps: []\n"
    config_sha = hashlib.sha256(config_blob).hexdigest()
    scenarios_sha = hashlib.sha256(scenarios_blob).hexdigest()
    state = _state(
        tmp_path,
        object_store=FakeObjectStore(
            {
                f"uploads/config/{config_sha}": config_blob,
                f"uploads/scenarios/{scenarios_sha}": scenarios_blob,
            }
        ),
    )
    # Bind through the compose picker, which salts the cache key with the single-YAML name.
    payload, status = bind_composition(
        state,
        {"config": config_sha, "scenarios": scenarios_sha, "scenariosName": "smoke.yaml"},
    )
    assert status == 200, payload
    state.object_store = None

    result = restore_uploaded_config(
        state, {"artifacts": {"config": config_sha, "scenarios": scenarios_sha}}, org="default"
    )

    assert result is not None and result[1] == 200


def test_still_nothing_to_restore_from_with_no_store_and_no_cache(tmp_path: Path) -> None:
    # Nothing on disk and nothing to fetch: the `None` fallback stands, so "no store configured"
    # stops meaning "never restorable" without starting to mean "always restorable".
    state = _state(tmp_path)
    assert restore_uploaded_config(state, {"sha256": "a" * 64}, org="default") is None
    assert (
        restore_uploaded_config(state, {"artifacts": {"config": "a" * 64}}, org="default") is None
    )
