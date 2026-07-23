"""Tests for XCUITest code generation (structural scenario -> Swift)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.codegen import CodegenError, class_name_for, to_xcuitest
from bajutsu.scenario import load_scenarios


def _gen(yaml: str, env: dict[str, str] | None = None) -> str:
    scenarios = load_scenarios(yaml)
    return to_xcuitest(scenarios, "FixtureUITests", env or {})


def test_header_and_helpers() -> None:
    code = _gen("- name: x\n  steps:\n    - tap: { id: a }\n")
    assert "import XCTest" in code
    assert "final class FixtureUITests: XCTestCase {" in code
    assert "private func el(_ id: String) -> XCUIElement" in code
    assert 'NSPredicate(format: "identifier LIKE %@", glob)' in code


def test_launch_env_merges_app_and_scenario() -> None:
    code = _gen(
        "- name: x\n  preconditions:\n    launchEnv: { B: '2' }\n  steps:\n    - tap: { id: a }\n",
        env={"A": "1"},
    )
    assert 'app.launchEnvironment["A"] = "1"' in code
    assert 'app.launchEnvironment["B"] = "2"' in code
    assert "app.launch()" in code


def test_method_name_is_sanitized() -> None:
    code = _gen("- name: onboard, log in!\n  steps:\n    - tap: { id: a }\n")
    assert "func test_onboard_log_in() {" in code


def test_tap_type_and_wait() -> None:
    code = _gen(
        "- name: x\n  steps:\n"
        "    - tap: { id: onboarding.start }\n"
        "    - type: { text: a@b.com, into: { id: auth.email } }\n"
        "    - wait: { for: { id: home.title }, timeout: 5 }\n"
    )
    assert 'el("onboarding.start").tap()' in code
    assert 'el("auth.email").tap()' in code
    assert 'el("auth.email").typeText("a@b.com")' in code
    assert 'el("home.title").waitForExistence(timeout: 5.0)' in code


def test_text_editing_steps_emit_xcuitest_peers() -> None:
    # BE-0265: clear -> select-all + delete, delete -> repeated delete key, select -> Cmd+A,
    # copy -> Cmd+C on the app.
    code = _gen(
        "- name: x\n  steps:\n"
        "    - clear: { into: { id: form.note } }\n"
        "    - delete: { into: { id: form.note }, count: 2 }\n"
        "    - select: { into: { id: form.note } }\n"
        "    - copy: {}\n"
    )
    assert 'el("form.note").typeKey("a", modifierFlags: .command)' in code
    assert 'el("form.note").typeText(XCUIKeyboardKey.delete.rawValue)' in code
    assert (
        'el("form.note").typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 2))'
        in code
    )
    assert 'app.typeKey("c", modifierFlags: .command)' in code


def test_long_press_and_swipe() -> None:
    code = _gen(
        "- name: x\n  steps:\n"
        "    - longPress: { sel: { id: comp.longpress }, duration: 0.6 }\n"
        "    - swipe: { on: { id: comp.area }, direction: left }\n"
    )
    assert 'el("comp.longpress").press(forDuration: 0.6)' in code
    assert 'el("comp.area").swipeLeft()' in code


def test_drag_maps_to_swipe_gesture() -> None:
    # `drag` (BE-0227) is an element-anchored pointer drag; XCUITest's swipeX() is a real drag, so it
    # emits the same primitive a directional swipe does.
    code = _gen("- name: x\n  steps:\n    - drag: { on: { id: comp.divider }, direction: right }\n")
    assert 'el("comp.divider").swipeRight()' in code


def test_coordinate_swipe_maps_to_coordinate_drag() -> None:
    # `swipe { from, to }` (BE-0025): a coordinate drag via XCUICoordinate, not a `// TODO`.
    code = _gen("- name: x\n  steps:\n    - swipe: { from: [10, 20], to: [30, 40] }\n")
    assert "coord(10.0, 20.0).press(forDuration: 0.1, thenDragTo: coord(30.0, 40.0))" in code
    assert "private func coord(" in code  # the anchor helper is emitted
    assert "coordinate swipe" not in code  # the TODO fallback is gone for this form


def test_wait_until_gone() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - wait: { until: { gone: { id: spinner } }, timeout: 3 }\n"
    )
    assert 'el("spinner").waitForNonExistence(timeout: 3.0)' in code


def test_tap_by_label() -> None:
    code = _gen('- name: x\n  steps:\n    - tap: { label: "Delete" }\n')
    assert 'byLabel("Delete").tap()' in code


def test_back_taps_the_os_back_button() -> None:
    # BE-0210: iOS has no hardware back, so the generated XCUITest taps the OS navigation back
    # button ("BackButton") — the same element the XCUITest driver taps at runtime.
    code = _gen("- name: x\n  steps:\n    - back: {}\n")
    assert 'el("BackButton").tap()' in code
    assert "TODO" not in code


def test_assertions_exists_value_count() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - tap: { id: a }\n  expect:\n"
        "    - exists: { id: home.title }\n"
        "    - exists: { id: spinner, negate: true }\n"
        "    - value: { sel: { id: counter }, equals: '2' }\n"
        "    - count: { sel: { idMatches: 'list.row.*' }, equals: 5 }\n"
    )
    assert 'XCTAssertTrue(el("home.title").exists)' in code
    assert 'XCTAssertFalse(el("spinner").exists)' in code
    assert 'XCTAssertEqual(el("counter").value as? String, "2")' in code
    assert 'XCTAssertEqual(matchingId("list.row.*").count, 5)' in code


def test_state_assertions() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - tap: { id: a }\n  expect:\n"
        "    - enabled: { id: auth.submit }\n"
        "    - disabled: { id: auth.cancel }\n"
        "    - selected: { id: toggle }\n"
    )
    assert 'XCTAssertTrue(el("auth.submit").isEnabled)' in code
    assert 'XCTAssertFalse(el("auth.cancel").isEnabled)' in code
    assert 'XCTAssertTrue(el("toggle").isSelected)' in code


# --- BE-0026: compound selectors map structurally instead of UNSUPPORTED_SELECTOR ---


def test_compound_id_and_value_maps_to_predicate() -> None:
    code = _gen("- name: x\n  steps:\n    - tap: { id: counter, value: '5' }\n")
    assert (
        "app.descendants(matching: .any).matching(NSPredicate(format: "
        '"identifier == %@ AND value == %@", "counter", "5")).firstMatch.tap()' in code
    )


def test_index_maps_to_bound_by() -> None:
    code = _gen("- name: x\n  steps:\n    - tap: { idMatches: 'row.*', index: 2 }\n")
    assert (
        'matching(NSPredicate(format: "identifier LIKE %@", "row.*")).element(boundBy: 2).tap()'
        in code
    )


def test_negative_index_maps_to_count_offset() -> None:
    # Bajutsu's negative index counts from the end (`candidates[i]`), which XCUITest expresses
    # faithfully as `element(boundBy: query.count + index)` — `count - 1` is the last match.
    query = 'app.descendants(matching: .any).matching(NSPredicate(format: "identifier LIKE %@", "row.*"))'
    last = _gen("- name: x\n  steps:\n    - tap: { idMatches: 'row.*', index: -1 }\n")
    assert f"{query}.element(boundBy: {query}.count - 1).tap()" in last
    second_last = _gen("- name: x\n  steps:\n    - tap: { idMatches: 'row.*', index: -2 }\n")
    assert f"{query}.element(boundBy: {query}.count - 2).tap()" in second_last


def test_within_stays_unsupported() -> None:
    # `within` is geometric frame containment, not tree descendants — no faithful XCUITest query.
    code = _gen("- name: x\n  steps:\n    - tap: { id: row.action, within: { id: list.row } }\n")
    assert 'el("UNSUPPORTED_SELECTOR").tap()' in code


def test_label_matches_literal_maps_to_contains() -> None:
    code = _gen("- name: x\n  steps:\n    - tap: { labelMatches: 'Delete' }\n")
    assert 'matching(NSPredicate(format: "label CONTAINS %@", "Delete")).firstMatch.tap()' in code


def test_label_matches_regex_stays_unsupported() -> None:
    # A real regex (anchors/metachars) has no faithful NSPredicate form (re.search vs full match).
    code = _gen("- name: x\n  steps:\n    - tap: { labelMatches: '^Item ' }\n")
    assert 'el("UNSUPPORTED_SELECTOR").tap()' in code


def test_traits_map_to_element_type_and_state() -> None:
    button = _gen("- name: x\n  steps:\n    - tap: { traits: [button] }\n")
    assert (
        'matching(NSPredicate(format: "elementType == %ld", '
        "XCUIElement.ElementType.button.rawValue)).firstMatch.tap()" in button
    )
    disabled = _gen("- name: x\n  steps:\n    - tap: { id: a, traits: [notEnabled] }\n")
    assert '"identifier == %@ AND enabled == NO", "a"' in disabled
    selected = _gen("- name: x\n  steps:\n    - tap: { id: t, traits: [selected] }\n")
    assert '"identifier == %@ AND selected == YES", "t"' in selected


def test_traits_only_selector_has_no_trailing_comma() -> None:
    # notEnabled/selected add self-contained clauses (no arg); a traits-only selector must not
    # emit `NSPredicate(format: "enabled == NO", )` (a trailing comma is invalid Swift).
    code = _gen("- name: x\n  steps:\n    - tap: { traits: [notEnabled] }\n")
    assert 'matching(NSPredicate(format: "enabled == NO")).firstMatch.tap()' in code
    assert ", )" not in code


def test_compound_label_traits_index() -> None:
    code = _gen(
        "- name: x\n  steps:\n    - tap: { labelMatches: 'Item', traits: [button], index: 0 }\n"
    )
    assert (
        'matching(NSPredicate(format: "label CONTAINS %@ AND elementType == %ld", "Item", '
        "XCUIElement.ElementType.button.rawValue)).element(boundBy: 0).tap()" in code
    )


def test_simple_selectors_keep_their_helpers() -> None:
    # Single-field selectors are unchanged (stable, readable output).
    code = _gen(
        "- name: x\n  steps:\n    - tap: { id: a }\n    - tap: { label: B }\n"
        "    - tap: { idMatches: 'r.*' }\n"
    )
    assert 'el("a").tap()' in code
    assert 'byLabel("B").tap()' in code
    assert 'matchingId("r.*").firstMatch.tap()' in code


def test_id_candidate_list_emits_primary_candidate() -> None:
    # A generated test targets one platform (iOS here), so a cross-platform candidate list (BE-0221)
    # emits its primary (first, dotted SPEC) candidate — the id this platform surfaces.
    code = _gen(
        "- name: x\n  steps:\n"
        "    - tap: { id: [stable.refresh, stable_refresh] }\n"
        "    - tap: { idMatches: [stable.row.*, stable_row_*] }\n"
    )
    assert 'el("stable.refresh").tap()' in code
    assert 'matchingId("stable.row.*").firstMatch.tap()' in code
    assert "stable_refresh" not in code  # the underscore alternate is not emitted for iOS


def test_device_control_steps_emit_labeled_todo() -> None:
    code = _gen(
        "- name: x\n  steps:\n"
        "    - setLocation: { lat: 35.6, lon: 139.7 }\n"
        "    - push: { payload: { aps: { alert: hi } } }\n"
    )
    assert "// TODO: setLocation(lat: 35.6, lon: 139.7) — simctl location" in code
    assert "// TODO: push" in code and "simctl push" in code


def test_permissions_field_emits_a_labeled_todo_per_service() -> None:
    # `permissions` (BE-0276) is a scenario-level field, not a step: bajutsu applies it before the
    # generated test's launch, so it stays a labeled TODO naming each service individually.
    code = _gen(
        "- name: x\n  permissions: { camera: grant, location: revoke }\n  steps:\n"
        "    - tap: { id: a }\n"
    )
    assert "// TODO: permissions.camera (grant)" in code
    assert "// TODO: permissions.location (revoke)" in code


def test_interrupts_field_emits_a_labeled_todo_per_entry() -> None:
    # `interrupts` (BE-0314) has no native "check this condition throughout the test" construct, so —
    # like `permissions` — it stays a labeled TODO naming the field and each entry's condition, rather
    # than a silent skip that would fake a pass.
    code = _gen(
        "- name: x\n  interrupts:\n"
        "    - condition: { exists: { id: att.dialog } }\n"
        "      steps:\n        - tap: { id: att.allow }\n"
        "  steps:\n    - tap: { id: a }\n"
    )
    assert "// TODO: interrupts[0]" in code
    assert "att.dialog" in code
    assert "checks this opportunistically at run time" in code


def test_request_assertion_emits_labeled_todo() -> None:
    # XCUITest has no network interception, so a `request` assertion stays a TODO — but a labeled one
    # naming the endpoint and why, like the device-control steps, not a bare "unsupported" (BE-0026).
    code = _gen(
        "- name: x\n  steps:\n    - assert:\n        - request: { method: GET, path: /api/items }\n"
    )
    assert "// TODO: request assertion (GET /api/items)" in code
    assert "no network interception" in code
    assert "unsupported assertion" not in code  # the bare fallback is gone for this form


def test_request_assertion_label_keeps_count() -> None:
    # `count` is part of the assertion, so the TODO keeps it — matching the runtime/coverage label
    # (`request_label` with count), not a count-less description.
    code = _gen(
        "- name: x\n  steps:\n    - assert:\n        - request: { path: /api/items, count: 3 }\n"
    )
    assert "// TODO: request assertion (/api/items count=3)" in code


def test_request_sequence_and_response_schema_emit_labeled_todos() -> None:
    seq = _gen(
        "- name: x\n  steps:\n    - assert:\n"
        "        - requestSequence: [ { method: POST, path: /a }, { method: GET, path: /b } ]\n"
    )
    assert "// TODO: requestSequence assertion (POST /a, GET /b)" in seq
    schema = _gen(
        "- name: x\n  steps:\n    - assert:\n"
        "        - responseSchema: { request: { path: /api/items }, schema: items.json }\n"
    )
    assert "// TODO: responseSchema assertion (/api/items)" in schema
    assert "unsupported assertion" not in seq and "unsupported assertion" not in schema


def test_wait_until_request_emits_labeled_todo() -> None:
    # `until: { request }` is a network wait, not a settle — a labeled TODO naming the endpoint, not
    # the generic "settle wait" comment that would misdescribe it (BE-0026).
    code = _gen(
        "- name: x\n  steps:\n"
        "    - wait: { until: { request: { method: POST, path: /api/login } }, timeout: 5 }\n"
    )
    assert "// TODO: wait until request (POST /api/login)" in code
    assert "no network interception" in code
    assert "settle wait" not in code


def test_class_name_for() -> None:
    assert class_name_for("smoke") == "SmokeUITests"
    assert class_name_for("my-flow_v2") == "MyFlowV2UITests"
    # A Swift `class` name cannot start with a digit, so a digit-leading stem is prefixed `_`
    # (BE-0255 applied the guard uniformly; XCUITest was silently unguarded before).
    assert class_name_for("2fa_flow") == "_2FaFlowUITests"


def test_manual_step_is_a_labeled_todo() -> None:
    # BE-0185: a human-takeover step has no XCUITest equivalent — a labeled TODO naming the operation,
    # never a silent skip that would fake a pass.
    code = _gen('- name: x\n  steps:\n    - manual: { label: "solve the CAPTCHA" }\n')
    assert "// TODO: manual step — solve the CAPTCHA" in code
    assert "no deterministic run-time equivalent" in code


def test_manual_step_bypass_todo_names_the_bridge() -> None:
    code = _gen(
        "- name: x\n  steps:\n"
        '    - manual: { label: "approve Face ID", bypass: "disable biometrics behind a test flag" }\n'
    )
    assert "wire a deterministic bypass: disable biometrics behind a test flag" in code


# BE-0297: `if` / `forEach` / `extract` are evaluated at run time against the live tree, so no target
# can translate them to a static test. Codegen fails loudly at generation time rather than emitting a
# silent no-op stub that would quietly drop the branch, the loop body, or the capture.
def test_if_step_fails_loudly() -> None:
    with pytest.raises(CodegenError, match="`if` control-flow step"):
        _gen(
            "- name: x\n  steps:\n"
            "    - if:\n"
            "        condition: { exists: { id: banner } }\n"
            "        then:\n"
            "          - tap: { id: banner.dismiss }\n"
        )


def test_for_each_step_fails_loudly() -> None:
    with pytest.raises(CodegenError, match="`forEach` control-flow step"):
        _gen(
            "- name: x\n  steps:\n"
            "    - forEach:\n"
            "        sel: { idMatches: 'row.*' }\n"
            "        as: row\n"
            "        steps:\n"
            "          - tap: { id: '${vars.row}' }\n"
        )


def test_extract_capture_fails_loudly() -> None:
    with pytest.raises(CodegenError, match="`extract` capture"):
        _gen(
            "- name: x\n  steps:\n"
            "    - tap: { id: total }\n"
            "      extract: { amount: { sel: { id: total }, prop: value } }\n"
        )


# BE-0297 Unit 1/2/4: the scenarios the non-gating `ui-test-coverage` codegen step compiles on-device
# (demos/showcase/Makefile). The macOS compile is the real proof, but it is expensive and off the fast
# gate, so this pins the Linux-checkable half: every coverage fixture must stay codegen-able (no
# CodegenError, no silent `unsupported`/UNSUPPORTED_SELECTOR degradation) and keep emitting the very
# XCTest primitives the compiled slice exists to exercise. A scenario edit that breaks codegen then
# fails here, in seconds, instead of only in the metered Simulator job.
_COVERAGE_DIR = Path(__file__).resolve().parents[1] / "demos" / "showcase" / "scenarios"
# (construct, source scenario, a distinguishing emitted Swift primitive)
_COVERAGE_CASES = [
    ("select/copy (Cmd+A / Cmd+C)", "text_editing.yaml", 'typeKey("a", modifierFlags: .command)'),
    ("delete key", "text_editing.yaml", "XCUIKeyboardKey.delete.rawValue"),
    ("longPress", "gestures.yaml", ".press(forDuration:"),
    ("directional swipe", "gestures.yaml", ".swipeUp()"),
    ("pinch", "gestures_multitouch.yaml", ".pinch(withScale: 2.0"),
    ("rotate", "gestures_multitouch.yaml", ".rotate(1.0"),
    ("coordinate swipe (drag)", "codegen_extra.yaml", "thenDragTo: coord("),
    ("compound traits+index", "codegen_extra.yaml", ".element(boundBy: 0)"),
]


@pytest.mark.parametrize(("construct", "scenario", "primitive"), _COVERAGE_CASES)
def test_coverage_scenarios_emit_their_construct(
    construct: str, scenario: str, primitive: str
) -> None:
    scenarios = load_scenarios((_COVERAGE_DIR / scenario).read_text(encoding="utf-8"))
    code = to_xcuitest(scenarios, class_name_for(scenario.removesuffix(".yaml")), {})
    # No construct in the compiled slice may degrade to a silent/unsupported stub — that is the exact
    # gap BE-0297 closes; a labeled device-state `// TODO` (clipboard / simctl) is a different thing.
    assert "UNSUPPORTED_SELECTOR" not in code
    assert "// TODO: unsupported" not in code
    assert primitive in code, f"{construct}: expected {primitive!r} in generated Swift"
