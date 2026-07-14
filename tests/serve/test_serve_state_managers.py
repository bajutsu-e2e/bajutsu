"""Tests for the auth and provider-settings managers carved out of `ServeState` (BE-0248).

Each type is exercised standalone — constructed on its own, without a full `ServeState` whose
`__post_init__` resolves stores, secrets, and a launch dir the managers don't depend on — mirroring
the `JobRegistry` tests (BE-0198). The point is that each type's invariant is expressed by its own
boundary: the session/token/OAuth decisions for `SessionManager`, and the copy-on-read/copy-on-write
discipline that stops a caller aliasing the live provider-settings entry for `ProviderSettingsManager`.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve import state as srv_state
from bajutsu.serve.provider_store import LocalProviderSettingsStore, PersistedProviderSettings
from bajutsu.serve.sessions import InMemorySessionStore

# --- SessionManager (the auth/session/OAuth cluster) ---


def test_session_manager_open_when_no_token_configured() -> None:
    # With no shared token configured the server is open: check_token rejects every candidate, so
    # the gate falls through to session validation rather than a token compare (BE-0051).
    auth = srv_state.SessionManager()
    assert auth.token is None
    assert auth.check_token("anything") is False


def test_session_manager_checks_the_configured_token() -> None:
    # A configured token matches only its exact value (a constant-time compare).
    auth = srv_state.SessionManager(token="s3cret")
    assert auth.check_token("s3cret") is True
    assert auth.check_token("wrong") is False


def test_session_manager_issues_and_validates_sessions() -> None:
    # issue_session mints an opaque id the store then recognizes; an unknown id is not valid.
    auth = srv_state.SessionManager()
    sid = auth.issue_session()
    assert auth.valid_session(sid) is True
    assert auth.valid_session("not-a-session") is False


def test_session_manager_binds_the_oauth_identity_to_the_session() -> None:
    # An OAuth login binds the GitHub identity to the session, so a later layer maps it back (7b-2).
    auth = srv_state.SessionManager()
    sid = auth.issue_session(identity="alice")
    assert auth.sessions.identity(sid) == "alice"


def test_session_manager_uses_the_injected_session_store() -> None:
    # The store is a swappable seam (a server backend injects a Redis/SQL one), so a provided store
    # is the one used rather than a fresh default.
    store = InMemorySessionStore()
    auth = srv_state.SessionManager(sessions=store)
    sid = auth.issue_session()
    assert store.valid(sid) is True


# --- ProviderSettingsManager (the per-org AI provider-settings cluster) ---


def test_provider_manager_returns_none_for_an_unloaded_org() -> None:
    # None means "not loaded" — the operations layer lazily loads from the org's store on first
    # access (BE-0229); it must not be conflated with an empty selection.
    mgr = srv_state.ProviderSettingsManager()
    assert mgr.org_provider_settings("default") is None


def test_provider_manager_reads_return_an_independent_copy() -> None:
    # Copy-on-read: a read returns an independent copy (its slots dict too), so a caller mutating the
    # result can never reach back into the live entry.
    mgr = srv_state.ProviderSettingsManager()
    mgr.put_org_provider_settings(
        "default",
        srv_state.OrgProviderSettings(
            provider="ant", slots={"ant": srv_state.ProviderSettings(model="m")}, language="ja"
        ),
    )
    got = mgr.org_provider_settings("default")
    assert got is not None
    got.provider = "mutated"
    got.slots["ant"] = srv_state.ProviderSettings(model="tampered")
    got.slots["extra"] = srv_state.ProviderSettings()
    fresh = mgr.org_provider_settings("default")
    assert fresh is not None
    assert fresh.provider == "ant"
    assert fresh.slots == {"ant": srv_state.ProviderSettings(model="m")}


def test_provider_manager_stores_a_copy_on_write() -> None:
    # Copy-on-write: the seeded snapshot is copied in, so a later edit to the caller's own instance
    # (e.g. a store reload reusing the object) can't alias the live entry.
    seed = srv_state.OrgProviderSettings(
        provider="ant", slots={"ant": srv_state.ProviderSettings(model="m")}, language=""
    )
    mgr = srv_state.ProviderSettingsManager()
    mgr.put_org_provider_settings("default", seed)
    seed.provider = "changed"
    seed.slots["ant"] = srv_state.ProviderSettings(model="changed")
    fresh = mgr.org_provider_settings("default")
    assert fresh is not None
    assert fresh.provider == "ant"
    assert fresh.slots == {"ant": srv_state.ProviderSettings(model="m")}


def test_provider_manager_choice_keeps_other_providers_slots() -> None:
    # set_org_provider_choice writes the slot in place: a provider left behind keeps its remembered
    # slot, while the active provider and language are last-writer-wins (BE-0229/BE-0183/BE-0188).
    mgr = srv_state.ProviderSettingsManager()
    mgr.set_org_provider_choice(
        "default", provider="ant", slot=srv_state.ProviderSettings(model="a"), language="en"
    )
    mgr.set_org_provider_choice(
        "default", provider="bedrock", slot=srv_state.ProviderSettings(model="b"), language="ja"
    )
    got = mgr.org_provider_settings("default")
    assert got is not None
    assert got.provider == "bedrock"
    assert got.language == "ja"
    assert got.slots == {
        "ant": srv_state.ProviderSettings(model="a"),
        "bedrock": srv_state.ProviderSettings(model="b"),
    }


def test_provider_manager_persists_the_current_snapshot(tmp_path: Path) -> None:
    # persist re-snapshots the org's in-memory slots under its own lock and writes them through the
    # given store, so the one out-of-package caller (`_persist_provider_settings`) never reaches into
    # the manager's locks directly. The active provider is written as passed.
    mgr = srv_state.ProviderSettingsManager()
    mgr.set_org_provider_choice(
        "default", provider="ant", slot=srv_state.ProviderSettings(model="m"), language=""
    )
    store = LocalProviderSettingsStore(tmp_path / "provider-settings.json")
    mgr.persist("default", "ant", store)
    loaded = store.load()
    assert loaded == PersistedProviderSettings(
        provider="ant", settings={"ant": srv_state.ProviderSettings(model="m")}
    )
