"""The hosted encrypted SecretStore (BE-0136 write-once secrets), on in-memory SQLite.

Exercises the `DbSecretStore` contract the same way the gate exercises the rest of the system of
record: real Fernet encryption, a real (in-memory) database, no mocks. The load-bearing guarantee
is that the stored ciphertext never contains the plaintext, and that no operation ever hands the
plaintext back — only a masked preview.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bajutsu.serve.server.db import engine_from_url
from bajutsu.serve.server.models import Base, Secret
from bajutsu.serve.server.secrets import DbSecretStore, fernet_from_env


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_set_then_describe_returns_masked_only() -> None:
    store = DbSecretStore(_engine(), "default", Fernet(Fernet.generate_key()))
    assert store.describe("aiApiKey") is None  # unset

    masked = store.set("aiApiKey", "sk-ant-secret-12345", updated_by="alice")
    assert masked == "sk-a…2345"
    assert store.describe("aiApiKey") == "sk-a…2345"


def test_scenario_declared_secret_round_trips_encrypted() -> None:
    # BE-0274: a scenario's own declared secret name (an env-var name, not one of the three fixed
    # operator-credential logical names) is stored per org through the same generic store — set →
    # describe returns the mask, and the ciphertext column never holds the plaintext. This pins the
    # BE-0136 generalization the hosted storage path relies on.
    engine = _engine()
    store = DbSecretStore(engine, "acme", Fernet(Fernet.generate_key()))
    assert store.describe("LOGIN_PASSWORD") is None  # unset

    assert store.set("LOGIN_PASSWORD", "hunter2-secret") == "hunt…cret"
    assert store.describe("LOGIN_PASSWORD") == "hunt…cret"
    with engine.connect() as conn:
        stored = conn.execute(select(Secret.ciphertext)).all()[0][0]
    assert "hunter2-secret" not in stored and "secret" not in stored


def test_stored_ciphertext_never_holds_the_plaintext() -> None:
    engine = _engine()
    store = DbSecretStore(engine, "default", Fernet(Fernet.generate_key()))
    store.set("aiApiKey", "sk-ant-secret-12345")

    with engine.connect() as conn:
        rows = conn.execute(select(Secret.ciphertext)).all()
    assert rows  # a row was written
    stored = rows[0][0]
    assert "sk-ant-secret-12345" not in stored
    assert "secret" not in stored


def test_overwrite_rotates_without_reading_the_old_value() -> None:
    store = DbSecretStore(_engine(), "default", Fernet(Fernet.generate_key()))
    store.set("aiApiKey", "sk-ant-first-11111")
    store.set("aiApiKey", "sk-ant-second-22222")
    assert store.describe("aiApiKey") == "sk-a…2222"


def test_empty_value_clears_the_secret() -> None:
    engine = _engine()
    store = DbSecretStore(engine, "default", Fernet(Fernet.generate_key()))
    store.set("aiApiKey", "sk-ant-secret-12345")

    assert store.set("aiApiKey", "") is None
    assert store.describe("aiApiKey") is None
    with engine.connect() as conn:
        assert conn.execute(select(Secret.ciphertext)).all() == []  # the row is gone


def test_secrets_are_scoped_per_org() -> None:
    engine = _engine()
    fernet = Fernet(Fernet.generate_key())
    DbSecretStore(engine, "acme", fernet).set("aiApiKey", "sk-ant-acme-11111")

    # A second org sees nothing the first org set — the store is keyed by (org_id, name).
    assert DbSecretStore(engine, "globex", fernet).describe("aiApiKey") is None
    assert DbSecretStore(engine, "acme", fernet).describe("aiApiKey") == "sk-a…1111"


def test_set_persists_the_updated_by_audit_provenance() -> None:
    # `updated_by` is best-effort audit metadata: who last wrote the secret. There is no reader on
    # the seam yet, so assert it lands in the row directly (BE-0136).
    engine = _engine()
    store = DbSecretStore(engine, "acme", Fernet(Fernet.generate_key()))
    store.set("aiApiKey", "sk-ant-first-11111", updated_by="alice")
    store.set("aiApiKey", "sk-ant-second-22222", updated_by="bob")  # overwrite by a different user
    with Session(engine) as session:
        row = session.get(Secret, {"org_id": "acme", "name": "aiApiKey"})
        assert row is not None
        assert row.updated_by == "bob"  # the latest identified writer wins

    # An overwrite by an unidentified caller (token/Bearer request, no actor) rotates the value but
    # keeps the last known writer, rather than erasing the audit trail with None.
    store.set("aiApiKey", "sk-ant-third-33333", updated_by=None)
    with Session(engine) as session:
        row = session.get(Secret, {"org_id": "acme", "name": "aiApiKey"})
        assert row is not None
        assert row.updated_by == "bob"


def test_describe_fails_loud_when_the_key_cannot_decrypt() -> None:
    # A rotated / wrong BAJUTSU_SECRETS_KEY must surface loudly, never be silently read as "unset"
    # (which would mislead an admin into thinking no key is configured). The stored row still exists;
    # describe raises rather than returning None (BE-0136, "fail loudly").
    engine = _engine()
    DbSecretStore(engine, "acme", Fernet(Fernet.generate_key())).set("aiApiKey", "sk-ant-secret-1")

    rotated = DbSecretStore(engine, "acme", Fernet(Fernet.generate_key()))  # a different master key
    with pytest.raises(InvalidToken):
        rotated.describe("aiApiKey")


def test_no_plaintext_get_reachable() -> None:
    # Like the local store, the hosted store exposes no plaintext read (BE-0136).
    assert not hasattr(DbSecretStore, "get")


def test_fernet_from_env_requires_the_key(monkeypatch) -> None:
    monkeypatch.delenv("BAJUTSU_SECRETS_KEY", raising=False)
    assert fernet_from_env() is None
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    assert fernet_from_env() is not None


def test_fernet_from_env_rejects_a_malformed_key_with_a_named_error(monkeypatch) -> None:
    # A malformed key fails loud, but the message names the offending variable so a startup failure
    # is diagnosable rather than a bare cryptography error.
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", "not-a-valid-fernet-key")
    with pytest.raises(ValueError, match="BAJUTSU_SECRETS_KEY"):
        fernet_from_env()


def test_engine_from_url_is_the_shared_helper() -> None:
    # DbSecretStore rides the same engine the Repository does, so a server wires one engine.
    engine = engine_from_url("sqlite://")
    Base.metadata.create_all(engine)
    store = DbSecretStore(engine, "default", Fernet(Fernet.generate_key()))
    store.set("aiApiKey", "sk-ant-shared-33333")
    assert store.describe("aiApiKey") == "sk-a…3333"
