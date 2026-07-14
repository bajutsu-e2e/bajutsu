"""Tests for persisting the serve AI provider settings across restarts (BE-0184, per-org BE-0229).

A wired deployment writes the per-provider settings map plus the active provider choice to durable
storage — a JSON file on local serve, a per-org row on a hosted database — so a restart restores
what the operator last saved instead of resetting to the launch environment. Since BE-0229 the
restored choice seeds a *per-org* in-memory selection (resolved into each job's env overlay), never
the shared `os.environ` — so a hosted, multi-tenant serve restores each org its own choice. Real
files and a real ``ThreadingHTTPServer`` — no mocks.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from _shared import _get_json, _post, _serve, project

from bajutsu import ai_config as aic
from bajutsu import anthropic_client as ac
from bajutsu import serve as srv
from bajutsu.ai import resolved_provider
from bajutsu.serve.operations.config import (
    resolve_provider_env,
    restore_persisted_provider_settings,
)
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.provider_store import (
    LocalProviderSettingsStore,
    PersistedProviderSettings,
    ProviderSettingsError,
)
from bajutsu.serve.state import ProviderSettings, ProviderSettingsManager

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"

_PROVIDER_ENV_VARS = (
    aic.PROVIDER_ENV,
    aic.BEDROCK_MODEL_ENV,
    aic.MODEL_ENV,
    aic.EFFORT_ENV,
    ac.ANTHROPIC_KEY_ENV,
    aic.LANGUAGE_ENV,
    "AWS_REGION",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env() -> Iterator[None]:
    """Give every test a clean launch env and fully restore it afterwards.

    Since BE-0229 neither the endpoint nor the boot restore writes `os.environ`, but the env is
    still the fallback layer resolution reads when an org selected nothing — so a stray `BAJUTSU_AI_*`
    from another test would perturb these assertions. A plain snapshot/restore keeps each test
    starting from a clean env (the anthropic-client model-resolution tests, say, read
    `BAJUTSU_AI_MODEL` without setting it)."""
    saved = {var: os.environ.get(var) for var in _PROVIDER_ENV_VARS}
    for var in _PROVIDER_ENV_VARS:
        os.environ.pop(var, None)
    try:
        yield
    finally:
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value


def _default_overlay(state: srv.ServeState) -> dict[str, str]:
    """The `default` org's resolved AI provider env overlay — what a spawned job would receive
    (BE-0229). Empty when nothing is selected (the zero-config path falls back to the inherited env)."""
    return resolve_provider_env(state, DEFAULT_ORG)


def _default_slots(state: srv.ServeState) -> dict[str, ProviderSettings]:
    """The `default` org's remembered per-provider slots (BE-0183), or an empty map when none."""
    settings = state.providers.org_provider_settings(DEFAULT_ORG)
    return settings.slots if settings is not None else {}


# --- the store itself (unit) ------------------------------------------------------------


def test_local_store_round_trips_through_a_file(tmp_path: Path) -> None:
    """A saved snapshot reads back byte-for-byte through the JSON file."""
    store = LocalProviderSettingsStore(tmp_path / "provider-settings.json")
    data = PersistedProviderSettings(
        provider="bedrock",
        settings={
            "api-key": ProviderSettings(model="claude-x", effort="high"),
            "bedrock": ProviderSettings(model=_BEDROCK_MODEL, region="us-east-1"),
        },
    )
    store.save(data)
    assert store.load() == data


def test_local_store_load_is_none_when_absent(tmp_path: Path) -> None:
    """No file means nothing persisted — the zero-config path (BE-0101) reads None and falls
    back to today's env-derived defaults."""
    assert LocalProviderSettingsStore(tmp_path / "missing.json").load() is None


def test_local_store_rejects_a_corrupt_file(tmp_path: Path) -> None:
    """A malformed file fails loudly (determinism-first) rather than silently resetting — the
    boot path turns this into a visible warning and falls back, but the store never guesses."""
    path = tmp_path / "provider-settings.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ProviderSettingsError):
        LocalProviderSettingsStore(path).load()


def test_restore_skips_a_slot_for_an_unknown_provider(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-active slot naming a provider that no longer exists is skipped, not written into the
    in-memory map (which the Settings UI would otherwise surface). The valid active slot still
    restores."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    store_path.write_text(
        '{"provider": "api-key", "settings": '
        '{"api-key": {"model": "m"}, "legacy-gone": {"model": "x"}}}',
        encoding="utf-8",
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    remembered = _default_slots(state)
    assert "api-key" in remembered  # the valid active slot restored
    assert "legacy-gone" not in remembered  # the unknown slot was skipped
    assert "legacy-gone" in caplog.text


def test_rejects_a_non_string_leaf_field(tmp_path: Path) -> None:
    """A structurally valid file with a non-string leaf (e.g. a numeric model) is rejected, not
    coerced to a string — the store fails on a malformed value rather than guessing."""
    path = tmp_path / "provider-settings.json"
    path.write_text(
        '{"provider": "api-key", "settings": {"api-key": {"model": 123}}}', encoding="utf-8"
    )
    with pytest.raises(ProviderSettingsError):
        LocalProviderSettingsStore(path).load()


# --- boot restore end to end ------------------------------------------------------------


def test_saved_provider_survives_a_restart(tmp_path: Path) -> None:
    """Save bedrock through the Web UI, then a fresh state restores the choice from the file — the
    exact friction BE-0184 removes. The restored choice resolves into the org's job overlay."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"

    state1 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    server1, port1 = _serve(state1)
    try:
        code, body = _post(
            port1,
            "/api/provider",
            {"provider": "bedrock", "region": "us-east-1", "model": _BEDROCK_MODEL},
        )
        assert code == 200 and body["provider"] == "bedrock"
    finally:
        server1.shutdown()
        server1.server_close()

    assert store_path.exists()  # unlike the pre-BE-0184 behaviour, the choice is on disk

    state2 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    restore_persisted_provider_settings(state2)
    # boot restored the selection, so a spawned job's overlay carries the active provider's settings
    overlay = _default_overlay(state2)
    assert overlay[aic.PROVIDER_ENV] == "bedrock"
    assert overlay[aic.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
    assert overlay["AWS_REGION"] == "us-east-1"

    server2, port2 = _serve(state2)
    try:
        info = _get_json(port2, "/api/provider")
        assert info["provider"] == "bedrock"
        assert info["model"] == _BEDROCK_MODEL
        assert info["region"] == "us-east-1"
    finally:
        server2.shutdown()
        server2.server_close()


def test_restart_restores_the_per_provider_map(tmp_path: Path) -> None:
    """The per-provider memory (BE-0183) also survives: a model saved for a provider left
    behind is still there after a restart, not just the active provider's."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"

    state1 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    server1, port1 = _serve(state1)
    try:
        _post(port1, "/api/provider", {"provider": "claude-code", "aiModel": "claude-code-x"})
        _post(port1, "/api/provider", {"provider": "api-key", "aiModel": "claude-api-y"})
    finally:
        server1.shutdown()
        server1.server_close()

    state2 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    restore_persisted_provider_settings(state2)
    remembered = _default_slots(state2)
    assert remembered["claude-code"].model == "claude-code-x"
    assert remembered["api-key"].model == "claude-api-y"


def test_zero_config_is_untouched_when_nothing_is_persisted(tmp_path: Path) -> None:
    """With no persisted file, boot restore is a no-op: no selection is made, so a job's overlay is
    empty and the AI-free zero-config path (BE-0101) reads exactly as before."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(
            store=LocalProviderSettingsStore(runs.parent / "absent.json")
        ),
    )
    restore_persisted_provider_settings(state)
    assert _default_overlay(state) == {}  # nothing selected → the job inherits its env unchanged
    assert aic.PROVIDER_ENV not in os.environ  # the process env is never touched
    assert _provider_of(state) == "api-key"


def _provider_of(state: srv.ServeState) -> str:
    server, port = _serve(state)
    try:
        return str(_get_json(port, "/api/provider")["provider"])
    finally:
        server.shutdown()
        server.server_close()


def test_corrupt_file_falls_back_to_env_defaults_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed persisted file does not brick serve: boot restore logs a visible warning and
    resolution falls back to the env-derived defaults (loud, not silent)."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    store_path.write_text("{ not json", encoding="utf-8")
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    assert _default_overlay(state) == {}  # fell back, did not crash or seed a choice
    assert "provider" in caplog.text.lower()


def test_inconsistent_active_provider_falls_back_to_env(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A structurally valid file that names an active provider with no saved slot (e.g. hand-
    edited) is inconsistent: boot warns and falls back rather than seeding an invalid empty slot
    (which would set a blank Bedrock model)."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    store_path.write_text(
        '{"provider": "bedrock", "settings": {"api-key": {"model": "x"}}}', encoding="utf-8"
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    assert _default_overlay(state) == {}  # did not seed an inconsistent (blank-model) choice
    assert "provider" in caplog.text.lower()


def test_bedrock_with_empty_model_falls_back_to_env(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A hand-edited file with `provider: bedrock` but a blank model sails through the 'no slot'
    check — materializing it would set an empty (invalid) Bedrock model. The guard covers this too:
    an empty Bedrock model is treated as incomplete and boot warns + falls back."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    store_path.write_text(
        '{"provider": "bedrock", "settings": {"bedrock": {"model": ""}}}', encoding="utf-8"
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    assert _default_overlay(state) == {}  # no blank-model bedrock choice seeded
    assert "provider" in caplog.text.lower()


def test_persist_failure_keeps_the_session_change_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If the file can't be written (here: a regular file sits where the parent dir should be),
    the provider still switches for the session and the request succeeds — the failure is logged
    loudly, not turned into a 500."""
    scn_dir, cfg, runs = project(tmp_path)
    blocker = runs.parent / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")  # so mkdir(parents=True) fails
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(
            store=LocalProviderSettingsStore(blocker / "provider-settings.json")
        ),
    )
    server, port = _serve(state)
    try:
        with caplog.at_level("WARNING"):
            code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["provider"] == "ant"
        assert body["persisted"] is False  # the response tells the UI the choice was not saved
        # the session change took effect in memory (resolved into the org's job overlay)
        assert _default_overlay(state)[aic.PROVIDER_ENV] == "ant"
        assert "persist" in caplog.text.lower()
    finally:
        server.shutdown()
        server.server_close()


def test_persist_failure_from_a_non_oserror_degrades_to_session_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The persist seam now backs the hosted DB store too, whose `session.commit` fails with a
    SQLAlchemy error, not an `OSError` (BE-0229). Such a write failure must degrade to session-only
    just like the file store's — logged loudly, `persisted: False`, the choice standing for the
    session — rather than propagating out of `set_provider`, honoring the function's contract."""
    from sqlalchemy.exc import SQLAlchemyError

    from bajutsu.serve.operations.config import _persist_provider_settings
    from bajutsu.serve.state import StoreBundle

    class _RaisingStore:
        def load(self) -> None:
            return None

        def save(self, data: object) -> None:
            raise SQLAlchemyError("db down")  # what a real DbProviderSettingsStore.save would raise

    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    state.org_stores = lambda org: StoreBundle(
        state.artifacts, state.scenarios, state.baselines, state.secrets, _RaisingStore()
    )
    state.providers.set_org_provider_choice(
        DEFAULT_ORG, provider="ant", slot=ProviderSettings(), language=""
    )
    with caplog.at_level("WARNING"):
        result = _persist_provider_settings(state, DEFAULT_ORG, "ant")
    assert result is False  # a non-OSError write failure is caught, not propagated
    assert _default_overlay(state)[aic.PROVIDER_ENV] == "ant"  # the session change still stands
    assert "persist" in caplog.text.lower()


def test_no_store_reports_persisted_null(tmp_path: Path) -> None:
    """With no store wired (a server backend without a database), the response carries
    persisted: null — distinct from persisted: true (durably saved) so the hosted operator's
    'saved' and the local operator's 'durably saved' don't conflate."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path
    )  # no provider_settings_store
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["persisted"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_successful_save_reports_persisted_true(tmp_path: Path) -> None:
    """A normal save reports `persisted: true`, so the Settings panel shows no warning."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(
            store=LocalProviderSettingsStore(runs.parent / "provider-settings.json")
        ),
    )
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["persisted"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_invalid_effort_in_slot_is_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A persisted slot with an unrecognised effort level is skipped and logged — the same
    invariant set_provider enforces on write, now also checked on restore."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    store_path.write_text(
        '{"provider": "api-key", "settings": {"api-key": {"model": "m", "effort": "ultra", "region": ""}}}',
        encoding="utf-8",
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    # The active provider guard also failed (its slot was invalid), so resolution falls back.
    assert _default_overlay(state) == {}
    assert "invalid" in caplog.text.lower() or "provider" in caplog.text.lower()


def test_persist_lock_serializes_writes(tmp_path: Path) -> None:
    """The persistence lock + in-lock re-snapshot ensures the thread that finishes last writes the
    most up-to-date state: the file always reflects both mutations, not just the faster thread's."""
    import threading

    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )

    errors: list[Exception] = []
    last_provider: list[str] = []

    def save_from_thread(prov: str) -> None:
        try:
            from bajutsu.serve.operations.config import _persist_provider_settings

            state.providers.set_org_provider_choice(
                DEFAULT_ORG, provider=prov, slot=ProviderSettings(model=f"m-{prov}"), language=""
            )
            _persist_provider_settings(state, DEFAULT_ORG, prov)
            last_provider.append(prov)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=save_from_thread, args=(p,)) for p in ("api-key", "ant")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"errors in threads: {errors}"
    loaded = LocalProviderSettingsStore(store_path).load()
    assert loaded is not None
    # The last writer's provider is recorded as active.
    assert loaded.provider == last_provider[-1]
    # Both mutations are in the file — the re-snapshot inside _persist_lock picked them both up.
    assert "api-key" in loaded.settings
    assert "ant" in loaded.settings


def test_config_ai_block_wins_over_a_restored_value(tmp_path: Path) -> None:
    """The safety property the doc leans on: a restored choice only seeds the *env* layer of a
    spawned job (via its overlay), and a config `ai:` block still wins over the env (config > env),
    so a stale persisted provider can never override an explicit config."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    LocalProviderSettingsStore(store_path).save(
        PersistedProviderSettings(
            provider="bedrock",
            settings={"bedrock": ProviderSettings(model=_BEDROCK_MODEL, region="us-east-1")},
        )
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        providers=ProviderSettingsManager(store=LocalProviderSettingsStore(store_path)),
    )
    restore_persisted_provider_settings(state)
    assert (
        _default_overlay(state)[aic.PROVIDER_ENV] == "bedrock"
    )  # restore seeds the job's env layer
    # A config ai: block overrides that restored env value (config > env), for both provider and model.
    cfg_ai = aic.AiConfig(provider="api-key", model="cfg-model")
    assert resolved_provider(cfg_ai) == "api-key"
    assert aic.resolve_model("fallback", cfg_ai) == "cfg-model"


# --- local construction wires the store; the boot path restores it ----------------------


def test_local_build_state_wires_the_store(tmp_path: Path) -> None:
    """Local serve construction owns the file (a sibling of runs_dir); loading from it is the boot
    path's job (after logging is live), which `restore_persisted_provider_settings` triggers — so a
    malformed file is logged loudly at startup, not on the first request."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"
    LocalProviderSettingsStore(store_path).save(
        PersistedProviderSettings(
            provider="bedrock",
            settings={"bedrock": ProviderSettings(model=_BEDROCK_MODEL, region="eu-west-1")},
        )
    )
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=scn_dir,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=1,
        token=None,
        cwd=tmp_path,
    )
    assert isinstance(state.providers.store, LocalProviderSettingsStore)
    # Construction wires the store but does not eagerly load it — the in-memory entry is still absent
    # (a pure read that does not lazy-load), so nothing has been resolved from disk yet.
    assert state.providers.org_provider_settings(DEFAULT_ORG) is None
    restore_persisted_provider_settings(state)
    overlay = _default_overlay(state)
    assert overlay[aic.PROVIDER_ENV] == "bedrock"
    assert overlay[aic.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
