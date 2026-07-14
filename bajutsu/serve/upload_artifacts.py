"""Store a config / scenarios / binary artifact as an independent, content-addressed blob (BE-0268).

BE-0073's combined bundle couples three things whose change cadence differs wildly: a large binary
that changes every build, a small scenario tree that changes every edit, and a config that almost
never changes. This module lets each be uploaded and cached on its own, keyed by the sha256 of its
raw bytes — the same content-addressing BE-0243 already gives the combined bundle's zip, just one
level finer. All three kinds are cached identically as raw bytes; no kind-specific extraction
happens here. Extraction (unzipping ``scenarios``, placing ``binary`` at a config's ``appPath``) is
`materialize_composition`'s job (``bajutsu/serve/operations/composition.py``), once a config names
where each artifact belongs — this module only ever answers "do I already have these bytes, and if
not, here they are."
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from bajutsu.serve.server.object_store import org_prefix, upload_prefix

ArtifactKind = Literal["config", "scenarios", "binary"]

# The three artifact kinds BE-0268 uploads independently. Order matters for nothing; it's a
# closed set a caller can iterate to validate a triple's keys.
ARTIFACT_KINDS: tuple[ArtifactKind, ...] = ("config", "scenarios", "binary")


def artifact_store_key(prefix: str, org: str, kind: ArtifactKind, sha256: str) -> str:
    """The object-store key *kind*'s content lives at for *org* (BE-0268): nested under the same
    per-kind sub-prefix scheme every sibling store already uses, and under the existing
    ``uploads/`` prefix so it can never collide with a legacy combined-bundle key
    (``uploads/<sha256>.zip`` — a bare sha never equals the literal ``config``/``scenarios``/
    ``binary`` segment this inserts)."""
    return f"{upload_prefix(org_prefix(prefix, org))}{kind}/{sha256}"


def local_artifact_dir(artifacts_dir: Path, org: str, kind: ArtifactKind) -> Path:
    """*org*'s local cache root for *kind* under *artifacts_dir* — the sibling of
    `bajutsu.serve.operations.upload._org_uploads_dir`'s org-scoping, one directory per kind so a
    kind's bare sha256-named entries never collide with another kind's."""
    return artifacts_dir / kind / org_prefix("", org)


def materialize_artifact(
    src_path: Path, artifacts_dir: Path, org: str, kind: ArtifactKind, sha256: str
) -> Path:
    """Resolve *sha256*'s content-addressed cache entry for *kind* under *artifacts_dir*, copying
    only on a cache miss — the same trust boundary `materialize_bundle` gives a cached bundle
    extraction: this replica already has these exact bytes, so it need not copy them again.

    Unlike `materialize_bundle`, there is no extraction or validation here — an artifact is cached
    as-is; a `scenarios` zip is only ever unzipped, and a `binary` only ever placed, once
    `materialize_composition` knows the config that names where they belong. The returned path is
    a single file, never a directory.
    """
    dest_dir = local_artifact_dir(artifacts_dir, org, kind)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sha256
    if dest.exists():
        return dest
    fd, tmp_name = tempfile.mkstemp(dir=dest_dir, prefix=f".{sha256}.tmp-")
    tmp = Path(tmp_name)
    try:
        with open(fd, "wb") as out, src_path.open("rb") as src:
            shutil.copyfileobj(src, out)
        try:
            tmp.rename(dest)
        except OSError:
            # A concurrent call won the rename; its bytes are identical (same sha256), so drop ours.
            if not dest.exists():
                raise
            tmp.unlink(missing_ok=True)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest
