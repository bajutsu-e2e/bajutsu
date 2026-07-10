"""Tests for BE-0184: persisting the serve AI provider settings across restarts.

Local serve writes the per-provider settings map plus the active provider choice to a small
JSON file, so a restart restores what the operator last saved instead of resetting to the
launch environment (the pre-BE-0184 behaviour, where the choice lived only in ``os.environ``).
Real files and a real ``ThreadingHTTPServer`` — no mocks.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from _shared import _get_json, _post, _serve, project

from bajutsu import anthropic_client as ac
from bajutsu import serve as srv
from bajutsu.ai import resolved_provider
from bajutsu.serve.operations.config import restore_persisted_provider_settings
from bajutsu.serve.provider_store import (
    LocalProviderSettingsStore,
    PersistedProviderSettings,
    ProviderSettingsError,
)
from bajutsu.serve.state import ProviderSettings

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"

_PROVIDER_ENV_VARS = (
    ac.PROVIDER_ENV,
    ac.BEDROCK_MODEL_ENV,
    ac.MODEL_ENV,
    ac.EFFORT_ENV,
    ac.ANTHROPIC_KEY_ENV,
    ac.LANGUAGE_ENV,
    "AWS_REGION",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env() -> Iterator[None]:
    """Give every test a clean provider env and fully restore it afterwards.

    The endpoint and the boot restore write `os.environ` directly (not through monkeypatch), and a
    value that was absent at entry leaves no monkeypatch record to undo — so a plain snapshot/restore
    here is what keeps a saved provider from leaking into unrelated tests (e.g. the anthropic-client
    model-resolution tests, which read `BAJUTSU_AI_MODEL` without setting it)."""
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


def _forget_provider_env() -> None:
    """Drop the provider env vars to simulate a restart within a test; the autouse fixture still
    owns the final restore."""
    for var in _PROVIDER_ENV_VARS:
        os.environ.pop(var, None)


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
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    remembered = state.provider_settings_snapshot()
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
    """Save bedrock through the Web UI, drop the launch env (a restart), then a fresh state
    restores the choice from the file — the exact friction BE-0184 removes."""
    scn_dir, cfg, runs = project(tmp_path)
    store_path = runs.parent / "provider-settings.json"

    state1 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        provider_settings_store=LocalProviderSettingsStore(store_path),
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

    _forget_provider_env()  # a restart: the env that carried the selection is gone

    state2 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    restore_persisted_provider_settings(state2)
    # boot seeded the process env for the active provider, so spawned jobs inherit it too
    assert os.environ[ac.PROVIDER_ENV] == "bedrock"
    assert os.environ[ac.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
    assert os.environ["AWS_REGION"] == "us-east-1"

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
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    server1, port1 = _serve(state1)
    try:
        _post(port1, "/api/provider", {"provider": "claude-code", "aiModel": "claude-code-x"})
        _post(port1, "/api/provider", {"provider": "api-key", "aiModel": "claude-api-y"})
    finally:
        server1.shutdown()
        server1.server_close()

    _forget_provider_env()  # a restart, mid-test
    state2 = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    restore_persisted_provider_settings(state2)
    remembered = state2.provider_settings_snapshot()
    assert remembered["claude-code"].model == "claude-code-x"
    assert remembered["api-key"].model == "claude-api-y"


def test_zero_config_is_untouched_when_nothing_is_persisted(tmp_path: Path) -> None:
    """With no persisted file, boot restore is a no-op: the env-derived defaults stand and the
    AI-free zero-config path (BE-0101) reads exactly as before."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        provider_settings_store=LocalProviderSettingsStore(runs.parent / "absent.json"),
    )
    restore_persisted_provider_settings(state)
    assert ac.PROVIDER_ENV not in os.environ
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
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    assert ac.PROVIDER_ENV not in os.environ  # fell back, did not crash
    assert "provider" in caplog.text.lower()


def test_inconsistent_active_provider_falls_back_to_env(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A structurally valid file that names an active provider with no saved slot (e.g. hand-
    edited) is inconsistent: boot warns and falls back to the env defaults rather than seeding an
    invalid empty slot (which would set a blank Bedrock model)."""
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
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    assert ac.PROVIDER_ENV not in os.environ  # did not seed an inconsistent active provider
    assert ac.BEDROCK_MODEL_ENV not in os.environ  # crucially, no blank Bedrock model
    assert "provider" in caplog.text.lower()


def test_bedrock_with_empty_model_falls_back_to_env(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A hand-edited file with `provider: bedrock` but a blank model sails through the 'no slot'
    check — `_apply_provider_env` would then set BEDROCK_MODEL_ENV to "" (invalid for Bedrock).
    The guard now also covers this case: an empty Bedrock model is treated as incomplete and boot
    warns + falls back rather than materializing an invalid env."""
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
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    with caplog.at_level("WARNING"):
        restore_persisted_provider_settings(state)
    assert ac.PROVIDER_ENV not in os.environ
    assert ac.BEDROCK_MODEL_ENV not in os.environ  # no blank model seeded
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
        provider_settings_store=LocalProviderSettingsStore(blocker / "provider-settings.json"),
    )
    server, port = _serve(state)
    try:
        with caplog.at_level("WARNING"):
            code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["provider"] == "ant"
        assert body["persisted"] is False  # the response tells the UI the choice was not saved
        assert os.environ[ac.PROVIDER_ENV] == "ant"  # the session change took effect
        assert "persist" in caplog.text.lower()
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
        provider_settings_store=LocalProviderSettingsStore(runs.parent / "provider-settings.json"),
    )
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["persisted"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_config_ai_block_wins_over_a_restored_value(tmp_path: Path) -> None:
    """The safety property the doc leans on: a restored choice only seeds the *env* layer, and a
    config `ai:` block still wins over the env (config > env), so a stale persisted provider can
    never override an explicit config."""
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
        provider_settings_store=LocalProviderSettingsStore(store_path),
    )
    restore_persisted_provider_settings(state)
    assert os.environ[ac.PROVIDER_ENV] == "bedrock"  # restore seeded the env layer
    # A config ai: block overrides that restored env value (config > env), for both provider and model.
    cfg_ai = ac.AiConfig(provider="api-key", model="cfg-model")
    assert resolved_provider(cfg_ai) == "api-key"
    assert ac.resolve_model("fallback", cfg_ai) == "cfg-model"


# --- local construction wires the store; the boot path restores it ----------------------


def test_local_build_state_wires_the_store(tmp_path: Path) -> None:
    """Local serve construction owns the file (a sibling of runs_dir); restoring from it is the
    boot path's job (after logging is live), which `restore_persisted_provider_settings` does."""
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
    assert isinstance(state.provider_settings_store, LocalProviderSettingsStore)
    assert ac.PROVIDER_ENV not in os.environ  # construction does not seed the env; boot does
    restore_persisted_provider_settings(state)
    assert os.environ[ac.PROVIDER_ENV] == "bedrock"
    assert os.environ[ac.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
