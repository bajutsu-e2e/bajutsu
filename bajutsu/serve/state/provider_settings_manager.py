"""The per-organization provider-settings cluster carved out of the server state (BE-0248)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .org_provider_settings import OrgProviderSettings
from .provider_settings import ProviderSettings

if TYPE_CHECKING:
    from bajutsu.serve.provider_store import ProviderSettingsStore


@dataclass
class ProviderSettingsManager:
    """The per-org AI-provider-settings cluster carved out of `ServeState` (BE-0248): the in-memory
    selection map, its durable store, the two locks that guard them, and the read/write methods —
    the in-memory half of the per-org provider selection (BE-0229). Giving it a boundary makes the
    copy-on-read/copy-on-write discipline a property of this type rather than a convention spread
    across three method bodies, the way `JobRegistry` (BE-0198) made atomic id assignment a property
    of its boundary.

    `settings` maps an org to its `OrgProviderSettings`; `store` is the `default` org's durable
    backing on local serve (None on a hosted deployment, whose per-org stores come from `org_stores`,
    and on a server backend without a database — the selection is then session-only). `_provider_lock`
    guards the in-memory map against concurrent Settings-panel reads/writes (serve is a
    ThreadingHTTPServer); `_persist_lock` serializes the re-snapshot + disk write in `persist`, kept
    separate so I/O never runs inside the in-memory lock.
    """

    settings: dict[str, OrgProviderSettings] = field(default_factory=dict)
    store: ProviderSettingsStore | None = None
    _provider_lock: threading.Lock = field(default_factory=threading.Lock)
    _persist_lock: threading.Lock = field(default_factory=threading.Lock)

    def org_provider_settings(self, org: str) -> OrgProviderSettings | None:
        """A copy of *org*'s AI provider selection, or None when the org has no in-memory entry yet
        (BE-0229). Taken under the lock — serve is a ThreadingHTTPServer, so a bare read could race a
        concurrent `set_org_provider_choice` write. Returns a copy (the slots dict too) so the caller
        can never mutate the live entry. None means "not loaded"; the operations layer lazily loads
        it from the org's store on first access."""
        with self._provider_lock:
            current = self.settings.get(org)
            if current is None:
                return None
            return OrgProviderSettings(
                provider=current.provider,
                slots=dict(current.slots),
                language=current.language,
            )

    def put_org_provider_settings(self, org: str, settings: OrgProviderSettings) -> None:
        """Seed *org*'s in-memory entry from a freshly loaded snapshot (BE-0229), under the lock.
        Stores an independent copy so a later store reload can't alias a live entry."""
        with self._provider_lock:
            self.settings[org] = OrgProviderSettings(
                provider=settings.provider,
                slots=dict(settings.slots),
                language=settings.language,
            )

    def set_org_provider_choice(
        self, org: str, *, provider: str, slot: ProviderSettings, language: str
    ) -> None:
        """Apply one save to *org*'s selection under the lock (BE-0229): set the active *provider*,
        store its *slot* (BE-0183), and set the org-wide output *language* (BE-0188). The slot is
        written into the existing entry in place, so a provider left behind keeps its remembered slot
        — and a concurrent save for a *different* provider adds its own slot rather than clobbering
        this one (mirroring the pre-BE-0229 per-key map write). The active provider and language are
        last-writer-wins, as they were process-globally. Assumes the org's persisted slots are
        already loaded (the caller loads them first) so this never drops them."""
        with self._provider_lock:
            current = self.settings.get(org)
            if current is None:
                current = OrgProviderSettings()
                self.settings[org] = current
            current.slots[provider] = slot
            current.provider = provider
            current.language = language

    def persist(self, org: str, provider: str, store: ProviderSettingsStore) -> None:
        """Write *provider* + *org*'s current in-memory slot map to *store* (BE-0229), serialized by
        `_persist_lock` so whichever thread wins the lock last re-reads the org's settings inside it
        and writes the most up-to-date map. Keeping the lock inside this method is what lets the one
        out-of-package caller (`operations/config.py`'s `_persist_provider_settings`) drive the write
        without reaching into the manager's locks directly. Store resolution, failure handling, and
        the persisted/not-persisted signaling stay with that caller — this owns only the race-safe
        re-snapshot and the write."""
        # Imported lazily: `provider_store` imports `ProviderSettings` from this module, so a
        # top-level import here would be a cycle (the same reason `env_var_for_secret` imports late).
        from bajutsu.serve.provider_store import PersistedProviderSettings

        with self._persist_lock:
            # Re-read inside the lock so the thread that wins last always writes the most recent
            # in-memory state, regardless of when each thread's mutation was applied.
            snapshot = self.org_provider_settings(org)
            slots = snapshot.slots if snapshot is not None else {}
            store.save(PersistedProviderSettings(provider=provider, settings=slots))
