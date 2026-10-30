"""Tests for config resolution (team defaults x per-target)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from bajutsu.common.config import (
    AndroidConfig,
    Effective,
    IosConfig,
    WebConfig,
    load_config,
    parse_config_dict,
    resolve,
    xcuitest_targets_real_device,
)
from bajutsu.common.scenario import Interrupt, SystemAlertInterrupt, TipKitInterrupt


def _ios(eff: Effective) -> IosConfig:
    assert isinstance(eff.platform_config, IosConfig)
    return eff.platform_config


def _web(eff: Effective) -> WebConfig:
    assert isinstance(eff.platform_config, WebConfig)
    return eff.platform_config


CONFIG_YAML = (
    "defaults:\n"
    "  platforms:\n"
    "    ios:\n"
    "      runsOn: {ios: {locale: ja_JP}}\n"
    "  evidence:\n"
    "    capture: [screenshot.after, elements, actionLog]\n"
    "    redact:\n"
    "      headers: [Authorization, Cookie]\n"
    "      fields: [token, password]\n"
    "targets:\n"
    "  sample:\n"
    "    platform: ios\n"
    "    subject:\n"
    "      id: com.bajutsu.sample\n"
    "      launch:\n"
    "        env: {SAMPLE_UITEST: '1'}\n"
    "      idNamespaces: [home, list, counter, settings]\n"
    "    services:\n"
    "      mockServer: {cmd: serve, port: 8080, stubs: ./mocks/sample}\n"
    "    evidence:\n"
    "      redact:\n"
    "        labels: [カード番号]\n"
    "reservedNamespaces: [auth, nav]\n"
)


def test_resolve_sample() -> None:
    eff = resolve(load_config(CONFIG_YAML), "sample")
    assert _ios(eff).bundle_id == "com.bajutsu.sample"
    assert eff.backend == ["ios"]  # derived from `platform`
    assert eff.locale == "ja_JP"  # from defaults.platforms.ios
    assert eff.launch_env == {"SAMPLE_UITEST": "1"}
    assert eff.id_namespaces == ["home", "list", "counter", "settings"]
    assert eff.reserved_namespaces == ["auth", "nav"]
    assert eff.mock_server is not None and eff.mock_server.port == 8080


def test_redact_is_merged() -> None:
    eff = resolve(load_config(CONFIG_YAML), "sample")
    assert eff.redact.labels == ["カード番号"]  # from the app
    assert eff.redact.headers == ["Authorization", "Cookie"]  # from defaults
    assert eff.redact.fields == ["token", "password"]


def test_redact_unmask_headers_is_merged() -> None:
    # BE-0130: the escape hatch is a union like the other redact lists, so a default opt-out
    # declared at either level survives the merge rather than being silently dropped.
    cfg = load_config(
        "defaults:\n"
        "  evidence:\n"
        "    redact:\n"
        "      unmaskHeaders: [authorization]\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    evidence:\n"
        "      redact:\n"
        "        unmaskHeaders: [cookie]\n"
    )
    assert resolve(cfg, "s").redact.unmask_headers == ["authorization", "cookie"]


# BE-0047: the `ai` block resolves like any other setting (defaults overridden per target) into
# an AiConfig the AI paths read; an absent block resolves to None (env-only, as before).


def test_ai_block_resolves_from_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  ai: {provider: api-key, model: claude-opus-4-8, baseUrl: 'https://gw.internal/v1', keyEnv: MY_KEY}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    ai = resolve(cfg, "s").ai
    assert ai is not None
    assert ai.provider == "api-key"
    assert ai.model == "claude-opus-4-8"
    assert ai.base_url == "https://gw.internal/v1"
    assert ai.key_env == "MY_KEY"


def test_ai_block_target_overrides_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  ai: {provider: api-key, model: claude-opus-4-8, keyEnv: TEAM_KEY}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    ai: {model: claude-sonnet-x, keyEnv: APP_KEY}\n"
    )
    ai = resolve(cfg, "s").ai
    assert ai is not None
    assert ai.provider == "api-key"  # falls through from defaults
    assert ai.model == "claude-sonnet-x"  # target override
    assert ai.key_env == "APP_KEY"  # target override


def test_ai_block_absent_resolves_to_none() -> None:
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "s").ai is None


def test_ai_usage_ledger_and_pricing_resolve() -> None:
    # BE-0196: the ledger path and per-model pricing override resolve as plain dicts on AiConfig.
    cfg = load_config(
        "defaults:\n"
        "  ai:\n"
        "    provider: api-key\n"
        "    usageLedger: runs/mine.jsonl\n"
        "    pricing:\n"
        "      api-key/sonnet: {input: 3.0, output: 15.0, cacheWrite: 3.75, cacheRead: 0.3}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    ai = resolve(cfg, "s").ai
    assert ai is not None
    assert ai.usage_ledger == "runs/mine.jsonl"
    assert ai.pricing == {
        "api-key/sonnet": {"input": 3.0, "output": 15.0, "cacheWrite": 3.75, "cacheRead": 0.3}
    }


def test_ai_language_resolves_and_target_overrides() -> None:
    # BE-0188: `ai.language` merges like effort — defaults resolve, a target overrides field-wise.
    cfg = load_config(
        "defaults:\n"
        "  ai: {provider: api-key, language: en}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    ai: {language: ja}\n"
        "  t:\n"
        "    platform: ios\n"
        "    subject: {id: com.y}\n"
    )
    s_ai = resolve(cfg, "s").ai
    assert s_ai is not None and s_ai.language == "ja"  # override
    t_ai = resolve(cfg, "t").ai
    assert t_ai is not None and t_ai.language == "en"  # from defaults


def test_ai_block_keys_in_config_are_rejected() -> None:
    # A literal key in config is a foot-gun the schema forbids: only keyEnv (a NAME) is allowed.
    with pytest.raises(ValidationError):
        load_config(
            "defaults:\n"
            "  ai: {apiKey: sk-ant-secret}\n"
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
        )


def test_backend_is_derived_from_platform() -> None:
    # A target names one platform (BE-0452), so its backend is that platform as a one-entry list.
    cfg = load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "x").backend == ["ios"]


def test_target_overrides_platform_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  platforms:\n"
        "    ios:\n"
        "      runsOn: {ios: {locale: ja_JP}}\n"
        "targets:\n"
        "  x:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    runsOn: {ios: {locale: en_GB}}\n"
        "  y:\n"
        "    platform: ios\n"
        "    subject: {id: com.y}\n"
    )
    assert resolve(cfg, "x").locale == "en_GB"  # the target wins
    assert resolve(cfg, "y").locale == "ja_JP"  # falls through to defaults


def test_platform_defaults_apply_to_their_platform_alone() -> None:
    # `defaults.platforms.ios` holds a field a web target does not have; it must not reach one.
    cfg = load_config(
        "defaults:\n"
        "  platforms:\n"
        "    ios:\n"
        "      subject: {launch: {args: [-x]}}\n"
        "targets:\n"
        "  w:\n"
        "    platform: web\n"
        "    subject: {url: 'http://x/'}\n"
    )
    assert resolve(cfg, "w").launch_args == []


def test_a_required_field_may_come_from_platform_defaults() -> None:
    # Defaults merge before the target is validated, so `subject.id` may come from the defaults.
    cfg = load_config(
        "defaults:\n"
        "  platforms:\n"
        "    android:\n"
        "      subject: {id: com.shared}\n"
        "targets:\n"
        "  a: {platform: android}\n"
    )
    assert resolve(cfg, "a").app_identifier == "com.shared"


def test_a_target_clears_an_inherited_value_with_null() -> None:
    cfg = load_config(
        "defaults:\n"
        "  platforms: {ios: {subject: {reinstall: erase}}}\n"
        "targets:\n"
        "  x:\n"
        "    platform: ios\n"
        "    subject: {id: com.x, reinstall: null}\n"
    )
    assert resolve(cfg, "x").reinstall is None  # back to the built-in


def test_lists_replace_except_the_two_unions() -> None:
    cfg = load_config(
        "defaults:\n"
        "  evidence: {capture: [screenshot.after], redact: {fields: [password]}, secrets: [TEAM]}\n"
        "targets:\n"
        "  x:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    evidence: {capture: [elements], redact: {fields: [token]}, secrets: [OWN]}\n"
    )
    eff = resolve(cfg, "x")
    assert eff.secrets == ["TEAM", "OWN"]  # union: a target never unmasks a team-wide secret
    assert eff.redact.fields == ["password", "token"]  # union
    assert eff.capture == ["elements"]  # replaced


def test_unknown_defaults_platform_field_is_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match=r"defaults\.platforms\.ios: runsOn\.browser is not a destination this platform runs on",
    ):
        load_config(
            "defaults:\n  platforms:\n    ios:\n      runsOn: {browser: chromium}\ntargets: {}\n"
        )


def test_a_flat_defaults_runs_on_key_names_its_new_path() -> None:
    # BE-0469: the pre-grouping spelling fails under `defaults.platforms` too, naming where it moved.
    with pytest.raises(
        ValidationError,
        match=r"defaults\.platforms\.ios: runsOn\.locale moved to runsOn\.ios\.locale",
    ):
        load_config(
            "defaults:\n  platforms:\n    ios:\n      runsOn: {locale: ja_JP}\ntargets: {}\n"
        )


def test_an_unknown_defaults_group_field_lists_the_allowed_ones() -> None:
    # The groups other than `runsOn` still check their top-level keys by name.
    with pytest.raises(
        ValidationError, match=r"defaults\.platforms\.ios\.subject: unknown field\(s\) 'bogus'"
    ):
        load_config("defaults:\n  platforms:\n    ios:\n      subject: {bogus: 1}\ntargets: {}\n")


def test_unknown_defaults_platform_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid platform"):
        load_config("defaults:\n  platforms:\n    martian: {}\ntargets: {}\n")


@pytest.mark.parametrize(
    "removed",
    [
        "bundleId: com.x",
        "backend: [ios]",
        "deviceProvider: {kind: appium, endpoint: 'http://grid.local:4723'}",
        "cloudBatch: devicefarm",
        "cloudBatchBudget: 2",
        "requires: [ios18]",
        "setup: ./prelude.yaml",
        "device: iPhone 15",
    ],
)
def test_a_flat_or_removed_target_key_is_rejected(removed: str) -> None:
    # BE-0452 regrouped the keys and moved where a run happens out of the target; the error names
    # the keys a target accepts rather than Pydantic's generic extra-field message.
    with pytest.raises(ValidationError, match="a target accepts platform, subject, runsOn"):
        load_config(
            f"targets:\n  s:\n    platform: ios\n    subject: {{id: com.x}}\n    {removed}\n"
        )


@pytest.mark.parametrize("removed", ["backend: [ios]", "device: iPhone 15", "requires: [ios18]"])
def test_a_removed_defaults_key_is_rejected(removed: str) -> None:
    with pytest.raises(ValidationError, match="defaults accepts services, run"):
        load_config(f"defaults:\n  {removed}\ntargets: {{}}\n")


def test_a_target_needs_platform() -> None:
    with pytest.raises(ValidationError, match="needs `platform`"):
        load_config("targets:\n  s:\n    subject: {id: com.x}\n")


def test_unknown_app_raises() -> None:
    with pytest.raises(KeyError, match="unknown target"):
        resolve(load_config(CONFIG_YAML), "nope")


def test_minimal_defaults() -> None:
    cfg = load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n")
    eff = resolve(cfg, "x")
    assert eff.backend == ["ios"]
    assert eff.locale == "en_US"  # the built-in iOS locale (BE-0320)
    assert eff.capture == ["screenshot.after", "elements", "actionLog"]
    assert _ios(eff).app_path is None  # absent unless configured
    assert eff.evidence_dirs.scenarios is None  # absent unless configured


def test_app_path_parsed() -> None:
    cfg = load_config(
        "targets:\n  x:\n    platform: ios\n    subject: {id: com.x, path: build/X.app}\n"
    )
    assert _ios(resolve(cfg, "x")).app_path == "build/X.app"


def test_scenarios_parsed() -> None:
    cfg = load_config(
        "targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n    paths: {scenarios: scn/dir}\n"
    )
    assert resolve(cfg, "x").evidence_dirs.scenarios == "scn/dir"


def test_ready_when_selector_parsed() -> None:
    cfg = load_config(
        "targets:\n"
        "  x:\n"
        "    platform: ios\n"
        "    subject:\n"
        "      id: com.x\n"
        "      startWhen:\n"
        "        exists: {id: onboarding.start}\n"
    )
    assert resolve(cfg, "x").start_when == {"id": "onboarding.start"}


def test_ready_when_defaults_to_none() -> None:
    assert (
        resolve(
            load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n"), "x"
        ).start_when
        is None
    )


def test_ready_when_candidate_list_parsed() -> None:
    # A readyWhen with a canonical-first candidate list (BE-0221) is accepted, like a scenario step.
    cfg = load_config(
        "targets:\n"
        "  x:\n"
        "    platform: ios\n"
        "    subject:\n"
        "      id: com.x\n"
        "      startWhen:\n"
        "        exists:\n"
        "          id: [stable.row.1, stable_row_1]\n"
    )
    assert resolve(cfg, "x").start_when == {"id": ["stable.row.1", "stable_row_1"]}


def test_ready_when_misordered_candidate_list_rejected() -> None:
    # readyWhen is validated the same way a scenario selector is: an underscore-first list (the
    # canonical dotted form not leading) fails loudly at load, not silently (BE-0221).
    with pytest.raises(ValidationError, match="canonical"):
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject:\n"
            "      id: com.x\n"
            "      startWhen:\n"
            "        exists:\n"
            "          id: [stable_row_1, stable.row.1]\n"
        )


def test_ready_when_idmatches_misordered_candidate_list_rejected() -> None:
    # idMatches in readyWhen is validated on its own branch, the same way as id (BE-0221).
    with pytest.raises(ValidationError, match="canonical"):
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject:\n"
            "      id: com.x\n"
            "      startWhen:\n"
            "        exists:\n"
            "          idMatches: [a_b, a.b]\n"
        )


def test_web_app_baseurl_no_bundleid() -> None:
    # A web app identifies its target by baseUrl and needs no bundleId — it carries a WebConfig,
    # which has no bundle_id field at all (the point of the per-platform split, BE-0126).
    cfg = load_config(
        "targets:\n"
        "  web:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:8787/index.html'}\n"
        "    paths: {scenarios: demos/web/scenarios}\n"
    )
    eff = resolve(cfg, "web")
    assert _web(eff).base_url == "http://127.0.0.1:8787/index.html"
    assert not hasattr(eff.platform_config, "bundle_id")
    assert eff.backend == ["web"]
    assert eff.evidence_dirs.scenarios == "demos/web/scenarios"
    assert _web(eff).headless is True  # the web backend runs headless unless opted out


def test_web_app_headless_override() -> None:
    # A web app can opt into a headed (visible) browser via `headless: false`; the
    # `bajutsu run --headed` flag and the Web UI's "show browser" toggle do the same per run.
    cfg = load_config(
        "targets:\n"
        "  web:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:8787/'}\n"
        "    driver: {headless: false}\n"
    )
    assert _web(resolve(cfg, "web")).headless is False


def test_web_app_browser_defaults_to_chromium() -> None:
    # The browser engine defaults to chromium, preserving today's single-engine behaviour (BE-0076).
    cfg = load_config(
        "targets:\n  web:\n    platform: web\n    subject: {url: 'http://127.0.0.1:8787/'}\n"
    )
    assert _web(resolve(cfg, "web")).browser == "chromium"


def test_web_app_browser_config_resolves() -> None:
    # A target can pin its engine via `browser`; it resolves straight onto the web sub-config (a
    # per-target knob, like headless).
    cfg = load_config(
        "targets:\n"
        "  web:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:8787/'}\n"
        "    runsOn:\n"
        "      browser: {kind: firefox}\n"
    )
    assert _web(resolve(cfg, "web")).browser == "firefox"


def test_web_app_unknown_browser_rejected_at_load() -> None:
    # A typo'd engine fails loudly at config load (the field_validator), not as a mid-run
    # AttributeError when the driver does getattr(pw, engine).
    with pytest.raises(ValidationError, match="invalid browser"):
        load_config(
            "targets:\n"
            "  web:\n"
            "    platform: web\n"
            "    subject: {url: 'http://x/'}\n"
            "    runsOn:\n"
            "      browser: {kind: safari}\n"
        )


def test_web_app_device_mode_defaults_to_desktop() -> None:
    # No deviceMode = desktop: the web backend keeps today's plain desktop context, so an existing
    # web config is unchanged (BE-0228).
    cfg = load_config(
        "targets:\n  web:\n    platform: web\n    subject: {url: 'http://127.0.0.1:8787/'}\n"
    )
    assert _web(resolve(cfg, "web")).device_mode == "desktop"


def test_web_app_device_mode_config_resolves() -> None:
    # A target drives a mobile face by naming a Playwright device preset; it resolves straight onto
    # the web sub-config (a per-target knob, like browser). The preset name itself is resolved
    # lazily in the driver, so config load never imports Playwright.
    cfg = load_config(
        "targets:\n"
        "  web:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:8787/'}\n"
        "    runsOn: {browser: {emulate: iPhone 13}}\n"
    )
    assert _web(resolve(cfg, "web")).device_mode == "iPhone 13"


def test_device_provider_defaults_to_none() -> None:
    # The config names no device provider (BE-0452): only an `appium` environment of the worker config
    # fills one, so a resolved target carries None and `acquire_device` takes the `local` provider.
    cfg = load_config("targets:\n  app:\n    platform: ios\n    subject: {id: com.example.app}\n")
    assert resolve(cfg, "app").device_provider is None


def test_web_app_launch_server_parsed() -> None:
    # `launchServer` declares how to bring up baseUrl's host for a run; readyUrl defaults to None
    # (run falls back to baseUrl), readyTimeout to 30s.
    eff = resolve(
        load_config(
            "targets:\n"
            "  web:\n"
            "    platform: web\n"
            "    subject: {url: 'http://127.0.0.1:8799/'}\n"
            "    services:\n"
            "      server: {cmd: uv run bajutsu serve --port 8799, readyTimeout: 60}\n"
        ),
        "web",
    )
    assert eff.launch_server is not None
    assert eff.launch_server.cmd == "uv run bajutsu serve --port 8799"
    assert eff.launch_server.ready_timeout == 60.0
    assert eff.launch_server.ready_url is None  # run falls back to baseUrl
    assert (
        resolve(
            load_config("targets:\n  web:\n    platform: web\n    subject: {url: 'http://x/'}\n"),
            "web",
        ).launch_server
        is None
    )


def test_app_without_bundleid_or_baseurl_rejected() -> None:
    # Each platform's `subject` model requires its own identifier (BE-0452).
    with pytest.raises(ValidationError, match=r"subject\.id: Field required"):
        load_config("targets:\n  x:\n    platform: ios\n    paths: {scenarios: scn/dir}\n")


def test_legacy_apps_key_is_rejected() -> None:
    # Hard cutover (BE-0057): the renamed grammar keeps no `apps:` alias. `extra="forbid"` makes a
    # stale `apps:` config fail loudly at load time, rather than silently resolving to no targets.
    with pytest.raises(ValidationError):
        load_config("apps: { x: { bundleId: com.x } }")


def test_ios_app_carries_no_base_url() -> None:
    # An iOS target carries an IosConfig, which has no base_url field (BE-0126).
    cfg = load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n")
    eff = resolve(cfg, "x")
    assert isinstance(eff.platform_config, IosConfig)
    assert not hasattr(eff.platform_config, "base_url")


def test_baselines_parsed() -> None:
    cfg = load_config(
        "targets:\n"
        "  x:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    paths: {baselines: baselines/x}\n"
    )
    assert resolve(cfg, "x").evidence_dirs.baselines == "baselines/x"


def test_baselines_defaults_to_none() -> None:
    cfg = load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "x").evidence_dirs.baselines is None


def test_baselines_resolution_order() -> None:
    """_resolve_dir for baselines respects: --baselines flag > config > scenario-local default."""
    from bajutsu.run.cli import _resolve_dir

    eff_with = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    paths: {baselines: cfg/bl}\n"
        ),
        "x",
    )
    eff_without = resolve(
        load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n"), "x"
    )
    scenario_file = Path("/scenarios/app/smoke.yaml")

    # flag wins over everything
    assert _resolve_dir(
        "flag/bl", eff_with.evidence_dirs.baselines, scenario_file, "baselines"
    ) == Path("flag/bl")
    assert _resolve_dir(
        "flag/bl", eff_without.evidence_dirs.baselines, scenario_file, "baselines"
    ) == Path("flag/bl")

    # config used when no flag
    assert _resolve_dir("", eff_with.evidence_dirs.baselines, scenario_file, "baselines") == Path(
        "cfg/bl"
    )

    # scenario-local default when neither flag nor config
    assert _resolve_dir(
        "", eff_without.evidence_dirs.baselines, scenario_file, "baselines"
    ) == Path("/scenarios/app/baselines")


def test_schemas_parsed() -> None:
    cfg = load_config(
        "targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n    paths: {schemas: schemas/x}\n"
    )
    assert resolve(cfg, "x").evidence_dirs.schemas == "schemas/x"


def test_schemas_defaults_to_none() -> None:
    cfg = load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "x").evidence_dirs.schemas is None


def test_schemas_resolution_order() -> None:
    """_resolve_dir for schemas respects: --schemas flag > config > scenario-local default."""
    from bajutsu.run.cli import _resolve_dir

    eff_with = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    paths: {schemas: cfg/sc}\n"
        ),
        "x",
    )
    eff_without = resolve(
        load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n"), "x"
    )
    scenario_file = Path("/scenarios/app/smoke.yaml")

    assert _resolve_dir(
        "flag/sc", eff_with.evidence_dirs.schemas, scenario_file, "schemas"
    ) == Path("flag/sc")
    assert _resolve_dir("", eff_with.evidence_dirs.schemas, scenario_file, "schemas") == Path(
        "cfg/sc"
    )
    assert _resolve_dir("", eff_without.evidence_dirs.schemas, scenario_file, "schemas") == Path(
        "/scenarios/app/schemas"
    )


def test_goldens_parsed() -> None:
    cfg = load_config(
        "targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n    paths: {goldens: goldens/x}\n"
    )
    assert resolve(cfg, "x").evidence_dirs.goldens == "goldens/x"


def test_goldens_defaults_to_none() -> None:
    cfg = load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "x").evidence_dirs.goldens is None


def test_goldens_resolution_order() -> None:
    """_resolve_dir for goldens respects: --goldens flag > config > scenario-local default."""
    from bajutsu.run.cli import _resolve_dir

    eff_with = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    paths: {goldens: cfg/gl}\n"
        ),
        "x",
    )
    eff_without = resolve(
        load_config("targets:\n  x:\n    platform: ios\n    subject: {id: com.x}\n"), "x"
    )
    scenario_file = Path("/scenarios/app/smoke.yaml")

    # flag wins over everything
    assert _resolve_dir(
        "flag/gl", eff_with.evidence_dirs.goldens, scenario_file, "goldens"
    ) == Path("flag/gl")
    assert _resolve_dir(
        "flag/gl", eff_without.evidence_dirs.goldens, scenario_file, "goldens"
    ) == Path("flag/gl")

    # config used when no flag
    assert _resolve_dir("", eff_with.evidence_dirs.goldens, scenario_file, "goldens") == Path(
        "cfg/gl"
    )

    # scenario-local default when neither flag nor config
    assert _resolve_dir("", eff_without.evidence_dirs.goldens, scenario_file, "goldens") == Path(
        "/scenarios/app/goldens"
    )


def test_rebased_resolves_relative_paths_under_the_checkout_root() -> None:
    """Effective.rebased makes a Git config's relative path fields absolute under the checkout (BE-0063)."""
    eff = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x, path: build/A.app}\n"
            "    paths: {scenarios: e2e/scn, goldens: golden/data}\n"
        ),
        "x",
    )
    rebased = eff.rebased(Path("/co"))
    assert rebased.evidence_dirs.scenarios == "/co/e2e/scn"
    assert _ios(rebased).app_path == "/co/build/A.app"
    assert rebased.evidence_dirs.goldens == "/co/golden/data"


def test_rebased_resolves_xcuitest_test_runner() -> None:
    eff = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    driver:\n"
            "      runner: {testRunner: build/Runner.xctestrun}\n"
        ),
        "x",
    )
    rebased = eff.rebased(Path("/co"))
    xcuitest = _ios(rebased).xcuitest
    assert xcuitest is not None
    assert xcuitest.test_runner == "/co/build/Runner.xctestrun"


def test_rebased_preserves_xcuitest_device_type() -> None:
    # Rebasing reconstructs XcuitestConfig to rewrite path fields; deviceType must survive it, or a
    # `device` target would silently fall back to the Simulator in a checkout/upload run (BE-0238).
    eff = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    driver:\n"
            "      runner: {testRunner: build/Runner.xctestrun}\n"
            "    runsOn: {ios: {kind: device}}\n"
        ),
        "x",
    )
    xcuitest = _ios(eff.rebased(Path("/co"))).xcuitest
    assert xcuitest is not None
    assert xcuitest.device_type == "device"


def test_rebased_refuses_a_path_field_escaping_the_checkout() -> None:
    """A config path that climbs out of the checkout (`..` or absolute) is refused — confinement (BE-0051)."""
    for bad in ("../../etc", "/etc/passwd"):
        eff = resolve(
            load_config(
                "targets:\n"
                "  x:\n"
                "    platform: ios\n"
                f"    subject: {{id: com.x}}\n"
                f"    paths: {{scenarios: {bad}}}\n"
            ),
            "x",
        )
        with pytest.raises(ValueError, match="escapes the confinement root"):
            eff.rebased(Path("/co"))


def test_rebased_without_confinement_keeps_paths_outside_the_root() -> None:
    """`confine=False` (an operator-trusted local config, BE-0242) joins a `..`/absolute path instead of refusing it."""
    eff = resolve(
        load_config(
            "targets:\n"
            "  x:\n"
            "    platform: ios\n"
            "    subject: {id: com.x, path: /opt/App.app}\n"
            "    paths: {scenarios: ../sibling/scn}\n"
        ),
        "x",
    )
    rebased = eff.rebased(Path("/co/cfg"), confine=False)
    assert Path(rebased.evidence_dirs.scenarios or "").resolve() == Path(
        "/co/sibling/scn"
    )  # sibling, not refused
    assert _ios(rebased).app_path == "/opt/App.app"  # an absolute value is kept as-is


# --- Platform discriminator (BE-0009 Slice 4) --- #


def test_platform_explicit_resolves_into_effective() -> None:
    # An explicit `platform` on the target is authoritative and reaches Effective.
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "s").platform == "ios"


def test_platform_defaults_apply_when_target_omits_it() -> None:
    # A team-wide `defaults.platform` flows to a target that doesn't override it.
    cfg = load_config("targets:\n  s:\n    platform: web\n    subject: {url: 'https://app.test'}\n")
    assert resolve(cfg, "s").platform == "web"


def test_platform_derived_from_backend_when_unset() -> None:
    # With no explicit platform anywhere, it's derived from the backend (today's implicit behavior),
    # so existing configs are unchanged: playwright -> web, xcuitest -> ios.
    web = load_config("targets:\n  s:\n    platform: web\n    subject: {url: 'https://app.test'}\n")
    assert resolve(web, "s").platform == "web"
    ios = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(ios, "s").platform == "ios"


def test_package_resolves_into_effective() -> None:
    # The Android identifier (peer of bundleId / baseUrl) resolves onto the Android sub-config.
    cfg = load_config("targets:\n  s:\n    platform: android\n    subject: {id: com.x.app}\n")
    eff = resolve(cfg, "s")
    assert eff.platform == "android"
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.package == "com.x.app"


# --- Per-platform effective sub-configs (BE-0126) --- #


def test_ios_target_yields_ios_sub_config() -> None:
    # An iOS target's platform-specific knobs land on an IosConfig, not on the common core.
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x, path: build/Demo.app, build: make app}\n"
    )
    eff = resolve(cfg, "s")
    assert eff.platform == "ios"
    assert isinstance(eff.platform_config, IosConfig)
    ios = eff.platform_config
    assert ios.bundle_id == "com.x"
    assert ios.app_path == "build/Demo.app"
    assert ios.build == "make app"


def test_web_target_yields_web_sub_config() -> None:
    # A web target's Playwright knobs land on a WebConfig.
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: web\n"
        "    subject: {url: 'https://app.test'}\n"
        "    runsOn:\n"
        "      browser: {kind: firefox}\n"
        "    driver: {headless: false}\n"
    )
    eff = resolve(cfg, "s")
    assert eff.platform == "web"
    assert isinstance(eff.platform_config, WebConfig)
    web = eff.platform_config
    assert web.base_url == "https://app.test"
    assert web.browser == "firefox"
    assert web.headless is False


def test_android_target_yields_android_sub_config() -> None:
    cfg = load_config("targets:\n  s:\n    platform: android\n    subject: {id: com.x.app}\n")
    eff = resolve(cfg, "s")
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.package == "com.x.app"


def test_android_native_z_flows_into_the_sub_config() -> None:
    # BE-0407 unit 18: the `nativeZ` reading (BE-0355) is asked for per target, because the node walk
    # answering it costs every Android read whether or not the app opted a view in. The alias and the
    # default both matter — a wrong alias would ship as a silently ignored config line.
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: android\n"
        "    subject: {id: com.x.app}\n"
        "    driver: {nativeZ: true}\n"
    )
    assert resolve(cfg, "s").platform_config == AndroidConfig(package="com.x.app", native_z=True)


def test_android_native_z_defaults_off() -> None:
    # Off unless asked for: the walk reports nothing at all for an app that opted no view in, which
    # is every app but the ones BE-0355 was built for.
    cfg = load_config("targets:\n  s:\n    platform: android\n    subject: {id: com.x.app}\n")
    platform_config = resolve(cfg, "s").platform_config
    assert isinstance(platform_config, AndroidConfig)
    assert platform_config.native_z is False


def test_android_grant_permissions_flow_into_the_sub_config() -> None:
    # BE-0210: runtime permissions to pre-grant live in config (app-specific), reaching AndroidConfig.
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: android\n"
        "    subject:\n"
        "      id: com.x.app\n"
        "      grantPermissions: [android.permission.POST_NOTIFICATIONS, android.permission.CAMERA]\n"
    )
    eff = resolve(cfg, "s")
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.grant_permissions == [
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.CAMERA",
    ]


def test_android_grant_permissions_default_empty() -> None:
    cfg = load_config("targets:\n  s:\n    platform: android\n    subject: {id: com.x.app}\n")
    eff = resolve(cfg, "s")
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.grant_permissions == []


def test_web_target_carries_no_ios_fields() -> None:
    # The whole point of the split: a web target's config never exposes iOS-only knobs.
    cfg = load_config("targets:\n  s:\n    platform: web\n    subject: {url: 'https://app.test'}\n")
    eff = resolve(cfg, "s")
    assert not isinstance(eff.platform_config, IosConfig)
    assert not hasattr(eff.platform_config, "bundle_id")


def test_ios_target_carries_no_web_fields() -> None:
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    eff = resolve(cfg, "s")
    assert not isinstance(eff.platform_config, WebConfig)
    assert not hasattr(eff.platform_config, "base_url")


def test_rebased_rebases_app_path_inside_ios_config() -> None:
    # rebased() confines the iOS appPath to the checkout root, inside the sub-config.
    cfg = load_config(
        "targets:\n  s:\n    platform: ios\n    subject: {id: com.x, path: build/Demo.app}\n"
    )
    eff = resolve(cfg, "s").rebased(Path("/co"))
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.app_path == str(Path("/co") / "build/Demo.app")


def test_unknown_platform_is_rejected_at_load() -> None:
    with pytest.raises(ValidationError, match="platform"):
        load_config("targets:\n  s:\n    platform: martian\n    subject: {id: com.x}\n")


def test_ios_platform_requires_bundle_id() -> None:
    with pytest.raises(ValidationError, match=r"subject\.id: Field required"):
        load_config("targets:\n  s: {platform: ios}\n")


def test_web_platform_requires_base_url() -> None:
    with pytest.raises(ValidationError, match=r"subject\.url: Field required"):
        load_config("targets:\n  s: {platform: web}\n")


def test_android_platform_rejects_another_platforms_field() -> None:
    # A key another platform takes fails as unknown, listing what the block accepts (BE-0452).
    with pytest.raises(ValidationError, match="allowed fields: startWhen, idNamespaces, id"):
        load_config(
            "targets:\n  s:\n    platform: android\n    subject: {id: com.x, url: 'http://x/'}\n"
        )


# --- BE-0019: xcuitest config fields ---


def test_xcuitest_config_resolves() -> None:
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    driver:\n"
        "      runner: {testRunner: build/Runner.xctestrun, build: xcodebuild build-for-testing}\n"
    )
    eff = resolve(cfg, "s")
    xcuitest = _ios(eff).xcuitest
    assert xcuitest is not None
    assert xcuitest.test_runner == "build/Runner.xctestrun"
    assert xcuitest.build == "xcodebuild build-for-testing"


def test_xcuitest_config_defaults_to_none() -> None:
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert _ios(resolve(cfg, "s")).xcuitest is None


def test_xcuitest_device_type_defaults_to_simulator() -> None:
    # An omitted deviceType keeps the pre-BE-0238 behaviour: drive the Simulator (BE-0019).
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    driver:\n"
        "      runner: {testRunner: build/Runner.xctestrun}\n"
    )
    xcuitest = _ios(resolve(cfg, "s")).xcuitest
    assert xcuitest is not None
    assert xcuitest.device_type == "simulator"


def test_xcuitest_device_type_resolves_device() -> None:
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    driver:\n"
        "      runner: {testRunner: build/Runner.xctestrun}\n"
        "    runsOn: {ios: {kind: device}}\n"
    )
    xcuitest = _ios(resolve(cfg, "s")).xcuitest
    assert xcuitest is not None
    assert xcuitest.device_type == "device"


def test_xcuitest_device_type_invalid_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    driver:\n"
            "      runner: {testRunner: build/Runner.xctestrun}\n"
            "    runsOn: {ios: {kind: physical}}\n"
        )


# --- BE-0238: the real-device accessor backing the capability narrowing (Unit 3) --- #


def test_xcuitest_targets_real_device_true_for_device() -> None:
    # `runsOn.ios.kind: device` is the one shape that drives a physical device — the accessor the
    # Unit 3 capability narrowing consults to drop the simctl-backed DeviceControl / permission tokens.
    cfg = load_config(
        "targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n    runsOn: {ios: {kind: device}}\n"
    )
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is True


def test_xcuitest_targets_real_device_false_for_simulator() -> None:
    cfg = load_config(
        "targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n    runsOn: {ios: {kind: simulator}}\n"
    )
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is False


def test_xcuitest_targets_real_device_false_when_xcuitest_omitted() -> None:
    # An iOS target with no xcuitest block keeps the Simulator default (BE-0019), so it is not a real
    # device — the accessor must not raise on the missing block.
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is False


def test_xcuitest_targets_real_device_false_for_non_ios_target() -> None:
    # deviceType is iOS-only; an Android target can never target a real iOS device through XCUITest.
    cfg = load_config("targets:\n  s:\n    platform: android\n    subject: {id: com.example.app}\n")
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is False


# --- BE-0024: configurable doctor thresholds ---


def test_doctor_thresholds_resolve_from_defaults() -> None:
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "doctor: {idCoverageOk: 0.85, idCoverageFail: 0.6}\n"
    )
    eff = resolve(cfg, "s")
    assert eff.doctor_thresholds.ok_coverage == 0.85
    assert eff.doctor_thresholds.fail_coverage == 0.6


def test_doctor_thresholds_default_to_hardcoded_values() -> None:
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    eff = resolve(cfg, "s")
    assert eff.doctor_thresholds.ok_coverage == 0.9
    assert eff.doctor_thresholds.fail_coverage == 0.7


def test_doctor_ok_below_fail_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "doctor: {idCoverageOk: 0.5, idCoverageFail: 0.8}\n"
        )


def test_doctor_threshold_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\ndoctor: {idCoverageOk: 1.5}\n"
        )


# --- BE-0099: webhook notification config ---


def test_notify_config_resolves() -> None:
    cfg = load_config(
        "notify:\n"
        "- format: slack\n"
        "  url: ${secrets.SLACK_WEBHOOK_URL}\n"
        "  'on': [failure, change]\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    eff = resolve(cfg, "s")
    assert len(eff.notify) == 1
    assert eff.notify[0].format == "slack"
    assert eff.notify[0].url == "${secrets.SLACK_WEBHOOK_URL}"
    assert eff.notify[0].on == ["failure", "change"]
    assert eff.notify[0].targets == []


def test_notify_defaults_to_failure_event() -> None:
    cfg = load_config(
        "notify:\n"
        "- {format: slack, url: '${secrets.URL}'}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    assert resolve(cfg, "s").notify[0].on == ["failure"]


def test_notify_with_targets_filter() -> None:
    cfg = load_config(
        "notify:\n"
        "- format: slack\n"
        "  url: ${secrets.URL}\n"
        "  targets: [checkout, login]\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    assert resolve(cfg, "s").notify[0].targets == ["checkout", "login"]


def test_notify_unknown_format_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown notify format"):
        load_config(
            "notify:\n"
            "- {format: teams, url: '${secrets.URL}'}\n"
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
        )


def test_notify_unknown_event_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown notify event"):
        load_config(
            "notify:\n"
            "- format: slack\n"
            "  url: ${secrets.URL}\n"
            "  'on': [bogus]\n"
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
        )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("targets: { s: { platform: ios, app: { id: com.x } } }\n", r"targets\.<name>\.app moved"),
        (
            "defaults: { platforms: { ios: { app: { id: com.x } } } }\n"
            "targets: { s: { platform: ios, subject: { id: com.x } } }\n",
            r"defaults\.platforms\.ios\n.*`app` moved to `subject`",
        ),
        (
            "templates: { b: { app: { id: com.x } } }\n"
            "targets: { s: { extends: b, platform: ios, subject: { id: com.x } } }\n",
            r"templates\.b: app moved to `subject`",
        ),
    ],
)
def test_the_app_group_fails_naming_subject(text: str, message: str) -> None:
    # BE-0470 renamed the group; an old config fails rather than loading half-read.
    with pytest.raises(ValidationError, match=message):
        load_config(text)


def test_a_target_level_notify_names_the_top_level_list() -> None:
    # BE-0473: the top-level list alone says where results go.
    with pytest.raises(ValidationError, match=r"targets\.<name>\.notify moved to the top-level"):
        load_config(
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    notify:\n"
            "    - {format: slack, url: '${secrets.B}'}\n"
        )


def test_notify_absent_resolves_to_empty() -> None:
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "s").notify == []


def test_notify_on_accepts_single_string() -> None:
    cfg = load_config(
        "notify:\n"
        "- {format: slack, url: '${secrets.URL}', 'on': always}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    assert resolve(cfg, "s").notify[0].on == ["always"]


# --- BE-0165: visual compare engine config ---


def test_visual_compare_defaults_to_exact() -> None:
    cfg = load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n")
    assert resolve(cfg, "s").visual_compare == "exact"


def test_visual_compare_from_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  evidence: {visualCompare: pixelmatch}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
    )
    assert resolve(cfg, "s").visual_compare == "pixelmatch"


def test_visual_compare_target_overrides_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  evidence: {visualCompare: pixelmatch}\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    evidence: {visualCompare: exact}\n"
    )
    assert resolve(cfg, "s").visual_compare == "exact"


def test_visual_compare_target_only() -> None:
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    evidence: {visualCompare: pixelmatch}\n"
    )
    assert resolve(cfg, "s").visual_compare == "pixelmatch"


def test_visual_compare_invalid_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    evidence: {visualCompare: ssim}\n"
        )


def test_load_config_drops_top_level_orgs() -> None:
    # The org model is a serve concern the deterministic core does not understand (BE-0129). A run
    # in the hosted topology reads an org-bearing config, so the core loader must drop `orgs:` and
    # keep resolving targets rather than reject the whole document.
    cfg = load_config(
        "targets:\n"
        "  demo:\n"
        "    platform: ios\n"
        "    subject: {id: com.example.demo}\n"
        "orgs:\n"
        "  acme:\n"
        "    members: [alice]\n"
        "    targets: [demo]\n"
    )
    assert not hasattr(cfg, "orgs")
    assert _ios(resolve(cfg, "demo")).bundle_id == "com.example.demo"


def test_parse_config_dict_drops_orgs() -> None:
    cfg = parse_config_dict(
        {
            "targets": {"demo": {"platform": "ios", "subject": {"id": "com.example.demo"}}},
            "orgs": {"acme": {}},
        }
    )
    assert not hasattr(cfg, "orgs")
    assert "demo" in cfg.targets


def test_an_anchor_under_an_x_key_validates_like_the_written_out_block() -> None:
    # BE-0473: an `x-` key only hosts a YAML anchor, so a merged `<<` block resolves exactly as the
    # same block written in place, and the host key itself never reaches validation.
    anchored = load_config(
        "x-app: &app\n"
        "  launch: {env: {UITEST: '1'}}\n"
        "  idNamespaces: [stable, horse]\n"
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject:\n"
        "      <<: *app\n"
        "      id: com.x\n"
        "      idNamespaces: []\n"
    )
    written = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x, launch: {env: {UITEST: '1'}}, idNamespaces: []}\n"
    )
    assert anchored == written
    assert resolve(anchored, "s") == resolve(written, "s")


def test_only_the_x_prefix_is_ignored_at_the_top_level() -> None:
    # The prefix is the whole exception: a misspelled top-level key still fails as unknown.
    with pytest.raises(ValidationError, match="xapp"):
        load_config("xapp: {a: 1}\ntargets: {}\n")


def test_load_config_drops_top_level_ui() -> None:
    # `ui.default_theme` is a serve-only presentation setting (BE-0191); like `orgs`, the core run
    # path must ignore it rather than reject the document under extra="forbid".
    cfg = load_config(
        "targets:\n"
        "  demo:\n"
        "    platform: ios\n"
        "    subject: {id: com.example.demo}\n"
        "ui: {default_theme: daylight}\n"
    )
    assert not hasattr(cfg, "ui")
    assert _ios(resolve(cfg, "demo")).bundle_id == "com.example.demo"


def test_unknown_top_level_key_still_rejected() -> None:
    # Dropping `orgs` / `ui` must not loosen the typo guard: any other stray top-level key still fails.
    with pytest.raises(ValidationError):
        load_config("targetz:\n  demo: { bundleId: com.example.demo }\n")


def test_scalar_root_containing_orgs_raises_validationerror_not_attributeerror() -> None:
    # A YAML scalar root that happens to contain "orgs" must not trip the orgs-drop into an
    # AttributeError (which would escape callers that normalize config errors); it fails as a
    # ValidationError (a ValueError) like any other malformed document.
    with pytest.raises(ValidationError):
        load_config("orgs\n")


# --- BE-0177: per-target run-behavior defaults (interrupts / erase / network) ---


def test_run_behavior_defaults_when_unset() -> None:
    # An unset target resolves to the built-in defaults: no interrupts entry (the guard answers no
    # prompt, and TipKit stays off, since a tip is sometimes what a scenario asserts on), no reinstall
    # mode (the built-in clean), network on.
    eff = resolve(load_config("targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n"), "s")
    assert eff.run_defaults.interrupts == []
    assert eff.reinstall is None
    assert eff.run_defaults.network is True


def test_target_reinstall_and_network_resolve() -> None:
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    platform: ios\n"
        "    subject: {id: com.x, reinstall: erase}\n"
        "    evidence: {collectNetwork: false}\n"
    )
    eff = resolve(cfg, "s")
    assert eff.reinstall == "erase"
    assert eff.run_defaults.network is False


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("network: false", "evidence.collectNetwork"),
        ("visualCompare: exact", "evidence.visualCompare"),
        ("secrets: [A]", "evidence.secrets"),
        ("erase: true", "subject.reinstall: erase"),
    ],
)
def test_a_moved_run_key_names_its_new_location(old: str, new: str) -> None:
    # BE-0473 reads none of the old keys, so the error is the whole migration path an author gets.
    target = f"targets:\n  s:\n    platform: ios\n    subject: {{id: com.x}}\n    run: {{{old}}}\n"
    with pytest.raises(ValidationError, match=rf"run\.\w+ moved to {re.escape(new)}"):
        load_config(target)
    with pytest.raises(ValidationError, match=rf"run\.\w+ moved to {re.escape(new)}"):
        load_config(f"defaults:\n  run: {{{old}}}\ntargets: {{}}\n")
    with pytest.raises(ValidationError, match=rf"ios\.run\.\w+ moved to {re.escape(new)}"):
        load_config(f"defaults:\n  platforms:\n    ios:\n      run: {{{old}}}\ntargets: {{}}\n")


def test_collect_network_defaults_on_and_inherits_from_defaults() -> None:
    target = "  s:\n    platform: ios\n    subject: {id: com.x}\n"
    assert resolve(load_config("targets:\n" + target), "s").run_defaults.network is True
    cfg = load_config("defaults:\n  evidence: {collectNetwork: false}\ntargets:\n" + target)
    assert resolve(cfg, "s").run_defaults.network is False


def test_target_interrupts_carry_every_form_to_the_run_defaults() -> None:
    # BE-0473: one list shows every handler a target applies on its own.
    eff = resolve(
        load_config(
            "targets:\n"
            "  s:\n"
            "    platform: ios\n"
            "    subject: {id: com.x}\n"
            "    hooks:\n"
            "      interrupts:\n"
            "        - condition: {exists: {id: [aerr_wait]}}\n"
            "          steps: [{tap: {id: [aerr_close]}}]\n"
            "        - {systemAlert: notifications, choice: grant}\n"
            "        - {systemAlert: savePassword, choice: deny, frozenScreenTimeout: 20}\n"
            "        - tipKit: dismiss\n"
        ),
        "s",
    )
    screen, notif, save, tip = eff.run_defaults.interrupts
    assert isinstance(screen, Interrupt)
    assert isinstance(notif, SystemAlertInterrupt) and notif.choice == "grant"
    assert isinstance(save, SystemAlertInterrupt) and save.frozen_screen_timeout == 20
    assert isinstance(tip, TipKitInterrupt) and tip.tip_kit == "dismiss"


@pytest.mark.parametrize(
    ("run", "new_home"),
    [
        ("{systemAlertHandling: false}", "choice: leave"),
        ("{systemAlertHandling: {rules: [{prompt: notifications, choice: grant}]}}", "systemAlert"),
        ("{tipKitHandling: true}", "tipKit: dismiss"),
    ],
)
def test_a_target_run_handler_fails_naming_its_interrupts_entry(run: str, new_home: str) -> None:
    # The loader reads no old key (BE-0473), so the error is the migration path.
    with pytest.raises(ValidationError, match=r"hooks\.interrupts") as exc:
        load_config(
            f"targets:\n  s:\n    platform: ios\n    subject: {{id: com.x}}\n    run: {run}\n"
        )
    assert new_home in str(exc.value)


def test_a_defaults_platform_run_handler_fails_naming_its_interrupts_entry() -> None:
    with pytest.raises(ValidationError, match=r"tipKitHandling moved to hooks.interrupts"):
        load_config(
            "defaults:\n  platforms:\n    ios:\n      run: {tipKitHandling: true}\n"
            "targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n"
        )


def test_a_tip_kit_entry_fails_on_a_non_ios_target() -> None:
    # TipKit is Apple's framework, and a field never goes ignored (BE-0452).
    with pytest.raises(ValidationError, match="tipKit entry applies to iOS alone"):
        load_config(
            "targets:\n  a:\n    platform: android\n    subject: {id: com.x}\n"
            "    hooks: {interrupts: [{tipKit: dismiss}]}\n"
        )


def test_target_interrupts_answer_a_prompt_once() -> None:
    with pytest.raises(ValidationError, match="names systemAlert paste more than once"):
        load_config(
            "targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n"
            "    hooks:\n      interrupts:\n"
            "        - {systemAlert: paste, choice: grant}\n"
            "        - {systemAlert: paste, choice: deny}\n"
        )


def test_a_target_system_alert_entry_takes_no_target() -> None:
    # Only a screen entry carries `target` in a scenario; a target-level entry of any form has
    # nowhere for one to point, and a `target` beside `systemAlert` reads as a mixed entry.
    with pytest.raises(ValidationError, match=r"a screen entry \(target\) and `systemAlert`"):
        load_config(
            "targets:\n  s:\n    platform: ios\n    subject: {id: com.x}\n"
            "    hooks: {interrupts: [{systemAlert: paste, choice: grant, target: s}]}\n"
        )


@pytest.mark.parametrize("old", ["alertHandling", "dismissAlerts"])
def test_target_renamed_alert_keys_fail_as_unknown_run_fields(old: str) -> None:
    # BE-0401 deleted both aliases rather than carrying them a third time; under `run` (BE-0452)
    # each fails as an unknown key.
    with pytest.raises(ValidationError, match=old):
        load_config(
            f"targets:\n  s:\n    platform: ios\n    subject: {{id: com.x}}\n    run: {{{old}: false}}\n"
        )


def test_destination_blocks_resolve_to_the_target_conditions_and_state() -> None:
    # BE-0469 moved the fields under a destination block; what a run reads from them is unchanged.
    cfg = load_config(
        "targets:\n"
        "  i:\n"
        "    platform: ios\n"
        "    subject: {id: com.x}\n"
        "    runsOn:\n"
        "      ios: {model: iPhone 16, os: '>=18', locale: ja_JP, seedPhotos: [a.png]}\n"
        "  a:\n"
        "    platform: android\n"
        "    subject: {id: com.x}\n"
        "    runsOn: {android: {avd: [P1, P2], apiLevel: 34}}\n"
        "  w:\n"
        "    platform: web\n"
        "    subject: {url: 'http://x/'}\n"
        "    runsOn: {browser: {kind: webkit, version: '>=17', emulate: iPhone 13}}\n"
    )
    ios = resolve(cfg, "i")
    assert ios.conditions == {"model": "iPhone 16", "os": ">=18"}
    assert (ios.locale, ios.seed_photos) == ("ja_JP", ["a.png"])
    assert resolve(cfg, "a").conditions == {"avd": ["P1", "P2"], "apiLevel": "34"}
    web = resolve(cfg, "w")
    assert web.conditions == {"browser.version": ">=17"}  # one engine is the default, not a list
    assert _web(web).device_mode == "iPhone 13"


def test_a_tip_kit_entry_shares_through_defaults_hooks_on_an_all_ios_config() -> None:
    # `defaults.platforms` has no `hooks`, but `defaults.hooks` merges into every target, so an
    # all-iOS file states the entry once.
    cfg = load_config(
        "defaults: {hooks: {interrupts: [{tipKit: dismiss}]}}\n"
        "targets:\n  a: {platform: ios, subject: {id: com.a}}\n  b: {platform: ios, subject: {id: com.b}}\n"
    )
    for name in ("a", "b"):
        assert resolve(cfg, name).run_defaults.interrupts == [TipKitInterrupt(tipKit="dismiss")]
