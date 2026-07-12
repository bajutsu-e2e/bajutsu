"""The hosted per-org DB-backed ProviderSettingsStore (BE-0229), on in-memory SQLite.

Exercises the `DbProviderSettingsStore` contract like the rest of the system of record: a real
(in-memory) database, no mocks. Unlike the secret store these values are not sensitive — they are
read back for editing — so the store round-trips them in the clear, and (the load-bearing property
BE-0229 adds) it is scoped per org so one org's selection never leaks into another's.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from bajutsu.serve.provider_store import PersistedProviderSettings, ProviderSettingsError
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.provider_store import DbProviderSettingsStore
from bajutsu.serve.state import ProviderSettings

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _bedrock() -> PersistedProviderSettings:
    return PersistedProviderSettings(
        provider="bedrock",
        settings={
            "api-key": ProviderSettings(model="claude-x", effort="high"),
            "bedrock": ProviderSettings(model=_BEDROCK_MODEL, region="us-east-1"),
        },
    )


def test_load_is_none_when_absent() -> None:
    """An org that has never saved reads None — resolution then falls back to the env defaults."""
    assert DbProviderSettingsStore(_engine(), "acme").load() is None


def test_save_then_load_round_trips() -> None:
    store = DbProviderSettingsStore(_engine(), "acme")
    store.save(_bedrock())
    assert store.load() == _bedrock()


def test_overwrite_replaces_the_row() -> None:
    store = DbProviderSettingsStore(_engine(), "acme")
    store.save(_bedrock())
    store.save(
        PersistedProviderSettings(provider="api-key", settings={"api-key": ProviderSettings()})
    )
    loaded = store.load()
    assert loaded is not None
    assert loaded.provider == "api-key"
    assert set(loaded.settings) == {"api-key"}  # the old bedrock slot is gone


def test_settings_are_scoped_per_org() -> None:
    engine = _engine()
    DbProviderSettingsStore(engine, "acme").save(_bedrock())
    # A second org sees nothing the first saved — the row is keyed by org_id (BE-0229).
    assert DbProviderSettingsStore(engine, "globex").load() is None
    assert DbProviderSettingsStore(engine, "acme").load() == _bedrock()


def test_stored_in_the_clear_for_editing() -> None:
    """Not a secret: the model id is stored readable so the Settings UI can pre-populate it."""
    engine = _engine()
    DbProviderSettingsStore(engine, "acme").save(_bedrock())
    from sqlalchemy import select

    from bajutsu.serve.server.models import ProviderSettingsRow

    with engine.connect() as conn:
        row = conn.execute(select(ProviderSettingsRow.settings)).all()[0][0]
    assert row["bedrock"]["model"] == _BEDROCK_MODEL  # readable, not encrypted


def test_load_rejects_a_hand_edited_non_string_leaf() -> None:
    """A tampered row with a non-string leaf fails loudly through the shared decoder, the same as
    the file store — the DB store does not blindly trust its own column."""
    engine = _engine()
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import ProviderSettingsRow

    with Session(engine) as session:
        session.add(
            ProviderSettingsRow(
                org_id="acme", provider="api-key", settings={"api-key": {"model": 123}}
            )
        )
        session.commit()
    with pytest.raises(ProviderSettingsError):
        DbProviderSettingsStore(engine, "acme").load()
