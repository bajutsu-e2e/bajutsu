"""Target templates: `templates:` blocks a target folds in with `extends` (BE-0471).

The layers fold weakest first — `defaults`, `defaults.platforms.<platform>`, each `extends` entry in
listed order, then the target — under the merge rules every layer already shares. These tests pin
that order, the errors that fail the load, a template file's own relative paths, and that a
template is never a target.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaError
from pydantic import ValidationError

from bajutsu.common.config import load_config, load_config_file, resolve, unknown_target
from bajutsu.common.config.effective import Effective, IosConfig
from bajutsu.common.config.json_schema import config_json_schema
from bajutsu.common.config.schema._templates import REBASED_PATHS, _rebased
from bajutsu.common.config.schema.hooks import STEP_CONTAINERS


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_layers_fold_from_defaults_through_templates_to_the_target() -> None:
    config = load_config(
        """
defaults:
  evidence: { collectNetwork: false }
  platforms: { ios: { app: { launch: { env: { LAYER: platform, P: "1" } } } } }
templates:
  first: { app: { launch: { env: { LAYER: first, F: "1" } } } }
  second: { app: { launch: { env: { LAYER: second, S: "1" } } } }
targets:
  t:
    extends: [first, second]
    platform: ios
    app: { id: com.x }
"""
    )
    eff = resolve(config, "t")
    assert eff.launch_env == {"LAYER": "second", "P": "1", "F": "1", "S": "1"}
    assert eff.run_defaults.network is False


def test_the_target_wins_over_every_template() -> None:
    config = load_config(
        """
templates:
  base: { platform: ios, app: { id: com.base, launch: { args: [-a] } } }
targets:
  t: { extends: base, app: { id: com.x } }
"""
    )
    eff = resolve(config, "t")
    assert eff.app_identifier == "com.x"
    assert eff.launch_args == ["-a"]


def test_an_extending_template_wins_over_its_base_and_a_shared_base_folds_once() -> None:
    config = load_config(
        """
templates:
  base: { platform: ios, app: { launch: { env: { WHO: base } } } }
  child: { extends: base, app: { launch: { env: { WHO: child } } } }
targets:
  # `base` again after `child` keeps its first position, so `child` still wins.
  t: { extends: [child, base], app: { id: com.x } }
"""
    )
    assert resolve(config, "t").launch_env == {"WHO": "child"}


def test_a_template_null_clears_a_value_defaults_supplies() -> None:
    config = load_config(
        """
defaults:
  platforms: { ios: { app: { launch: { env: { DROP: "1", KEEP: "1" } } } } }
templates:
  clear: { app: { launch: { env: { DROP: null } } } }
targets:
  t: { extends: clear, platform: ios, app: { id: com.x } }
"""
    )
    assert resolve(config, "t").launch_env == {"KEEP": "1"}


def test_union_lists_keep_every_layer_and_refuse_a_null() -> None:
    text = """
defaults: { evidence: { secrets: [TEAM] } }
templates:
  t1: { evidence: { secrets: [ONE], redact: { fields: [password] } } }
  t2: { evidence: { secrets: [TWO, ONE] } }
targets:
  t: { extends: [t1, t2], platform: ios, app: { id: com.x }, evidence: { secrets: [OWN] } }
"""
    eff = resolve(load_config(text), "t")
    assert eff.secrets == ["TEAM", "ONE", "TWO", "OWN"]
    assert eff.redact.fields == ["password"]
    with pytest.raises(ValidationError, match=r"evidence\.secrets: null cannot clear it"):
        load_config(text.replace("secrets: [TWO, ONE]", "secrets: null"))


def test_a_template_ai_folds_before_defaults_ai_merges_field_by_field() -> None:
    config = load_config(
        """
defaults: { ai: { provider: anthropic, model: base-model } }
templates:
  ai: { ai: { model: template-model, effort: high } }
targets:
  t: { extends: ai, platform: ios, app: { id: com.x }, ai: { effort: low } }
"""
    )
    ai = resolve(config, "t").ai
    assert ai is not None
    assert (ai.provider, ai.model, ai.effort) == ("anthropic", "template-model", "low")


def test_a_target_takes_its_platform_from_a_template() -> None:
    config = load_config(
        """
defaults: { platforms: { web: { driver: { headless: false } } } }
templates:
  site: { platform: web, app: { url: "http://localhost:3000/" } }
targets:
  t: { extends: site }
"""
    )
    eff = resolve(config, "t")
    assert eff.schema_platform == "web"
    # The platform block of `defaults` follows the platform the template declares.
    assert getattr(eff.platform_config, "headless", None) is False


def test_layers_that_declare_different_platforms_fail_naming_both() -> None:
    with pytest.raises(
        ValidationError, match=r"template 'base' declares platform 'ios' but target"
    ):
        load_config(
            "templates:\n  base: { platform: ios }\n"
            "targets:\n  t: { extends: base, platform: web, app: { url: 'http://x/' } }\n"
        )


def test_a_target_with_no_platform_from_any_layer_fails_as_before() -> None:
    with pytest.raises(ValidationError, match="a target needs `platform`"):
        load_config(
            "templates:\n  base: { app: { id: com.x } }\ntargets:\n  t: { extends: base }\n"
        )


def test_an_unknown_template_lists_the_known_ones() -> None:
    with pytest.raises(ValidationError, match=r"unknown template 'nope'; templates: a, b"):
        load_config(
            "templates: { a: {}, b: {} }\n"
            "targets:\n  t: { extends: nope, platform: ios, app: { id: com.x } }\n"
        )


def test_a_cycle_prints_its_path() -> None:
    with pytest.raises(ValidationError, match="a template cycle: a → b → a"):
        load_config(
            "templates:\n  a: { extends: b }\n  b: { extends: a }\n"
            "targets:\n  t: { extends: a, platform: ios, app: { id: com.x } }\n"
        )


def test_a_template_key_outside_the_target_keys_fails_even_unextended() -> None:
    # Checked on every template, so a typo in one no target uses yet fails now, not on first use.
    with pytest.raises(ValidationError, match=r"templates\.a: unknown field\(s\) 'bundleId'"):
        load_config("templates:\n  a: { bundleId: com.x }\ntargets: {}\n")
    with pytest.raises(ValidationError, match=r"templates\.a: a template must be a mapping"):
        load_config("templates:\n  a: [platform]\n")


def test_a_template_file_key_outside_the_target_keys_fails(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", "bundleId: com.x\n")
    cfg = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ./a.yaml, platform: ios, app: { id: com.x } }\n",
    )
    with pytest.raises(ValidationError, match=r"template file './a.yaml': unknown field"):
        load_config_file(cfg)


def test_a_template_file_that_is_not_utf8_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_bytes(b"platform: \xff\xfe\n")
    cfg = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ./a.yaml, platform: ios, app: { id: com.x } }\n",
    )
    with pytest.raises(ValidationError, match=r"cannot read template file '\./a\.yaml'"):
        load_config_file(cfg)


def test_a_target_null_clears_a_value_a_template_supplies() -> None:
    config = load_config(
        "templates: { base: { platform: ios, app: { launch: { args: [-a], env: { E: '1' } } } } }\n"
        "targets:\n  t: { extends: base, app: { id: com.x, launch: { args: null } } }\n"
    )
    eff = resolve(config, "t")
    assert (eff.launch_args, eff.launch_env) == ([], {"E": "1"})


def test_a_malformed_extends_fails() -> None:
    with pytest.raises(ValidationError, match="must be a template name or a list of them"):
        load_config("targets:\n  t: { extends: { a: 1 }, platform: ios, app: { id: com.x } }\n")


def test_a_file_template_resolves_its_own_relative_paths(tmp_path: Path) -> None:
    _write(
        tmp_path / "shared" / "ios.template.yaml",
        """
platform: ios
app: { path: build/App.app }
runsOn: { seedPhotos: [photos/a.png, /abs/b.png] }
driver: { runner: { testRunner: ../runner/R.xctestrun } }
paths: { baselines: baselines }
""",
    )
    cfg = _write(
        tmp_path / "suite" / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ../shared/ios.template.yaml, app: { id: com.x } }\n",
    )
    eff = resolve(load_config_file(cfg, root=tmp_path), "t").rebased(cfg.parent, confine=False)
    shared = (tmp_path / "shared").resolve()
    ios = eff.platform_config
    assert isinstance(ios, IosConfig) and ios.xcuitest is not None
    assert Path(ios.app_path or "").resolve() == shared / "build" / "App.app"
    assert [Path(p).resolve() for p in eff.seed_photos] == [
        shared / "photos" / "a.png",
        Path("/abs/b.png").resolve(),
    ]
    assert (
        Path(ios.xcuitest.test_runner or "").resolve()
        == (tmp_path / "runner" / "R.xctestrun").resolve()
    )
    assert Path(eff.evidence_dirs.baselines or "").resolve() == shared / "baselines"


def test_a_file_template_chains_files_relative_to_itself(tmp_path: Path) -> None:
    _write(tmp_path / "t" / "base.yaml", "platform: ios\napp: { launch: { env: { B: '1' } } }\n")
    _write(tmp_path / "t" / "child.yaml", "extends: ./base.yaml\napp: { launch: { args: [-c] } }\n")
    cfg = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  t: { extends: t/child.yaml, app: { id: com.x } }\n",
    )
    eff = resolve(load_config_file(cfg), "t")
    assert (eff.launch_env, eff.launch_args) == ({"B": "1"}, ["-c"])


def test_a_file_template_names_files_alone(tmp_path: Path) -> None:
    _write(tmp_path / "child.yaml", "extends: base\n")
    cfg = _write(
        tmp_path / "bajutsu.config.yaml",
        "templates: { base: { platform: ios } }\n"
        "targets:\n  t: { extends: ./child.yaml, app: { id: com.x } }\n",
    )
    with pytest.raises(ValidationError, match="in a template file must be a file path"):
        load_config_file(cfg)


def test_a_file_template_rebases_the_components_its_hooks_call(tmp_path: Path) -> None:
    _write(
        tmp_path / "shared" / "parts" / "wait.yaml",
        "steps:\n  - tap: { id: ready }\n",
    )
    _write(
        tmp_path / "shared" / "hooks.yaml",
        """
hooks:
  setup:
    - if: { condition: { exists: { id: gate } }, then: [{ use: { component: parts/wait.yaml } }] }
  cleanup:
    - on: always
      steps: [{ use: { component: parts/wait.yaml } }]
""",
    )
    cfg = _write(
        tmp_path / "suite" / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ../shared/hooks.yaml, platform: ios, app: { id: com.x } }\n",
    )
    run = resolve(load_config_file(cfg, root=tmp_path), "t").run_defaults
    gated = run.setup[0].if_
    assert gated is not None and gated.then[0].tap is not None
    assert run.cleanup[0].steps[0].tap is not None


# Every YAML key path a step nests further steps under, spelled out here rather than read from
# `STEP_CONTAINERS`, so a wrong key in that table fails a case instead of skipping the container.
_NESTING = [
    ("steps",),
    ("if", "then"),
    ("if", "else"),
    ("forEach", "steps"),
    ("web", "steps"),
    ("app", "steps"),
]


def test_the_nesting_cases_cover_every_step_container() -> None:
    assert {keys for _, keys in STEP_CONTAINERS} == set(_NESTING)


@pytest.mark.parametrize("keys", _NESTING, ids=".".join)
def test_a_file_template_rebases_a_use_under_every_step_container(
    tmp_path: Path, keys: tuple[str, ...]
) -> None:
    step: dict[str, object] = {"use": {"component": "parts/wait.yaml"}}
    for key in reversed(keys):
        step = {key: [step] if key in {"steps", "then", "else"} else step}
    hooks = {"hooks": {"setup": [step]}}
    out = _rebased(hooks, tmp_path / "shared", tmp_path / "suite")
    nested: object = out["hooks"]["setup"][0]
    for key in keys:
        assert isinstance(nested, dict)
        nested = nested[key]
        if isinstance(nested, list):
            nested = nested[0]
    assert nested == {"use": {"component": "../shared/parts/wait.yaml"}}


def test_a_file_template_must_stay_inside_the_suite_root(tmp_path: Path) -> None:
    _write(tmp_path / "outside.yaml", "platform: ios\n")
    cfg = _write(
        tmp_path / "suite" / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ../outside.yaml, app: { id: com.x } }\n",
    )
    with pytest.raises(ValidationError, match="resolves outside the suite root"):
        load_config_file(cfg)


def test_a_missing_file_template_fails_loudly(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ./gone.yaml, platform: ios, app: { id: com.x } }\n",
    )
    with pytest.raises(ValidationError, match=r"cannot read template file '\./gone\.yaml'"):
        load_config_file(cfg)


def test_a_config_with_no_file_names_inline_templates_alone() -> None:
    with pytest.raises(ValidationError, match="loaded without its path"):
        load_config("targets:\n  t: { extends: ./a.yaml, platform: ios, app: { id: com.x } }\n")


def test_the_rebased_paths_match_the_fields_effective_rebases() -> None:
    # The raw rewrite and `Effective.rebased` name the same path fields, so one added to either
    # alone fails here rather than resolving against two bases.
    rebased = set(re.findall(r'at\("([\w.]+)"', inspect.getsource(Effective.rebased)))
    assert {".".join(path) for path in REBASED_PATHS} == rebased


def test_a_template_is_never_a_target() -> None:
    config = load_config(
        "templates: { showcase-ios: { platform: ios } }\n"
        "targets:\n  b: { extends: showcase-ios, app: { id: com.b } }\n"
        "  a: { extends: showcase-ios, app: { id: com.a } }\n"
    )
    assert set(config.targets) == {"a", "b"}
    with pytest.raises(KeyError, match="'showcase-ios' is a template, not a target; targets: a, b"):
        resolve(config, "showcase-ios")
    assert unknown_target(config, "nope").startswith("unknown target: 'nope'")


def test_a_template_and_a_target_may_share_a_name() -> None:
    config = load_config(
        "templates: { app: { platform: ios, app: { launch: { args: [-t] } } } }\n"
        "targets:\n  app: { extends: app, app: { id: com.x } }\n"
    )
    assert resolve(config, "app").launch_args == ["-t"]


_SCHEMA = config_json_schema()


@pytest.mark.parametrize(
    "templates",
    [
        {"ios": {"platform": "ios", "app": {"launch": {"env": {"A": "1"}}}}},
        {"bare": {"app": {"url": "http://x/"}, "evidence": {"secrets": ["S"]}}},
        {"chained": {"extends": ["a", "./b.yaml"], "evidence": {"redact": {"fields": ["pw"]}}}},
        # A template's `null` clears a group `defaults` set, as a target's may.
        {"clear": {"platform": "ios", "app": None}},
        {"clear": {"driver": None}},
    ],
)
def test_the_schema_accepts_a_template(templates: dict[str, object]) -> None:
    Draft202012Validator(_SCHEMA).validate({"templates": templates})


@pytest.mark.parametrize(
    "templates",
    [
        {"typo": {"bundleId": "com.x"}},
        # A template that names its platform takes that platform's groups only.
        {"web": {"platform": "web", "app": {"id": "com.x"}}},
    ],
)
def test_the_schema_refuses_a_malformed_template(templates: dict[str, object]) -> None:
    with pytest.raises(SchemaError):
        Draft202012Validator(_SCHEMA).validate({"templates": templates})


def test_the_schema_accepts_extends_on_a_target() -> None:
    text = "targets:\n  t: { extends: [a, ./b.yaml], platform: ios }\n"
    Draft202012Validator(_SCHEMA).validate(yaml.safe_load(text))


_SHOWCASE = Path(__file__).resolve().parent.parent / "demos" / "showcase" / "showcase.config.yaml"
_RUNNER = "../../BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun"


def test_the_showcase_writes_each_shared_block_once() -> None:
    # The showcase moved its repeated blocks into templates (BE-0471); a target that writes one back
    # in place is the copy that drifts without an error.
    raw = yaml.safe_load(_SHOWCASE.read_text(encoding="utf-8"))
    for name, target in raw["targets"].items():
        assert "runner" not in target.get("driver", {}), name
        assert "env" not in target.get("app", {}).get("launch", {}), name
        assert "interrupts" not in target.get("hooks", {}) or name == "showcase-swiftui-bundled", (
            name
        )


def test_every_showcase_target_still_resolves_its_shared_settings() -> None:
    # The move left every target's `Effective` unchanged; this pins the three settings it moved.
    config = load_config_file(_SHOWCASE)
    for name in config.targets:
        eff = resolve(config, name)
        if eff.schema_platform == "web":
            continue
        assert eff.launch_env == {"SHOWCASE_UITEST": "1"}, name
        if eff.schema_platform == "android":
            guards = [i.steps[0].tap for i in eff.run_defaults.interrupts]
            assert [g.id if g else None for g in guards] == [["aerr_close"]], name
            continue
        runner = eff.platform_config.xcuitest  # type: ignore[union-attr]
        if name == "showcase-swiftui-bundled":
            assert runner is None
        else:
            assert runner is not None and runner.test_runner == _RUNNER, name


def test_a_rebased_component_ref_stays_a_path(tmp_path: Path) -> None:
    # `../comp` from `sub/` rebases to `comp`, which `use` would read as a name without the `./`.
    _write(tmp_path / "comp", "steps:\n  - tap: { id: ready }\n")
    _write(tmp_path / "sub" / "t.yaml", "hooks: { setup: [{ use: { component: ../comp } }] }\n")
    cfg = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  t: { extends: ./sub/t.yaml, platform: ios, app: { id: com.x } }\n",
    )
    assert resolve(load_config_file(cfg), "t").run_defaults.setup[0].tap is not None
