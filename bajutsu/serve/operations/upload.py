"""Config-bundle upload serve operations (BE-0073, split out in BE-0127)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from bajutsu.config import load_config, resolve
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.helpers import list_targets
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.uploads import BundleError, Upload, extract_bundle, find_bundle_config


def _safe_filename(name: str) -> str:
    """A display-safe basename for the uploaded zip (provenance only): strip any directory and
    non-printable characters, bound the length, and fall back to a default when nothing remains."""
    base = "".join(c for c in Path(name or "").name if c.isprintable()).strip()
    return (base or "bundle.zip")[:200]


def bind_upload_config(
    state: ServeState, zip_path: Path, filename: str, *, sha256: str, actor: str | None = None
) -> tuple[Any, int]:
    """Bind an uploaded zip bundle as the active config (BE-0073) — a third source in the "Open
    config" UI, alongside the file browser and the Git picker.

    The zip is a self-contained checkout — a ``bajutsu.config.yaml``, its scenario tree, and the
    built ``appPath`` binary it names — delivered over the wire. *sha256* is the digest the handler
    computed while streaming the upload to *zip_path* (so the file is read once, not again to hash).
    We extract it into a serve-owned sandbox, then bind it exactly like the Git source binds a
    checkout (`bind_git_config`): `state.config` points at the bundle's config and `state.cwd` at the
    bundle root, so the config's relative `appPath`/`scenarios`/`baselines` resolve against the
    extracted tree and the Replay / Record / Crawl tabs all run from it. Every target's path fields
    are confined to the bundle at bind (`Effective.rebased`), so an uploaded config can't point
    serve's scenario/build logic at host paths outside the tree (BE-0051). Only one bundle is bound at
    a time — binding any other config removes this sandbox (`state.bind_upload`). Returns
    `{config, targets, source}` like the other sources; on any validation failure the freshly-extracted
    dir is removed and a 4xx is returned."""
    size = zip_path.stat().st_size
    state.uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(dir=state.uploads_dir))
    try:
        extract_bundle(zip_path, dest)
    except BundleError as e:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": f"invalid bundle: {e}"}, 400
    config_path = find_bundle_config(dest)
    if config_path is None:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": "bundle has no bajutsu.config.yaml"}, 400
    try:
        cfg = load_config(config_path.read_text(encoding="utf-8"))
        # Confine every target's path fields to the bundle, the same guard the Git source applies to a
        # fetched checkout (BE-0051): a config pointing appPath/scenarios/baselines at an absolute or
        # `..` path outside the tree is rejected here, so serve's resolution only sees in-bundle paths.
        for name in cfg.targets:
            resolve(cfg, name).rebased(config_path.parent)
    except (OSError, ValueError, yaml.YAMLError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        return {"error": f"invalid bundle: {e}"}, 400
    org = state.org_of(actor)
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
