"""The configuration serve is bound to, as one value (BE-0393)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from bajutsu.serve.uploads import Upload


@dataclass(frozen=True)
class ConfigBinding:
    """The configuration `serve` is bound to, as one value (BE-0393 unit 1).

    These six were six independent mutable attributes on `ServeState` that had to be written
    together — and were: each binder set all of them, and a separate `release_upload` cleared three to
    keep a stale bundle's directory from leaking into the next bind. Frozen and replaced whole, the
    incomplete combinations are unrepresentable, and there is one thing to key by session rather
    than six (unit 2).

    Attributes:
        config: The bound configuration file, or None until one is opened.
        cwd: What the configuration's relative paths resolve against — its own directory for a local
            file, the checkout root for a Git source, the extraction root for a bundle (BE-0242).
        provenance: Git-source provenance (host/owner/repo/ref/sha) when the configuration came from
            one, else None, so the UI can show which commit an opaque cache path was materialized
            from.
        upload: The bound uploaded bundle, or None when the configuration came from the file browser,
            Git, or startup.
        git_from_api: Whether this is a Git source bound at runtime through the API rather than
            pre-configured by the operator. Such a source is untrusted, so its jobs carry the
            `remote` config source the worker's command policy reads (BE-0121, BE-0492).
        org: The org that bound this configuration through the API — an uploaded bundle, a composed
            triple, or a Git source (BE-0375). None for the launch configuration, whose own `orgs:`
            block then partitions targets as the operator wrote it.
        origin: Which of the three answers `binding_for` gave, for the header to name (unit 7):
            `session` for what the member bound themselves, `inherited` for the org's remembered
            configuration restored into their slot, `deployment` for the one every sessionless
            request reads. Never set by a binder — the two funnels that decide which slot a value
            lands in stamp it, so it cannot disagree with where the value actually is.
    """

    config: Path | None = None
    cwd: Path = field(default_factory=Path.cwd)
    provenance: dict[str, str] | None = None
    upload: Upload | None = None
    git_from_api: bool = False
    org: str | None = None
    origin: Literal["deployment", "session", "inherited"] = "deployment"
