"""An object-storage BaselineStore for the hosted backend (BE-0015 server phase).

`LocalBaselineStore` keeps visual-regression baselines on disk. `ObjectBaselineStore` keeps the same
`BaselineStore` contract but stores them in S3-compatible object storage at ``<prefix>baselines/``,
reusing the injected `ObjectStore` slice. A baseline name is a relative path under that root;
*prefix* is the tenant prefix (parameterized so a shared bucket can host many tenants later with no
contract change). The object-store client is injected, so a fake drives the gate.
"""

from __future__ import annotations

from bajutsu.serve.baselines import _safe_baseline_name
from bajutsu.serve.server.object_store import ObjectStore, baseline_prefix


class ObjectBaselineStore:
    """`BaselineStore` backed by object storage under ``<prefix>baselines/``."""

    def __init__(self, store: ObjectStore, *, prefix: str = "") -> None:
        self._store = store
        self._dir = baseline_prefix(prefix)

    def open_bytes(self, name: str) -> bytes | None:
        return self._store.get_bytes(self._dir + name) if _safe_baseline_name(name) else None

    def write(self, name: str, data: bytes) -> str | None:
        if not _safe_baseline_name(name):
            return None
        self._store.put_bytes(self._dir + name, data)
        return name

    def names(self) -> list[str]:
        # Sorted for determinism; drop a stray ``baselines/`` marker (empty name) and any unsafe
        # key that open_bytes would reject anyway.
        return sorted(
            n
            for k in self._store.list_keys(self._dir)
            if (n := k[len(self._dir) :]) and _safe_baseline_name(n)
        )
