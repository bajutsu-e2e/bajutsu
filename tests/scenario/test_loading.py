"""Tests for scenario loading, serialization round-trips, and setup expansion."""

from __future__ import annotations

from bajutsu.scenario import (
    Scenario,
    Step,
    apply_setups,
    dump_scenario_file,
    dump_scenarios,
    load_scenario_file,
    load_scenarios,
    redact_totp_secrets,
)

# A valid base32 TOTP seed used across the redaction tests below.
_TOTP_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

# Top-level example.
SCENARIO_YAML = """
- name: open settings and reindex
  preconditions:
    erase: true
    launchEnv: { SAMPLE_SCREEN: "settings" }
    deeplink: "bajutsusample://settings"
    locale: "ja_JP"
  steps:
    - tap: { id: settings.open }
    - tap: { id: settings.reindex }
      capture: [screenshot.after, deviceLog]
  expect:
    - exists: { label: "Normalization setting changed", negate: true }
"""


def test_scenario_file_descriptions() -> None:
    text = (
        "description: file-level note\n"
        "scenarios:\n"
        "  - name: a\n"
        "    description: per-scenario note\n"
        "    steps:\n"
        "      - tap: { id: x }\n"
    )
    sf = load_scenario_file(text)
    assert sf.description == "file-level note"
    assert sf.scenarios[0].description == "per-scenario note"
    # load_scenarios still returns just the scenarios (file description dropped)
    assert load_scenarios(text)[0].description == "per-scenario note"


def test_scenario_file_bare_list_has_no_description() -> None:
    sf = load_scenario_file("- name: a\n  steps:\n    - tap: { id: x }\n")
    assert sf.description is None and sf.scenarios[0].name == "a"
    assert sf.scenarios[0].description is None


def test_scenario_file_round_trips_with_descriptions() -> None:
    sf = load_scenario_file(
        "description: top\nscenarios:\n  - name: a\n    description: d\n    steps:\n      - tap: { id: x }\n"
    )
    rt = load_scenario_file(dump_scenario_file(sf.scenarios, sf.description))
    assert rt.description == "top" and rt.scenarios[0].description == "d"
    # without a file description, dump_scenario_file emits the bare list form
    assert load_scenario_file(dump_scenario_file(sf.scenarios)).description is None


def test_load_scenario_example() -> None:
    scenarios = load_scenarios(SCENARIO_YAML)
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.name == "open settings and reindex"
    assert s.preconditions.erase is True
    assert s.preconditions.launch_env == {"SAMPLE_SCREEN": "settings"}
    assert len(s.steps) == 2
    assert s.steps[1].capture == ["screenshot.after", "deviceLog"]
    assert s.expect[0].exists is not None
    assert s.expect[0].exists.negate is True
    assert s.expect[0].exists.sel.label == "Normalization setting changed"


def test_capture_policy_on_key_is_not_yaml_bool() -> None:
    # `on` must stay a string key, not YAML 1.1 boolean True.
    yaml_text = """
- name: rules
  steps:
    - tap: { id: home.title }
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [network]
"""
    s = load_scenarios(yaml_text)[0]
    assert s.capture_policy[0].on.action == "tap"


def test_dump_round_trip() -> None:
    text = """
- name: round trip
  preconditions:
    launchEnv: { K: "1" }
  steps:
    - tap: { id: home.go }
    - type: { text: "hi", into: { id: home.field } }
    - wait: { for: { id: home.done }, timeout: 5 }
    - assert:
        - exists: { id: home.done }
  expect:
    - value: { sel: { id: counter }, equals: "3" }
    - exists: { id: spinner, negate: true }
  capturePolicy:
    - on: { action: tap, idMatches: "*.go" }
      capture: [elements]
"""
    reloaded = load_scenarios(dump_scenarios(load_scenarios(text)))
    assert len(reloaded) == 1
    s = reloaded[0]
    assert s.name == "round trip"
    assert s.steps[0].tap is not None and s.steps[0].tap.id == "home.go"
    assert s.steps[1].type is not None and s.steps[1].type.into is not None
    assert s.expect[0].value is not None and s.expect[0].value.equals == "3"
    assert s.expect[1].exists is not None and s.expect[1].exists.negate is True
    assert s.capture_policy[0].on.action == "tap"


def test_provenance_from_round_trips_at_every_level() -> None:
    # `from:` (BE-0044) is provenance attached to the scenario, each step, each expect assertion,
    # and each capturePolicy rule; it must survive load -> dump -> load by its `from` alias.
    text = """
- name: open settings and reindex
  from: Open settings, reindex, and confirm the normalization setting is gone
  steps:
    - tap: { id: settings.open }
      from: Open settings
  expect:
    - exists: { label: "Normalization setting changed", negate: true }
      from: The normalization setting is gone
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [screenshot.after]
      from: Capture a screenshot on every submit
"""
    s = load_scenarios(dump_scenarios(load_scenarios(text)))[0]
    assert s.from_ == "Open settings, reindex, and confirm the normalization setting is gone"
    assert s.steps[0].from_ == "Open settings"
    assert s.expect[0].from_ == "The normalization setting is gone"
    assert s.capture_policy[0].from_ == "Capture a screenshot on every submit"


def test_provenance_is_pruned_when_absent() -> None:
    # A hand-authored scenario carries no provenance, so a dumped scenario stays clean — no stray
    # `from:` keys appear.
    text = "- name: plain\n  steps:\n    - tap: { id: home.go }\n"
    dumped = dump_scenarios(load_scenarios(text))
    assert "from:" not in dumped


def _step(sid: str) -> Step:
    return Step.model_validate({"tap": {"id": sid}})


def test_apply_setups_prepends_prelude_steps() -> None:
    scns = [
        Scenario.model_validate(
            {"name": "a", "preconditions": {"setup": "login.yaml"}, "steps": [{"tap": {"id": "x"}}]}
        ),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "y"}}]}),  # no setup
    ]
    seen: list[str] = []

    def resolve(ref: str) -> list[Step]:
        seen.append(ref)
        return [_step("auth.email"), _step("auth.submit")]

    apply_setups(scns, default_setup=None, resolve=resolve)
    assert [s.tap.id for s in scns[0].steps if s.tap] == ["auth.email", "auth.submit", "x"]
    assert [s.tap.id for s in scns[1].steps if s.tap] == ["y"]  # untouched
    assert seen == ["login.yaml"]


def test_apply_setups_default_is_shared_and_resolved_once() -> None:
    scns = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "x"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "y"}}]}),
    ]
    count = 0

    def resolve(ref: str) -> list[Step]:
        nonlocal count
        count += 1
        return [_step("prelude")]

    apply_setups(scns, default_setup="common.yaml", resolve=resolve)
    assert scns[0].steps[0].tap and scns[0].steps[0].tap.id == "prelude"
    assert scns[1].steps[0].tap and scns[1].steps[0].tap.id == "prelude"
    assert count == 1  # the shared default is resolved once and cached


def test_redact_totp_secrets_masks_a_literal_seed() -> None:
    # A literal base32 seed written straight into the scenario must not survive into an
    # evidence snapshot: it is masked with the fixed placeholder (BE-0152).
    scn = Scenario.model_validate(
        {"name": "a", "steps": [{"totp": {"secret": _TOTP_SEED, "into": {"var": "code"}}}]}
    )
    redacted = redact_totp_secrets(scn)
    assert redacted.steps[0].totp is not None
    assert redacted.steps[0].totp.secret == "<redacted>"
    assert (
        scn.steps[0].totp is not None and scn.steps[0].totp.secret == _TOTP_SEED
    )  # original intact


def test_redact_totp_secrets_keeps_a_secret_reference() -> None:
    # A `${secrets.*}` reference is not the seed — the resolved value is scrubbed by the run-level
    # secret pass (BE-0032) — so the reference is kept as-is, reviewable in the snapshot.
    scn = Scenario.model_validate(
        {"name": "a", "steps": [{"totp": {"secret": "${secrets.SEED}", "into": {"var": "code"}}}]}
    )
    redacted = redact_totp_secrets(scn)
    assert redacted.steps[0].totp is not None
    assert redacted.steps[0].totp.secret == "${secrets.SEED}"


def test_redact_totp_secrets_reaches_nested_steps() -> None:
    # totp can sit inside a control-flow block (forEach / if / web); the mask must reach it there.
    scn = Scenario.model_validate(
        {
            "name": "a",
            "steps": [
                {
                    "forEach": {
                        "sel": {"id": "rows"},
                        "as": "row",
                        "steps": [{"totp": {"secret": _TOTP_SEED, "into": {"var": "code"}}}],
                    }
                }
            ],
        }
    )
    redacted = redact_totp_secrets(scn)
    for_each = redacted.steps[0].for_each
    assert for_each is not None and for_each.steps[0].totp is not None
    assert for_each.steps[0].totp.secret == "<redacted>"
    assert _TOTP_SEED not in dump_scenarios([redacted])
