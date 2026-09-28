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

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bajutsu.serve.server.object_store import org_prefix, upload_prefix

ArtifactKind = Literal["config", "scenarios", "binary"]

# The three artifact kinds BE-0268 uploads independently. Order matters for nothing; it's a
# closed set a caller can iterate to validate a triple's keys.
ARTIFACT_KINDS: tuple[ArtifactKind, ...] = ("config", "scenarios", "binary")

# The kinds a single `run` job may override on its own (BE-0431); `config` is not one of them.
OVERRIDE_KINDS: tuple[ArtifactKind, ...] = ("binary", "scenarios")


@dataclass(frozen=True)
class ArtifactOverrides:
    """The standalone artifacts one `run` job resolves against instead of its bound tree (BE-0431).

    Only the job carries this — no binding or remembered configuration records it — so two jobs
    naming different artifacts never contend. *target* is the one target the job runs, whose
    `appPath` and scenarios directory alone receive the overrides. An absent leg (None) keeps
    resolving through the org's binding; at least one leg is named, since a job naming none carries
    no overrides at all.
    """

    target: str
    binary: str | None = None
    scenarios: str | None = None

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("artifact overrides need the target they apply to")
        if self.binary is None and self.scenarios is None:
            raise ValueError("artifact overrides need at least one named leg")

    @property
    def identity(self) -> str:
        """A digest over the named legs, so two jobs whose overrides differ never share a tree.

        Joined in a fixed kind order with an absent leg as an empty segment, like
        `_composition_id`: a real 64-character digest can never collide with that empty segment.
        """
        return hashlib.sha256(f"{self.binary or ''}:{self.scenarios or ''}".encode()).hexdigest()

    @property
    def shas(self) -> dict[ArtifactKind, str]:
        """The named legs, keyed by artifact kind."""
        return {kind: sha for kind in OVERRIDE_KINDS if (sha := getattr(self, kind)) is not None}

    @property
    def provenance(self) -> dict[str, str]:
        """The manifest `provenance` entries naming what this job installed, keyed like the request."""
        return {f"{kind}Artifact": sha for kind, sha in self.shas.items()}

    def to_spec(self) -> dict[str, str | None]:
        """The JSON form a queued job spec carries to the worker."""
        return {"target": self.target, "binary": self.binary, "scenarios": self.scenarios}


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
        with os.fdopen(fd, "wb") as out, src_path.open("rb") as src:
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
