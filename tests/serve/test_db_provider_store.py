"""The hosted per-org DB-backed ProviderSettingsStore (BE-0229), on in-memory SQLite in the gate
and, behind the `postgres` marker, against a real Postgres service in the serve-db.yml lane (BE-0309).

Exercises the `DbProviderSettingsStore` contract like the rest of the system of record: a real
database, no mocks. Unlike the secret store these values are not sensitive — they are
read back for editing — so the store round-trips them in the clear, and (the load-bearing property
BE-0229 adds) it is scoped per org so one org's selection never leaks into another's.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from bajutsu.serve.provider_store import PersistedProviderSettings, ProviderSettingsError
from bajutsu.serve.server.models import Base, Org
from bajutsu.serve.server.provider_store import DbProviderSettingsStore
from bajutsu.serve.state import ProviderSettings

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"

# The orgs these tests save settings for. Seeded so the org_id FK holds on Postgres — the SQLite
# gate leaves FKs off, so it never needed the parent rows; seeding keeps each test dialect-agnostic.
_ORGS = ("acme", "globex")


def _engine(serve_engine: Callable[..., Engine]) -> Engine:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(Org(id=org_id, slug=org_id, name=org_id) for org_id in _ORGS)
        session.commit()
    return engine


def _bedrock() -> PersistedProviderSettings:
    return PersistedProviderSettings(
        provider="bedrock",
        settings={
            "api-key": ProviderSettings(model="claude-x", effort="high"),
            "bedrock": ProviderSettings(model=_BEDROCK_MODEL, region="us-east-1"),
        },
    )


def test_load_is_none_when_absent(serve_engine: Callable[..., Engine]) -> None:
    """An org that has never saved reads None — resolution then falls back to the env defaults."""
    assert DbProviderSettingsStore(_engine(serve_engine), "acme").load() is None


def test_save_then_load_round_trips(serve_engine: Callable[..., Engine]) -> None:
    store = DbProviderSettingsStore(_engine(serve_engine), "acme")
    store.save(_bedrock())
    assert store.load() == _bedrock()


def test_overwrite_replaces_the_row(serve_engine: Callable[..., Engine]) -> None:
    store = DbProviderSettingsStore(_engine(serve_engine), "acme")
    store.save(_bedrock())
    store.save(
        PersistedProviderSettings(provider="api-key", settings={"api-key": ProviderSettings()})
    )
    loaded = store.load()
    assert loaded is not None
    assert loaded.provider == "api-key"
    assert set(loaded.settings) == {"api-key"}  # the old bedrock slot is gone


def test_settings_are_scoped_per_org(serve_engine: Callable[..., Engine]) -> None:
    engine = _engine(serve_engine)
    DbProviderSettingsStore(engine, "acme").save(_bedrock())
    # A second org sees nothing the first saved — the row is keyed by org_id (BE-0229).
    assert DbProviderSettingsStore(engine, "globex").load() is None
    assert DbProviderSettingsStore(engine, "acme").load() == _bedrock()


def test_stored_in_the_clear_for_editing(serve_engine: Callable[..., Engine]) -> None:
    """Not a secret: the model id is stored readable so the Settings UI can pre-populate it."""
    engine = _engine(serve_engine)
    DbProviderSettingsStore(engine, "acme").save(_bedrock())
    from sqlalchemy import select

    from bajutsu.serve.server.models import ProviderSettingsRow

    with engine.connect() as conn:
        row = conn.execute(select(ProviderSettingsRow.settings)).all()[0][0]
    assert row["bedrock"]["model"] == _BEDROCK_MODEL  # readable, not encrypted


def test_load_rejects_a_hand_edited_non_string_leaf(serve_engine: Callable[..., Engine]) -> None:
    """A tampered row with a non-string leaf fails loudly through the shared decoder, the same as
    the file store — the DB store does not blindly trust its own column."""
    engine = _engine(serve_engine)
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
