"""Tests for config resolution (team defaults x per-target)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.config import load_config, resolve

CONFIG_YAML = """
defaults:
  backend: [idb]
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
    assert eff.bundle_id == "com.bajutsu.sample"
    assert eff.backend == ["idb"]  # from defaults
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


def test_backend_single_string_normalized() -> None:
    cfg = load_config("defaults: { backend: idb }\ntargets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").backend == ["idb"]


def test_app_overrides_defaults() -> None:
    cfg = load_config(
        "defaults: { backend: [fake], device: 'iPhone 15' }\n"
        "targets: { x: { bundleId: com.x, backend: idb, locale: en_US } }"
    )
    eff = resolve(cfg, "x")
    assert eff.backend == ["idb"]  # app override
    assert eff.device == "iPhone 15"  # falls through to defaults
    assert eff.locale == "en_US"


def test_unknown_app_raises() -> None:
    with pytest.raises(KeyError, match="unknown target"):
        resolve(load_config(CONFIG_YAML), "nope")


def test_minimal_defaults() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    eff = resolve(cfg, "x")
    assert eff.backend == ["idb"]
    assert eff.device == "iPhone 15"
    assert eff.capture == ["screenshot.after", "elements", "actionLog"]
    assert eff.app_path is None  # absent unless configured
    assert eff.scenarios is None  # absent unless configured


def test_app_path_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, appPath: build/X.app } }")
    assert resolve(cfg, "x").app_path == "build/X.app"


def test_scenarios_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, scenarios: scn/dir } }")
    assert resolve(cfg, "x").scenarios == "scn/dir"


def test_web_app_baseurl_no_bundleid() -> None:
    # A web app identifies its target by baseUrl and needs no bundleId; bundle_id defaults to "".
    cfg = load_config(
        "defaults: { backend: [web] }\n"
        "targets: { web: { baseUrl: 'http://127.0.0.1:8787/index.html', scenarios: demos/web/scenarios } }"
    )
    eff = resolve(cfg, "web")
    assert eff.base_url == "http://127.0.0.1:8787/index.html"
    assert eff.bundle_id == ""
    assert eff.backend == ["web"]
    assert eff.scenarios == "demos/web/scenarios"
    assert eff.headless is True  # the web backend runs headless unless opted out


def test_web_app_headless_override() -> None:
    # A web app can opt into a headed (visible) browser via `headless: false`; the
    # `bajutsu run --headed` flag and the Web UI's "show browser" toggle do the same per run.
    cfg = load_config("targets: { web: { baseUrl: 'http://127.0.0.1:8787/', headless: false } }")
    assert resolve(cfg, "web").headless is False


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


def test_ios_app_baseurl_defaults_to_none() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").base_url is None


def test_baselines_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, baselines: baselines/x } }")
    assert resolve(cfg, "x").baselines == "baselines/x"


def test_baselines_defaults_to_none() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").baselines is None


def test_baselines_resolution_order() -> None:
    """_resolve_baselines_dir respects: --baselines flag > config > scenario-local default."""
    from pathlib import Path

    from bajutsu.cli.commands.run import _resolve_baselines_dir

    eff_with = resolve(load_config("targets: { x: { bundleId: com.x, baselines: cfg/bl } }"), "x")
    eff_without = resolve(load_config("targets: { x: { bundleId: com.x } }"), "x")
    scenario_file = Path("/scenarios/app/smoke.yaml")

    # flag wins over everything
    assert _resolve_baselines_dir("flag/bl", eff_with, scenario_file) == Path("flag/bl")
    assert _resolve_baselines_dir("flag/bl", eff_without, scenario_file) == Path("flag/bl")

    # config used when no flag
    assert _resolve_baselines_dir("", eff_with, scenario_file) == Path("cfg/bl")

    # scenario-local default when neither flag nor config
    assert _resolve_baselines_dir("", eff_without, scenario_file) == Path(
        "/scenarios/app/baselines"
    )


def test_schemas_parsed() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x, schemas: schemas/x } }")
    assert resolve(cfg, "x").schemas == "schemas/x"


def test_schemas_defaults_to_none() -> None:
    cfg = load_config("targets: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").schemas is None


def test_schemas_resolution_order() -> None:
    """_resolve_schemas_dir respects: --schemas flag > config > scenario-local default."""
    from pathlib import Path

    from bajutsu.cli.commands.run import _resolve_schemas_dir

    eff_with = resolve(load_config("targets: { x: { bundleId: com.x, schemas: cfg/sc } }"), "x")
    eff_without = resolve(load_config("targets: { x: { bundleId: com.x } }"), "x")
    scenario_file = Path("/scenarios/app/smoke.yaml")

    assert _resolve_schemas_dir("flag/sc", eff_with, scenario_file) == Path("flag/sc")  # flag wins
    assert _resolve_schemas_dir("", eff_with, scenario_file) == Path("cfg/sc")  # then config
    assert _resolve_schemas_dir("", eff_without, scenario_file) == Path("/scenarios/app/schemas")
