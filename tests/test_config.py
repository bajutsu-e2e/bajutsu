"""Tests for config resolution (team defaults x per-target)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bajutsu.config import (
    AndroidConfig,
    Effective,
    IosConfig,
    WebConfig,
    load_config,
    parse_config_dict,
    resolve,
    xcuitest_targets_real_device,
)


def _ios(eff: Effective) -> IosConfig:
    assert isinstance(eff.platform_config, IosConfig)
    return eff.platform_config


def _web(eff: Effective) -> WebConfig:
    assert isinstance(eff.platform_config, WebConfig)
    return eff.platform_config


CONFIG_YAML = """
defaults:
  backend: [ios]
  device: "iPhone 15"
  locale: ja_JP
  capture: [screenshot.after, elements, actionLog]
  redact: { headers: [Authorization, Cookie], fields: [token, password] }
  reservedNamespaces: [auth, nav]

targets:
  sample:
    bundleId: com.bajutsu.sample
    deeplinkScheme: bajutsusample
    launchEnv: { SAMPLE_UITEST: "1" }
    idNamespaces: [home, list, counter, settings]
    mockServer: { cmd: "serve", port: 8080, stubs: ./mocks/sample }
    setup: ./scenarios/sample/_setup.yaml
    redact: { labels: ["カード番号"] }
"""


def test_resolve_sample() -> None:
    eff = resolve(load_config(CONFIG_YAML), "sample")
    assert _ios(eff).bundle_id == "com.bajutsu.sample"
    assert eff.backend == ["ios"]  # from defaults
    assert eff.device == "iPhone 15"
    assert eff.locale == "ja_JP"
    assert eff.launch_env == {"SAMPLE_UITEST": "1"}
    assert eff.id_namespaces == ["home", "list", "counter", "settings"]
    assert eff.reserved_namespaces == ["auth", "nav"]
    assert eff.mock_server is not None and eff.mock_server.port == 8080
    assert eff.setup == "./scenarios/sample/_setup.yaml"


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
        "  redact: { unmaskHeaders: [authorization] }\n"
        "targets:\n"
        "  s:\n"
        "    bundleId: com.x\n"
        "    redact: { unmaskHeaders: [cookie] }\n"
    )
    assert resolve(cfg, "s").redact.unmask_headers == ["authorization", "cookie"]


# BE-0166: `requires` (the capability tokens routing needs) resolves as a union of the team-wide
# default and the target's own list — the target adds requirements, never replaces them.


def test_requires_resolves_as_union_of_defaults_and_target() -> None:
    cfg = load_config(
        "defaults:\n"
        "  requires: [ios18]\n"
        "targets:\n"
        "  s:\n"
        "    bundleId: com.x\n"
        "    requires: [ipad]\n"
    )
    assert sorted(resolve(cfg, "s").requires) == ["ios18", "ipad"]


def test_requires_defaults_to_empty() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    assert resolve(cfg, "s").requires == []


# BE-0047: the `ai` block resolves like any other setting (defaults overridden per target) into
# an AiConfig the AI paths read; an absent block resolves to None (env-only, as before).


def test_ai_block_resolves_from_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  ai:\n"
        "    provider: api-key\n"
        "    model: claude-opus-4-8\n"
        "    baseUrl: https://gw.internal/v1\n"
        "    keyEnv: MY_KEY\n"
        "targets:\n  s:\n    bundleId: com.x\n"
    )
    ai = resolve(cfg, "s").ai
    assert ai is not None
    assert ai.provider == "api-key"
    assert ai.model == "claude-opus-4-8"
    assert ai.base_url == "https://gw.internal/v1"
    assert ai.key_env == "MY_KEY"


def test_ai_block_target_overrides_defaults() -> None:
    cfg = load_config(
        "defaults:\n  ai: { provider: api-key, model: claude-opus-4-8, keyEnv: TEAM_KEY }\n"
        "targets:\n  s:\n    bundleId: com.x\n    ai: { model: claude-sonnet-x, keyEnv: APP_KEY }\n"
    )
    ai = resolve(cfg, "s").ai
    assert ai is not None
    assert ai.provider == "api-key"  # falls through from defaults
    assert ai.model == "claude-sonnet-x"  # target override
    assert ai.key_env == "APP_KEY"  # target override


def test_ai_block_absent_resolves_to_none() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    assert resolve(cfg, "s").ai is None


def test_ai_usage_ledger_and_pricing_resolve() -> None:
    # BE-0196: the ledger path and per-model pricing override resolve as plain dicts on AiConfig.
    cfg = load_config(
        "defaults:\n"
        "  ai:\n"
        "    provider: api-key\n"
        "    usageLedger: runs/mine.jsonl\n"
        "    pricing:\n"
        "      api-key/sonnet: { input: 3.0, output: 15.0, cacheWrite: 3.75, cacheRead: 0.3 }\n"
        "targets:\n  s:\n    bundleId: com.x\n"
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
        "defaults:\n  ai: { provider: api-key, language: en }\n"
        "targets:\n"
        "  s:\n    bundleId: com.x\n    ai: { language: ja }\n"
        "  t:\n    bundleId: com.y\n"
    )
    assert resolve(cfg, "s").ai is not None and resolve(cfg, "s").ai.language == "ja"  # override
    assert (
        resolve(cfg, "t").ai is not None and resolve(cfg, "t").ai.language == "en"
    )  # from defaults


def test_ai_block_keys_in_config_are_rejected() -> None:
    # A literal key in config is a foot-gun the schema forbids: only keyEnv (a NAME) is allowed.
    with pytest.raises(ValidationError):
        load_config(
            "defaults:\n  ai: { apiKey: sk-ant-secret }\ntargets:\n  s:\n    bundleId: com.x\n"
        )


def test_backend_single_string_normalized() -> None:
    cfg = load_config("defaults: { backend: xcuitest }\ntargets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").backend == ["xcuitest"]


def test_app_overrides_defaults() -> None:
    cfg = load_config(
        "defaults: { backend: [fake], device: 'iPhone 15' }\n"
        "targets: { x: { bundleId: com.x, backend: xcuitest, locale: en_US } }"
    )
    eff = resolve(cfg, "x")
    assert eff.backend == ["xcuitest"]  # app override
    assert eff.device == "iPhone 15"  # falls through to defaults
    assert eff.locale == "en_US"


def test_unknown_app_raises() -> None:
    with pytest.raises(KeyError, match="unknown target"):
        resolve(load_config(CONFIG_YAML), "nope")


def test_minimal_defaults() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    eff = resolve(cfg, "x")
    assert eff.backend == ["ios"]  # the default (BE-0290)
    assert eff.device == "iPhone 15"
    assert eff.capture == ["screenshot.after", "elements", "actionLog"]
    assert _ios(eff).app_path is None  # absent unless configured
    assert eff.evidence_dirs.scenarios is None  # absent unless configured


def test_app_path_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, appPath: build/X.app } }")
    assert _ios(resolve(cfg, "x")).app_path == "build/X.app"


def test_scenarios_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, scenarios: scn/dir } }")
    assert resolve(cfg, "x").evidence_dirs.scenarios == "scn/dir"


def test_ready_when_selector_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, readyWhen: { id: onboarding.start } } }")
    assert resolve(cfg, "x").ready_when == {"id": "onboarding.start"}


def test_ready_when_defaults_to_none() -> None:
    assert resolve(load_config("targets: { x: { bundleId: com.x } }"), "x").ready_when is None


def test_ready_when_candidate_list_parsed() -> None:
    # A readyWhen with a canonical-first candidate list (BE-0221) is accepted, like a scenario step.
    cfg = load_config(
        "targets: { x: { bundleId: com.x, readyWhen: { id: [stable.row.1, stable_row_1] } } }"
    )
    assert resolve(cfg, "x").ready_when == {"id": ["stable.row.1", "stable_row_1"]}


def test_ready_when_misordered_candidate_list_rejected() -> None:
    # readyWhen is validated the same way a scenario selector is: an underscore-first list (the
    # canonical dotted form not leading) fails loudly at load, not silently (BE-0221).
    with pytest.raises(ValidationError, match="canonical"):
        load_config(
            "targets: { x: { bundleId: com.x, readyWhen: { id: [stable_row_1, stable.row.1] } } }"
        )


def test_ready_when_idmatches_misordered_candidate_list_rejected() -> None:
    # idMatches in readyWhen is validated on its own branch, the same way as id (BE-0221).
    with pytest.raises(ValidationError, match="canonical"):
        load_config("targets: { x: { bundleId: com.x, readyWhen: { idMatches: [a_b, a.b] } } }")


def test_web_app_baseurl_no_bundleid() -> None:
    # A web app identifies its target by baseUrl and needs no bundleId — it carries a WebConfig,
    # which has no bundle_id field at all (the point of the per-platform split, BE-0126).
    cfg = load_config(
        "defaults: { backend: [web] }\n"
        "targets: { web: { baseUrl: 'http://127.0.0.1:8787/index.html', scenarios: demos/web/scenarios } }"
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
    cfg = load_config("targets: { web: { baseUrl: 'http://127.0.0.1:8787/', headless: false } }")
    assert _web(resolve(cfg, "web")).headless is False


def test_web_app_browser_defaults_to_chromium() -> None:
    # The browser engine defaults to chromium, preserving today's single-engine behaviour (BE-0076).
    cfg = load_config("targets: { web: { baseUrl: 'http://127.0.0.1:8787/' } }")
    assert _web(resolve(cfg, "web")).browser == "chromium"


def test_web_app_browser_config_resolves() -> None:
    # A target can pin its engine via `browser`; it resolves straight onto the web sub-config (a
    # per-target knob, like headless).
    cfg = load_config("targets: { web: { baseUrl: 'http://127.0.0.1:8787/', browser: firefox } }")
    assert _web(resolve(cfg, "web")).browser == "firefox"


def test_web_app_unknown_browser_rejected_at_load() -> None:
    # A typo'd engine fails loudly at config load (the field_validator), not as a mid-run
    # AttributeError when the driver does getattr(pw, engine).
    with pytest.raises(ValidationError, match="invalid browser"):
        load_config("targets: { web: { baseUrl: 'http://x/', browser: safari } }")


def test_web_app_device_mode_defaults_to_desktop() -> None:
    # No deviceMode = desktop: the web backend keeps today's plain desktop context, so an existing
    # web config is unchanged (BE-0228).
    cfg = load_config("targets: { web: { baseUrl: 'http://127.0.0.1:8787/' } }")
    assert _web(resolve(cfg, "web")).device_mode == "desktop"


def test_web_app_device_mode_config_resolves() -> None:
    # A target drives a mobile face by naming a Playwright device preset; it resolves straight onto
    # the web sub-config (a per-target knob, like browser). The preset name itself is resolved
    # lazily in the driver, so config load never imports Playwright.
    cfg = load_config(
        "targets: { web: { baseUrl: 'http://127.0.0.1:8787/', deviceMode: 'iPhone 13' } }"
    )
    assert _web(resolve(cfg, "web")).device_mode == "iPhone 13"


def test_device_provider_defaults_to_none() -> None:
    # No deviceProvider = the built-in local device path; Effective carries None so acquire_device
    # falls back to the `local` provider (BE-0236), leaving every existing target unchanged.
    cfg = load_config("targets: { app: { bundleId: com.example.app } }")
    assert resolve(cfg, "app").device_provider is None


def test_device_provider_config_resolves() -> None:
    # A target selects a device provider by `kind`; it resolves straight onto Effective (a per-target
    # knob, like mailbox). The kind is validated against the registry at runtime, not config load, so
    # the deterministic core never imports a cloud SDK (BE-0236 / BE-0112).
    cfg = load_config(
        "targets: { app: { bundleId: com.example.app, deviceProvider: {kind: firebase-streaming} } }"
    )
    provider = resolve(cfg, "app").device_provider
    assert provider is not None
    assert provider.kind == "firebase-streaming"


def test_device_provider_kind_defaults_to_local() -> None:
    # A deviceProvider block with no `kind` means the explicit local provider — the same default the
    # schema carries, so writing the block without a kind is a no-op over omitting it entirely.
    cfg = load_config("targets: { app: { bundleId: com.example.app, deviceProvider: {} } }")
    provider = resolve(cfg, "app").device_provider
    assert provider is not None
    assert provider.kind == "local"


def test_appium_provider_endpoint_round_trips_through_config() -> None:
    # The `endpoint` field on `deviceProvider` must survive the YAML → `load_config` → `resolve`
    # path; a field-name or alias mismatch in `_Model` (which sets `extra="forbid"`) would only
    # surface here, not in the direct-construction tests in `test_device_provider.py`.
    cfg = load_config(
        "targets: { app: { bundleId: com.example.app,"
        " deviceProvider: { kind: appium, endpoint: 'http://grid.local:4723' } } }"
    )
    provider = resolve(cfg, "app").device_provider
    assert provider is not None
    assert provider.kind == "appium"
    assert provider.endpoint == "http://grid.local:4723"


def test_web_app_launch_server_parsed() -> None:
    # `launchServer` declares how to bring up baseUrl's host for a run; readyUrl defaults to None
    # (run falls back to baseUrl), readyTimeout to 30s.
    eff = resolve(
        load_config(
            "targets: { web: { baseUrl: 'http://127.0.0.1:8799/', "
            "launchServer: { cmd: 'uv run bajutsu serve --port 8799', readyTimeout: 60 } } }"
        ),
        "web",
    )
    assert eff.launch_server is not None
    assert eff.launch_server.cmd == "uv run bajutsu serve --port 8799"
    assert eff.launch_server.ready_timeout == 60.0
    assert eff.launch_server.ready_url is None  # run falls back to baseUrl
    assert (
        resolve(load_config("targets: { web: { baseUrl: 'http://x/' } }"), "web").launch_server
        is None
    )


def test_app_without_bundleid_or_baseurl_rejected() -> None:
    # Dropping bundleId's required-ness must not silently accept a target-less app.
    with pytest.raises(ValidationError, match="needs bundleId"):
        load_config("targets: { x: { scenarios: scn/dir } }")


def test_legacy_apps_key_is_rejected() -> None:
    # Hard cutover (BE-0057): the renamed grammar keeps no `apps:` alias. `extra="forbid"` makes a
    # stale `apps:` config fail loudly at load time, rather than silently resolving to no targets.
    with pytest.raises(ValidationError):
        load_config("apps: { x: { bundleId: com.x } }")


def test_ios_app_carries_no_base_url() -> None:
    # An iOS target carries an IosConfig, which has no base_url field (BE-0126).
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    eff = resolve(cfg, "x")
    assert isinstance(eff.platform_config, IosConfig)
    assert not hasattr(eff.platform_config, "base_url")


def test_baselines_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, baselines: baselines/x } }")
    assert resolve(cfg, "x").evidence_dirs.baselines == "baselines/x"


def test_baselines_defaults_to_none() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").evidence_dirs.baselines is None


def test_baselines_resolution_order() -> None:
    """_resolve_dir for baselines respects: --baselines flag > config > scenario-local default."""
    from bajutsu.cli.commands.run import _resolve_dir

    eff_with = resolve(load_config("targets: { x: { bundleId: com.x, baselines: cfg/bl } }"), "x")
    eff_without = resolve(load_config("targets: { x: { bundleId: com.x } }"), "x")
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
    cfg = load_config("targets: { x: { bundleId: com.x, schemas: schemas/x } }")
    assert resolve(cfg, "x").evidence_dirs.schemas == "schemas/x"


def test_schemas_defaults_to_none() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").evidence_dirs.schemas is None


def test_schemas_resolution_order() -> None:
    """_resolve_dir for schemas respects: --schemas flag > config > scenario-local default."""
    from bajutsu.cli.commands.run import _resolve_dir

    eff_with = resolve(load_config("targets: { x: { bundleId: com.x, schemas: cfg/sc } }"), "x")
    eff_without = resolve(load_config("targets: { x: { bundleId: com.x } }"), "x")
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
    cfg = load_config("targets: { x: { bundleId: com.x, goldens: goldens/x } }")
    assert resolve(cfg, "x").evidence_dirs.goldens == "goldens/x"


def test_goldens_defaults_to_none() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").evidence_dirs.goldens is None


def test_goldens_resolution_order() -> None:
    """_resolve_dir for goldens respects: --goldens flag > config > scenario-local default."""
    from bajutsu.cli.commands.run import _resolve_dir

    eff_with = resolve(load_config("targets: { x: { bundleId: com.x, goldens: cfg/gl } }"), "x")
    eff_without = resolve(load_config("targets: { x: { bundleId: com.x } }"), "x")
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
            "targets:\n  x:\n    bundleId: com.x\n    scenarios: e2e/scn\n    appPath: build/A.app\n    goldens: golden/data\n"
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
            "targets:\n  x:\n    bundleId: com.x\n    xcuitest:\n"
            "      testRunner: build/Runner.xctestrun\n"
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
            "targets:\n  x:\n    bundleId: com.x\n    xcuitest:\n"
            "      testRunner: build/Runner.xctestrun\n      deviceType: device\n"
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
            load_config(f"targets:\n  x:\n    bundleId: com.x\n    scenarios: {bad}\n"), "x"
        )
        with pytest.raises(ValueError, match="escapes the checkout"):
            eff.rebased(Path("/co"))


def test_rebased_without_confinement_keeps_paths_outside_the_root() -> None:
    """`confine=False` (an operator-trusted local config, BE-0242) joins a `..`/absolute path instead of refusing it."""
    eff = resolve(
        load_config(
            "targets:\n  x:\n    bundleId: com.x\n    scenarios: ../sibling/scn\n"
            "    appPath: /opt/App.app\n"
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
    cfg = load_config(
        "targets:\n  s:\n    platform: ios\n    bundleId: com.x\n    backend: [ios]\n"
    )
    assert resolve(cfg, "s").platform == "ios"


def test_platform_defaults_apply_when_target_omits_it() -> None:
    # A team-wide `defaults.platform` flows to a target that doesn't override it.
    cfg = load_config(
        "defaults:\n  platform: web\ntargets:\n  s:\n    baseUrl: https://app.test\n"
        "    backend: [playwright]\n"
    )
    assert resolve(cfg, "s").platform == "web"


def test_platform_derived_from_backend_when_unset() -> None:
    # With no explicit platform anywhere, it's derived from the backend (today's implicit behavior),
    # so existing configs are unchanged: playwright -> web, xcuitest -> ios.
    web = load_config("targets:\n  s:\n    baseUrl: https://app.test\n    backend: [playwright]\n")
    assert resolve(web, "s").platform == "web"
    ios = load_config("targets:\n  s:\n    bundleId: com.x\n    backend: [xcuitest]\n")
    assert resolve(ios, "s").platform == "ios"


def test_package_resolves_into_effective() -> None:
    # The Android identifier (peer of bundleId / baseUrl) resolves onto the Android sub-config.
    cfg = load_config(
        "targets:\n  s:\n    platform: android\n    package: com.x.app\n    backend: [adb]\n"
    )
    eff = resolve(cfg, "s")
    assert eff.platform == "android"
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.package == "com.x.app"


# --- Per-platform effective sub-configs (BE-0126) --- #


def test_ios_target_yields_ios_sub_config() -> None:
    # An iOS target's platform-specific knobs land on an IosConfig, not on the common core.
    cfg = load_config(
        "targets:\n  s:\n    platform: ios\n    bundleId: com.x\n"
        "    deeplinkScheme: myscheme\n    appPath: build/Demo.app\n"
        "    build: make app\n    backend: [ios]\n"
    )
    eff = resolve(cfg, "s")
    assert eff.platform == "ios"
    assert isinstance(eff.platform_config, IosConfig)
    ios = eff.platform_config
    assert ios.bundle_id == "com.x"
    assert ios.deeplink_scheme == "myscheme"
    assert ios.app_path == "build/Demo.app"
    assert ios.build == "make app"


def test_web_target_yields_web_sub_config() -> None:
    # A web target's Playwright knobs land on a WebConfig.
    cfg = load_config(
        "targets:\n  s:\n    platform: web\n    baseUrl: https://app.test\n"
        "    browser: firefox\n    headless: false\n    backend: [playwright]\n"
    )
    eff = resolve(cfg, "s")
    assert eff.platform == "web"
    assert isinstance(eff.platform_config, WebConfig)
    web = eff.platform_config
    assert web.base_url == "https://app.test"
    assert web.browser == "firefox"
    assert web.headless is False


def test_android_target_yields_android_sub_config() -> None:
    cfg = load_config(
        "targets:\n  s:\n    platform: android\n    package: com.x.app\n    backend: [adb]\n"
    )
    eff = resolve(cfg, "s")
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.package == "com.x.app"


def test_android_grant_permissions_flow_into_the_sub_config() -> None:
    # BE-0210: runtime permissions to pre-grant live in config (app-specific), reaching AndroidConfig.
    cfg = load_config(
        "targets:\n  s:\n    platform: android\n    package: com.x.app\n    backend: [adb]\n"
        "    grantPermissions: [android.permission.POST_NOTIFICATIONS, android.permission.CAMERA]\n"
    )
    eff = resolve(cfg, "s")
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.grant_permissions == [
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.CAMERA",
    ]


def test_android_grant_permissions_default_empty() -> None:
    cfg = load_config("targets:\n  s:\n    platform: android\n    package: com.x.app\n")
    eff = resolve(cfg, "s")
    assert isinstance(eff.platform_config, AndroidConfig)
    assert eff.platform_config.grant_permissions == []


def test_web_target_carries_no_ios_fields() -> None:
    # The whole point of the split: a web target's config never exposes iOS-only knobs.
    cfg = load_config("targets:\n  s:\n    baseUrl: https://app.test\n    backend: [playwright]\n")
    eff = resolve(cfg, "s")
    assert not isinstance(eff.platform_config, IosConfig)
    assert not hasattr(eff.platform_config, "bundle_id")


def test_ios_target_carries_no_web_fields() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n    backend: [ios]\n")
    eff = resolve(cfg, "s")
    assert not isinstance(eff.platform_config, WebConfig)
    assert not hasattr(eff.platform_config, "base_url")


def test_rebased_rebases_app_path_inside_ios_config() -> None:
    # rebased() confines the iOS appPath to the checkout root, inside the sub-config.
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n    appPath: build/Demo.app\n")
    eff = resolve(cfg, "s").rebased(Path("/co"))
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.app_path == str(Path("/co") / "build/Demo.app")


def test_unknown_platform_is_rejected_at_load() -> None:
    with pytest.raises(ValidationError, match="platform"):
        load_config("targets:\n  s:\n    platform: martian\n    bundleId: com.x\n")


def test_ios_platform_requires_bundle_id() -> None:
    # An iOS target carrying the wrong identifier (baseUrl, no bundleId) is rejected with a
    # platform-aware message — distinct from the "no identifier at all" check.
    with pytest.raises(ValidationError, match="bundleId"):
        load_config("targets:\n  s:\n    platform: ios\n    baseUrl: https://app.test\n")


def test_web_platform_requires_base_url() -> None:
    with pytest.raises(ValidationError, match="baseUrl"):
        load_config("targets:\n  s:\n    platform: web\n    bundleId: com.x\n")


def test_android_platform_requires_package() -> None:
    with pytest.raises(ValidationError, match="package"):
        load_config("targets:\n  s:\n    platform: android\n    bundleId: com.x\n")


# --- BE-0019: xcuitest config fields ---


def test_xcuitest_config_resolves() -> None:
    cfg = load_config(
        "targets:\n"
        "  s:\n"
        "    bundleId: com.x\n"
        "    xcuitest:\n"
        "      testRunner: build/Runner.xctestrun\n"
        "      build: xcodebuild build-for-testing\n"
    )
    eff = resolve(cfg, "s")
    xcuitest = _ios(eff).xcuitest
    assert xcuitest is not None
    assert xcuitest.test_runner == "build/Runner.xctestrun"
    assert xcuitest.build == "xcodebuild build-for-testing"


def test_xcuitest_config_defaults_to_none() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    assert _ios(resolve(cfg, "s")).xcuitest is None


def test_xcuitest_device_type_defaults_to_simulator() -> None:
    # An omitted deviceType keeps the pre-BE-0238 behaviour: drive the Simulator (BE-0019).
    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n"
        "      testRunner: build/Runner.xctestrun\n"
    )
    xcuitest = _ios(resolve(cfg, "s")).xcuitest
    assert xcuitest is not None
    assert xcuitest.device_type == "simulator"


def test_xcuitest_device_type_resolves_device() -> None:
    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n"
        "      testRunner: build/Runner.xctestrun\n      deviceType: device\n"
    )
    xcuitest = _ios(resolve(cfg, "s")).xcuitest
    assert xcuitest is not None
    assert xcuitest.device_type == "device"


def test_xcuitest_device_type_invalid_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n"
            "      testRunner: build/Runner.xctestrun\n      deviceType: physical\n"
        )


# --- BE-0238: the real-device accessor backing the capability narrowing (Unit 3) --- #


def test_xcuitest_targets_real_device_true_for_device() -> None:
    # `xcuitest.deviceType: device` is the one shape that drives a physical device — the accessor the
    # Unit 3 capability narrowing consults to drop the simctl-backed DeviceControl / permission tokens.
    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
    )
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is True


def test_xcuitest_targets_real_device_false_for_simulator() -> None:
    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: simulator\n"
    )
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is False


def test_xcuitest_targets_real_device_false_when_xcuitest_omitted() -> None:
    # An iOS target with no xcuitest block keeps the Simulator default (BE-0019), so it is not a real
    # device — the accessor must not raise on the missing block.
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is False


def test_xcuitest_targets_real_device_false_for_non_ios_target() -> None:
    # deviceType is iOS-only; an Android target can never target a real iOS device through XCUITest.
    cfg = load_config("targets:\n  s:\n    package: com.example.app\n")
    assert xcuitest_targets_real_device(resolve(cfg, "s")) is False


# --- BE-0024: configurable doctor thresholds ---


def test_doctor_thresholds_resolve_from_defaults() -> None:
    cfg = load_config(
        "defaults:\n"
        "  doctor:\n"
        "    idCoverageOk: 0.85\n"
        "    idCoverageFail: 0.6\n"
        "targets:\n  s:\n    bundleId: com.x\n"
    )
    eff = resolve(cfg, "s")
    assert eff.doctor_thresholds.ok_coverage == 0.85
    assert eff.doctor_thresholds.fail_coverage == 0.6


def test_doctor_thresholds_default_to_hardcoded_values() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    eff = resolve(cfg, "s")
    assert eff.doctor_thresholds.ok_coverage == 0.9
    assert eff.doctor_thresholds.fail_coverage == 0.7


def test_doctor_ok_below_fail_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "defaults:\n"
            "  doctor:\n"
            "    idCoverageOk: 0.5\n"
            "    idCoverageFail: 0.8\n"
            "targets:\n  s:\n    bundleId: com.x\n"
        )


def test_doctor_threshold_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(
            "defaults:\n  doctor:\n    idCoverageOk: 1.5\ntargets:\n  s:\n    bundleId: com.x\n"
        )


# --- BE-0099: webhook notification config ---


def test_notify_config_resolves() -> None:
    cfg = load_config(
        "notify:\n"
        "  - format: slack\n"
        "    url: '${secrets.SLACK_WEBHOOK_URL}'\n"
        "    on: [failure, change]\n"
        "targets:\n  s:\n    bundleId: com.x\n"
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
        "  - format: slack\n"
        "    url: '${secrets.URL}'\n"
        "targets:\n  s:\n    bundleId: com.x\n"
    )
    assert resolve(cfg, "s").notify[0].on == ["failure"]


def test_notify_with_targets_filter() -> None:
    cfg = load_config(
        "notify:\n"
        "  - format: slack\n"
        "    url: '${secrets.URL}'\n"
        "    targets: [checkout, login]\n"
        "targets:\n  s:\n    bundleId: com.x\n"
    )
    assert resolve(cfg, "s").notify[0].targets == ["checkout", "login"]


def test_notify_unknown_format_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown notify format"):
        load_config(
            "notify:\n"
            "  - format: teams\n"
            "    url: '${secrets.URL}'\n"
            "targets:\n  s:\n    bundleId: com.x\n"
        )


def test_notify_unknown_event_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown notify event"):
        load_config(
            "notify:\n"
            "  - format: slack\n"
            "    url: '${secrets.URL}'\n"
            "    on: [bogus]\n"
            "targets:\n  s:\n    bundleId: com.x\n"
        )


def test_notify_target_override() -> None:
    cfg = load_config(
        "notify:\n"
        "  - format: slack\n"
        "    url: '${secrets.A}'\n"
        "targets:\n"
        "  s:\n"
        "    bundleId: com.x\n"
        "    notify:\n"
        "      - format: slack\n"
        "        url: '${secrets.B}'\n"
        "        on: [always]\n"
    )
    eff = resolve(cfg, "s")
    assert len(eff.notify) == 1
    assert eff.notify[0].url == "${secrets.B}"
    assert eff.notify[0].on == ["always"]


def test_notify_absent_resolves_to_empty() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    assert resolve(cfg, "s").notify == []


def test_notify_on_accepts_single_string() -> None:
    cfg = load_config(
        "notify:\n"
        "  - format: slack\n"
        "    url: '${secrets.URL}'\n"
        "    on: always\n"
        "targets:\n  s:\n    bundleId: com.x\n"
    )
    assert resolve(cfg, "s").notify[0].on == ["always"]


# --- BE-0165: visual compare engine config ---


def test_visual_compare_defaults_to_exact() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n")
    assert resolve(cfg, "s").visual_compare == "exact"


def test_visual_compare_from_defaults() -> None:
    cfg = load_config(
        "defaults:\n  visualCompare: pixelmatch\ntargets:\n  s:\n    bundleId: com.x\n"
    )
    assert resolve(cfg, "s").visual_compare == "pixelmatch"


def test_visual_compare_target_overrides_defaults() -> None:
    cfg = load_config(
        "defaults:\n  visualCompare: pixelmatch\n"
        "targets:\n  s:\n    bundleId: com.x\n    visualCompare: exact\n"
    )
    assert resolve(cfg, "s").visual_compare == "exact"


def test_visual_compare_target_only() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n    visualCompare: pixelmatch\n")
    assert resolve(cfg, "s").visual_compare == "pixelmatch"


def test_visual_compare_invalid_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config("targets:\n  s:\n    bundleId: com.x\n    visualCompare: ssim\n")


def test_load_config_drops_top_level_orgs() -> None:
    # The org model is a serve concern the deterministic core does not understand (BE-0129). A run
    # in the hosted topology reads an org-bearing config, so the core loader must drop `orgs:` and
    # keep resolving targets rather than reject the whole document.
    cfg = load_config(
        "targets:\n  demo: { bundleId: com.example.demo }\n"
        "orgs:\n  acme:\n    members: [alice]\n    targets: [demo]\n"
    )
    assert not hasattr(cfg, "orgs")
    assert _ios(resolve(cfg, "demo")).bundle_id == "com.example.demo"


def test_parse_config_dict_drops_orgs() -> None:
    cfg = parse_config_dict(
        {"targets": {"demo": {"bundleId": "com.example.demo"}}, "orgs": {"acme": {}}}
    )
    assert not hasattr(cfg, "orgs")
    assert "demo" in cfg.targets


def test_load_config_drops_top_level_ui() -> None:
    # `ui.default_theme` is a serve-only presentation setting (BE-0191); like `orgs`, the core run
    # path must ignore it rather than reject the document under extra="forbid".
    cfg = load_config(
        "targets:\n  demo: { bundleId: com.example.demo }\nui:\n  default_theme: daylight\n"
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


# --- BE-0177: per-target run-behavior defaults (alertHandling / erase / network) ---


def test_run_behavior_defaults_when_unset() -> None:
    # An unset target resolves to the built-in defaults: guard on (alert_handling None → on with the
    # default instruction), erase off, network on.
    eff = resolve(load_config("targets:\n  s:\n    bundleId: com.x\n"), "s")
    assert eff.run_defaults.alert_handling is None
    assert eff.run_defaults.erase is False
    assert eff.run_defaults.network is True


def test_target_erase_and_network_resolve() -> None:
    cfg = load_config("targets:\n  s:\n    bundleId: com.x\n    erase: true\n    network: false\n")
    eff = resolve(cfg, "s")
    assert eff.run_defaults.erase is True
    assert eff.run_defaults.network is False


def test_target_alert_handling_bool_shorthand() -> None:
    # The bare-boolean on-disk form is coerced to `{ enabled: <bool> }`, as on a scenario.
    eff = resolve(
        load_config("targets:\n  s:\n    bundleId: com.x\n    alertHandling: false\n"), "s"
    )
    assert eff.run_defaults.alert_handling is not None
    assert eff.run_defaults.alert_handling.enabled is False
    assert eff.run_defaults.alert_handling.instruction is None


def test_target_alert_handling_object_form() -> None:
    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    alertHandling: { instruction: Allow }\n"
    )
    eff = resolve(cfg, "s")
    assert eff.run_defaults.alert_handling is not None
    assert eff.run_defaults.alert_handling.enabled is True  # object form keeps the guard on
    assert eff.run_defaults.alert_handling.instruction == "Allow"


def test_target_dismiss_alerts_alias_still_resolves() -> None:
    # BE-0317: the deprecated `dismissAlerts` config key resolves to the same default as `alertHandling`.
    eff = resolve(
        load_config("targets:\n  s:\n    bundleId: com.x\n    dismissAlerts: false\n"), "s"
    )
    assert eff.run_defaults.alert_handling is not None
    assert eff.run_defaults.alert_handling.enabled is False
