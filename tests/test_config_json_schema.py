"""`bajutsu config schema` and the scenario schema's `runsOn` stay in step with the models (BE-0452).

Both are generated from the config models and the platform registry, never written by hand, so these
tests pin the generated shape to the models: each registered platform is one branch keyed by
`platform`, each group lists exactly the fields its model accepts, and configs the loader accepts
validate against it while a key from another platform fails in both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.backends import PLATFORMS
from bajutsu.common.config import load_config
from bajutsu.common.config.json_schema import config_json_schema, scenario_runs_on_schema
from bajutsu.common.config.schema.defaults import DEFAULTS_KEYS
from bajutsu.common.config.schema.platform.registry import (
    GROUPS,
    PLATFORM_SCHEMAS,
    allowed_fields,
)
from bajutsu.common.config.schema.target_config import TARGET_KEYS, run_model
from bajutsu.common.lint import scenario_json_schema

_REPO = Path(__file__).resolve().parent.parent
_SCHEMA = config_json_schema()
_DEFS: dict[str, Any] = _SCHEMA["$defs"]


def _resolve(ref: dict[str, Any]) -> dict[str, Any]:
    target = next(s for s in ref.get("anyOf", [ref]) if "$ref" in s)
    resolved: dict[str, Any] = _DEFS[target["$ref"].rsplit("/", 1)[-1]]
    return resolved


def _branch(platform: str) -> dict[str, Any]:
    targets = _SCHEMA["properties"]["targets"]["additionalProperties"]
    mapping = targets["discriminator"]["mapping"]
    branch: dict[str, Any] = _DEFS[mapping[platform].rsplit("/", 1)[-1]]
    return branch


def test_each_registered_platform_is_one_branch_keyed_by_platform() -> None:
    targets = _SCHEMA["properties"]["targets"]["additionalProperties"]
    assert targets["discriminator"]["propertyName"] == "platform"
    assert set(targets["discriminator"]["mapping"]) == set(PLATFORM_SCHEMAS) == set(PLATFORMS)
    assert len(targets["oneOf"]) == len(PLATFORM_SCHEMAS)
    for platform in PLATFORM_SCHEMAS:
        branch = _branch(platform)
        assert branch["properties"]["platform"]["const"] == platform
        assert tuple(branch["properties"]) == (*TARGET_KEYS, "extends")
        assert branch["additionalProperties"] is False


@pytest.mark.parametrize("platform", list(PLATFORM_SCHEMAS))
def test_each_group_lists_exactly_the_fields_its_model_accepts(platform: str) -> None:
    schema = PLATFORM_SCHEMAS[platform]
    props = _branch(platform)["properties"]
    for group in GROUPS:
        model = run_model(platform) if group == "run" else schema.model(group)
        assert set(_resolve(props[group]).get("properties", {})) == set(allowed_fields(model))


def test_defaults_name_every_platform_with_every_field_optional() -> None:
    defaults = _DEFS["Defaults"]
    assert set(defaults["properties"]) == set(DEFAULTS_KEYS)
    platforms = defaults["properties"]["platforms"]["properties"]
    assert set(platforms) == set(PLATFORM_SCHEMAS)
    assert set(platforms["ios"]["properties"]) == set(GROUPS)
    assert "required" not in platforms["ios"]["properties"]["app"]
    assert "id" in platforms["ios"]["properties"]["app"]["properties"]


_EXAMPLE = """
defaults:
  run: { erase: true }
  platforms:
    ios:
      app: { launch: { env: { UITEST: "1" } } }
      runsOn: { os: ">=17" }
targets:
  phone:
    platform: ios
    app: { id: com.example.app }
    runsOn: { model: ["iPhone 16", "iPad Pro 13-inch (M4)"] }
  emulator:
    platform: android
    app: { id: com.example.app }
    runsOn: { avd: Pixel_8, apiLevel: 34 }
  site:
    platform: web
    app: { url: "http://localhost:3000/" }
    runsOn: { browser: { engine: [chromium, webkit] } }
"""


def test_a_config_the_loader_accepts_validates() -> None:
    load_config(_EXAMPLE)
    Draft202012Validator(_SCHEMA).validate(yaml.safe_load(_EXAMPLE))


# Every config the repository ships: the root one and each demo's (`tmp/` and worktrees are not).
_REPO_CONFIGS = sorted(
    p.relative_to(_REPO).as_posix()
    for p in [*_REPO.glob("*.config.yaml"), *(_REPO / "demos").rglob("*.config.yaml")]
)


@pytest.mark.parametrize("path", _REPO_CONFIGS)
def test_the_repository_configs_validate(path: str) -> None:
    data = yaml.safe_load((_REPO / path).read_text(encoding="utf-8"))
    Draft202012Validator(_SCHEMA).validate(data)


def test_every_shipped_config_is_checked() -> None:
    assert len(_REPO_CONFIGS) >= 10
    assert "demos/showcase/showcase.config.yaml" in _REPO_CONFIGS


def test_exists_is_stated_in_its_inline_form() -> None:
    hook = {"exists": {"id": ["aerr_wait"], "negate": True}}
    text = (
        "targets:\n  t:\n    platform: ios\n    app:\n      id: com.x\n"
        "      startWhen: { exists: { id: home.title } }\n"
        f"    hooks: {{ interrupts: [{{ condition: {json.dumps(hook)}, steps: [] }}] }}\n"
    )
    load_config(text)
    Draft202012Validator(_SCHEMA).validate(yaml.safe_load(text))
    scenario = json.loads(scenario_json_schema())
    good = [{"name": "a", "steps": [], "expect": [{"exists": {"id": "x", "negate": True}}]}]
    assert not list(Draft202012Validator(scenario).iter_errors(good))


@pytest.mark.parametrize(
    "target",
    [
        # A key another platform takes: the loader names it unknown, the schema rejects it.
        "{ platform: ios, app: { id: com.x }, runsOn: { avd: Pixel_8 } }",
        "{ platform: web, app: { url: 'http://x/' }, driver: { nativeZ: true } }",
        "{ platform: flutter, app: { id: com.x } }",
    ],
)
def test_a_key_from_another_platform_fails_in_both(target: str) -> None:
    text = f"targets:\n  t: {target}\n"
    with pytest.raises(ValueError):  # noqa: PT011 - the loader's own message is pinned elsewhere
        load_config(text)
    assert list(Draft202012Validator(_SCHEMA).iter_errors(yaml.safe_load(text)))


def test_the_command_prints_the_schema() -> None:
    result = CliRunner().invoke(app, ["config", "schema"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _SCHEMA


def test_the_scenario_schema_states_runs_on_per_platform() -> None:
    schema = json.loads(scenario_json_schema())
    runs_on = schema["$defs"]["Preconditions"]["properties"]["runsOn"]
    assert set(runs_on["properties"]) == set(PLATFORM_SCHEMAS)
    expected = scenario_runs_on_schema()
    for platform, block in runs_on["properties"].items():
        fields = set(allowed_fields(PLATFORM_SCHEMAS[platform].runs_on)) - {"kind", "emulate"}
        assert set(block["properties"]) == fields
        assert block == expected["properties"][platform]
    validator = Draft202012Validator(schema)
    good = [{"name": "a", "steps": [], "preconditions": {"runsOn": {"ios": {"os": ">=18"}}}}]
    assert not list(validator.iter_errors(good))
    driven = [{"name": "a", "steps": [], "preconditions": {"runsOn": {"ios": {"kind": "device"}}}}]
    assert list(validator.iter_errors(driven))
