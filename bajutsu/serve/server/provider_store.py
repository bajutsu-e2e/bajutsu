"""A per-org, DB-backed ProviderSettingsStore for the hosted backend (BE-0229).

`LocalProviderSettingsStore` (in `serve.provider_store`) persists the selection to a single JSON
file — the local-serve shape. `DbProviderSettingsStore` keeps the same `ProviderSettingsStore`
seam but persists it in the ``provider_settings`` table, scoped by ``org_id`` like the other server
tables, so a saved selection survives a restart *per organization* on a hosted deployment — the
per-organization store BE-0184 deferred until per-org runtime resolution (BE-0229) existed to feed
it. Unlike `DbSecretStore`, these values are not sensitive: they are read back for editing, so the
row stores them in the clear (no encryption), matching the file store. SQLAlchemy is imported
lazily (behind the `db` extra), so the default serve/CLI path never loads it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bajutsu.serve.provider_store import (
    PersistedProviderSettings,
    decode,
    encode_settings,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class DbProviderSettingsStore:
    """A `ProviderSettingsStore` backed by the ``provider_settings`` table, scoping every row to
    *org_id* (BE-0229). One instance serves one org, mirroring how `DbSecretStore` and the
    object-storage seams are built per org."""

    def __init__(self, engine: Engine, org_id: str) -> None:
        self._engine = engine
        self._org_id = org_id

    def load(self) -> PersistedProviderSettings | None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import ProviderSettingsRow

        with Session(self._engine) as session:
            row = session.get(ProviderSettingsRow, self._org_id)
            if row is None:
                return None
            # Validate through the shared decoder rather than trusting the row: a hand-edited DB
            # can carry a non-string leaf, and the boot/load path expects the same failure the file
            # store raises. `decode` re-checks the JSON-column shape it wrote.
            return decode(
                {"provider": row.provider, "settings": row.settings or {}},
                f"provider_settings[{self._org_id}]",
            )

    def save(self, data: PersistedProviderSettings) -> None:
        from sqlalchemy.orm import Session

        from bajutsu.serve.server.models import ProviderSettingsRow

        payload = encode_settings(data.settings)
        with Session(self._engine) as session:
            row = session.get(ProviderSettingsRow, self._org_id)
            if row is None:
                session.add(
                    ProviderSettingsRow(
                        org_id=self._org_id, provider=data.provider, settings=payload
                    )
                )
            else:  # overwrite in place — the selection is replaced wholesale on each save
                row.provider = data.provider
                row.settings = payload
            session.commit()
