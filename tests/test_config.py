"""Tests for config resolution (team defaults x per-app)."""

from __future__ import annotations

import pytest

from bajutsu.config import load_config, resolve

CONFIG_YAML = """
defaults:
  backend: [rocketsim, idb]
  device: "iPhone 15"
  locale: ja_JP
  capture: [screenshot.after, elements, actionLog]
  redact: { headers: [Authorization, Cookie], fields: [token, password] }
  reservedNamespaces: [auth, nav]

apps:
  searchsample:
    bundleId: com.example.SearchSample
    deeplinkScheme: searchsample
    launchEnv: { SEARCH_SHOW_SETTINGS: "1" }
    idNamespaces: [settings, search, result]
    mockServer: { cmd: "serve", port: 8080, stubs: ./mocks/searchsample }
    setup: ./scenarios/searchsample/_setup.yaml
    redact: { labels: ["カード番号"] }
"""


def test_resolve_searchsample() -> None:
    eff = resolve(load_config(CONFIG_YAML), "searchsample")
    assert eff.bundle_id == "com.example.SearchSample"
    assert eff.backend == ["rocketsim", "idb"]  # from defaults
    assert eff.device == "iPhone 15"
    assert eff.locale == "ja_JP"
    assert eff.launch_env == {"SEARCH_SHOW_SETTINGS": "1"}
    assert eff.id_namespaces == ["settings", "search", "result"]
    assert eff.reserved_namespaces == ["auth", "nav"]
    assert eff.mock_server is not None and eff.mock_server.port == 8080
    assert eff.setup == "./scenarios/searchsample/_setup.yaml"


def test_redact_is_merged() -> None:
    eff = resolve(load_config(CONFIG_YAML), "searchsample")
    assert eff.redact.labels == ["カード番号"]            # from the app
    assert eff.redact.headers == ["Authorization", "Cookie"]  # from defaults
    assert eff.redact.fields == ["token", "password"]


def test_backend_single_string_normalized() -> None:
    cfg = load_config("defaults: { backend: rocketsim }\napps: { x: { bundleId: com.x } }")
    assert resolve(cfg, "x").backend == ["rocketsim"]


def test_app_overrides_defaults() -> None:
    cfg = load_config(
        "defaults: { backend: [rocketsim], device: 'iPhone 15' }\n"
        "apps: { x: { bundleId: com.x, backend: idb, locale: en_US } }"
    )
    eff = resolve(cfg, "x")
    assert eff.backend == ["idb"]   # app override
    assert eff.device == "iPhone 15"  # falls through to defaults
    assert eff.locale == "en_US"


def test_unknown_app_raises() -> None:
    with pytest.raises(KeyError):
        resolve(load_config(CONFIG_YAML), "nope")


def test_minimal_defaults() -> None:
    cfg = load_config("apps: { x: { bundleId: com.x } }")
    eff = resolve(cfg, "x")
    assert eff.backend == ["rocketsim"]
    assert eff.device == "iPhone 15"
    assert eff.capture == ["screenshot.after", "elements", "actionLog"]
