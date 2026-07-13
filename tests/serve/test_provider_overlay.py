"""Per-org AI provider resolution and the per-job env overlay (BE-0229).

The load-bearing change: the serve provider selection resolves *per organization* into a per-job
env overlay, rather than a single process-global `os.environ`. These tests cover the pure overlay
builder (`provider_env`), the spawn-env merge that makes it authoritative (`_spawn_env`), the
tenant-isolation guarantee (two orgs resolve independent selections, neither touching the process
env), and that the overlay travels in the worker job spec.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from bajutsu import ai_config as aic
from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import _spawn_env
from bajutsu.serve.operations.config import provider_env, resolve_provider_env
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.provider_store import DbProviderSettingsStore
from bajutsu.serve.server.worker_job import job_spec
from bajutsu.serve.state import Job, OrgProviderSettings, ProviderSettings, StoreBundle

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"


# --- provider_env: the pure overlay builder --------------------------------------------


def test_provider_env_is_empty_when_nothing_is_selected() -> None:
    """No provider → an empty overlay, so a spawned job inherits its env unchanged (zero-config)."""
    assert provider_env(OrgProviderSettings()) == {}


def test_provider_env_api_key_emits_model_and_effort() -> None:
    settings = OrgProviderSettings(
        provider="api-key", slots={"api-key": ProviderSettings(model="claude-x", effort="high")}
    )
    assert provider_env(settings) == {
        aic.PROVIDER_ENV: "api-key",
        aic.MODEL_ENV: "claude-x",
        aic.EFFORT_ENV: "high",
    }


def test_provider_env_omits_a_blank_model() -> None:
    """A blank model emits no MODEL_ENV; the spawn strips the managed vars first, so absence resolves
    to the provider's default rather than a stale launch value."""
    settings = OrgProviderSettings(provider="api-key", slots={"api-key": ProviderSettings()})
    assert provider_env(settings) == {aic.PROVIDER_ENV: "api-key"}


def test_provider_env_bedrock_emits_model_and_region() -> None:
    settings = OrgProviderSettings(
        provider="bedrock",
        slots={"bedrock": ProviderSettings(model=_BEDROCK_MODEL, region="us-east-1")},
    )
    assert provider_env(settings) == {
        aic.PROVIDER_ENV: "bedrock",
        aic.BEDROCK_MODEL_ENV: _BEDROCK_MODEL,
        "AWS_REGION": "us-east-1",
    }


def test_provider_env_bedrock_without_region_omits_aws_region() -> None:
    settings = OrgProviderSettings(
        provider="bedrock", slots={"bedrock": ProviderSettings(model=_BEDROCK_MODEL)}
    )
    assert provider_env(settings) == {
        aic.PROVIDER_ENV: "bedrock",
        aic.BEDROCK_MODEL_ENV: _BEDROCK_MODEL,
    }


def test_provider_env_carries_the_output_language() -> None:
    settings = OrgProviderSettings(
        provider="api-key", slots={"api-key": ProviderSettings()}, language="ja"
    )
    assert provider_env(settings) == {aic.PROVIDER_ENV: "api-key", aic.LANGUAGE_ENV: "ja"}


# --- _spawn_env: the overlay is authoritative ------------------------------------------


def test_spawn_env_applies_the_overlay_and_strips_stale_managed_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the overlay names a provider, the Bajutsu-managed vars are cleared from the inherited env
    first, so a stale launch-env value never leaks through into the job."""
    monkeypatch.setenv(aic.PROVIDER_ENV, "stale-launch-provider")
    monkeypatch.setenv(aic.MODEL_ENV, "stale-launch-model")
    job = Job(env_overlay={aic.PROVIDER_ENV: "bedrock", aic.BEDROCK_MODEL_ENV: _BEDROCK_MODEL})
    e = _spawn_env(job)
    assert e[aic.PROVIDER_ENV] == "bedrock"
    assert e[aic.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
    assert aic.MODEL_ENV not in e  # the stale general-model override was stripped, not inherited


def test_spawn_env_empty_overlay_leaves_the_inherited_env_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No overlay (nothing selected) → the inherited env passes through unchanged (zero-config)."""
    monkeypatch.setenv(aic.PROVIDER_ENV, "launch-provider")
    e = _spawn_env(Job())
    assert e[aic.PROVIDER_ENV] == "launch-provider"


def test_spawn_env_never_strips_aws_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """AWS_REGION is a general AWS setting, not provider-managed: the overlay only ever adds it, so a
    launch-env region survives an overlay that names a provider without one."""
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    e = _spawn_env(Job(env_overlay={aic.PROVIDER_ENV: "api-key"}))
    assert e["AWS_REGION"] == "eu-west-1"


# --- tenant isolation: two orgs, independent selections --------------------------------


def _multi_org_state(tmp_path: Path, engine):  # type: ignore[no-untyped-def]
    """A hosted-shaped state: alice→acme, bob→globex, each org's provider settings a DB-backed row."""
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    for org, member in (("acme", "alice"), ("globex", "bob")):
        repo.ensure_org(org, slug=org, name=org)
        repo.upsert_user(member, org_id=org, github_login=member, email=f"{member}@x")
    state = srv.ServeState(runs_dir=tmp_path / "runs", repository=repo)
    state.org_stores = lambda org: StoreBundle(
        state.artifacts,
        state.scenarios,
        state.baselines,
        state.secrets,
        DbProviderSettingsStore(engine, org),
    )
    return state


def test_two_orgs_resolve_independent_provider_selections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of BE-0229: one org's save never changes another org's AI runs, and it never
    touches the shared process env."""
    for var in (
        aic.PROVIDER_ENV,
        aic.MODEL_ENV,
        aic.BEDROCK_MODEL_ENV,
        aic.EFFORT_ENV,
        aic.LANGUAGE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    engine = create_engine("sqlite://")
    state = _multi_org_state(tmp_path, engine)

    ops.set_provider(
        state, {"provider": "bedrock", "region": "us-east-1", "model": _BEDROCK_MODEL}, "alice"
    )
    ops.set_provider(state, {"provider": "api-key", "aiModel": "claude-y"}, "bob")

    acme = resolve_provider_env(state, "acme")
    globex = resolve_provider_env(state, "globex")
    assert acme[aic.PROVIDER_ENV] == "bedrock" and acme[aic.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
    assert globex[aic.PROVIDER_ENV] == "api-key" and globex[aic.MODEL_ENV] == "claude-y"
    assert aic.PROVIDER_ENV not in os.environ  # neither save touched the shared process env

    # A restart-fresh state loads each org's own choice back from its own row (per-org persistence).
    state2 = _multi_org_state(tmp_path, engine)
    assert resolve_provider_env(state2, "acme")[aic.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
    assert resolve_provider_env(state2, "globex")[aic.MODEL_ENV] == "claude-y"


# --- the overlay travels to a remote worker --------------------------------------------


def test_job_spec_carries_the_env_overlay() -> None:
    """The resolved overlay is JSON-serialized into the worker job spec (BE-0229), so a remote worker
    runs with the org's selection without holding any provider settings of its own."""
    spec = job_spec(Job(id="7", cmd=["run"], env_overlay={aic.PROVIDER_ENV: "bedrock"}))
    assert spec["env_overlay"] == {aic.PROVIDER_ENV: "bedrock"}
