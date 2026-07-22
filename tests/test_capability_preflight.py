"""Preflight capability check (BE-0082): before any device work, a scenario that needs a capability
the chosen backend lacks fails fast and clearly, rather than partway through a run.

Pure over (scenario, capability set) — no device, no clock. Gates only the true hard requirements
(multiTouch for pinch/rotate, screenshot for visual, query/elements baseline). network and
conditionWait are deliberately NOT gated: the orchestrator polls for waits (conditionWait unused),
and the device backends capture network via the app-side collector despite not advertising the
`network` capability — gating either would reject scenarios that actually run.

The capability sets below are synthetic fixtures, named for the shape of backend they model, not for
any one actuator: `_LEAN_IOS` is a lean simctl-backed iOS backend (device control + iOS permissions,
but no multiTouch / semanticTap / network).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu import capability_preflight
from bajutsu.drivers import base
from bajutsu.scenario import Scenario

_LEAN_IOS = (
    {
        base.Capability.QUERY,
        base.Capability.ELEMENTS,
        base.Capability.SCREENSHOT,
    }
    | base.DEVICE_CONTROL_ALL
    # A simctl-backed iOS backend honors every permission service but `notifications` (BE-0276) — the
    # real asymmetry, so this fixture doubles as the "backend missing one service" case below.
    | base.IOS_PERMISSION_CAPABILITIES
)
_FULL = _LEAN_IOS | {
    base.Capability.SEMANTIC_TAP,
    base.Capability.CONDITION_WAIT,
    base.Capability.MULTI_TOUCH,
    base.Capability.NETWORK,
}
# `_LEAN_IOS` minus the whole device-control family (including permissions) — a backend that advertises no
# device-control token (e.g. Playwright, or a future backend without a real DeviceControl wired),
# used to prove device-control steps are gated.
_NO_CONTROL = _LEAN_IOS - base.DEVICE_CONTROL_ALL - base.IOS_PERMISSION_CAPABILITIES
# A backend that supports only part of the family (the Android emulator: setLocation + clipboard,
# plus the whole permission vocabulary, BE-0210/BE-0276) — used to prove preflight admits the
# supported subset and fails fast for the rest.
_SUBSET = {
    base.Capability.QUERY,
    base.Capability.ELEMENTS,
    base.Capability.SCREENSHOT,
    base.Capability.DC_SET_LOCATION,
    base.Capability.DC_CLIPBOARD,
} | base.ANDROID_PERMISSION_CAPABILITIES


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", **body})


def test_plain_scenario_is_runnable_on_lean_ios() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}], expect=[{"exists": {"id": "ok"}}])
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []


def test_pinch_requires_multitouch() -> None:
    sc = _sc(steps=[{"pinch": {"sel": {"id": "map"}, "scale": 2.0}}])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert reasons and any("multiTouch" in r for r in reasons)
    assert capability_preflight.unsupported(sc, _FULL) == []


def test_rotate_requires_multitouch() -> None:
    sc = _sc(steps=[{"rotate": {"sel": {"id": "dial"}, "radians": 1.57}}])
    assert any("multiTouch" in r for r in capability_preflight.unsupported(sc, _LEAN_IOS))


def test_visual_assertion_requires_screenshot() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}], expect=[{"visual": {"baseline": "home"}}])
    # a screenshot-capable backend is fine there; a backend without it is rejected.
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []
    no_shot = _LEAN_IOS - {base.Capability.SCREENSHOT}
    assert any("screenshot" in r for r in capability_preflight.unsupported(sc, no_shot))


def test_network_assertions_are_not_gated() -> None:
    # the lean iOS backend lacks the `network` capability but captures traffic via the app-side collector, so a
    # request/event/requestSequence/responseSchema assertion still runs — must not be rejected.
    sc = _sc(
        steps=[{"tap": {"id": "go"}}],
        expect=[
            {"request": {"path": "/api/items", "status": 200}},
            {"event": {"path": "/track", "body": {"name": "tap"}}},
        ],
    )
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []


def test_request_wait_is_not_gated() -> None:
    sc = _sc(steps=[{"wait": {"until": {"request": {"path": "/api/x"}}, "timeout": 5}}])
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []


def test_adb_does_not_advertise_network_yet_request_runs() -> None:
    # BE-0283: adb captures via the app-side collector (BajutsuNet + `adb reverse`), so it must NOT
    # advertise the `network` token — that means *native* driver observation — yet a `request`
    # assertion must still preflight-clean on the real adb capability set.
    from bajutsu.drivers.adb import AdbDriver

    assert base.Capability.NETWORK not in AdbDriver.CAPABILITIES
    sc = _sc(
        steps=[{"tap": {"id": "go"}}], expect=[{"request": {"path": "/horses", "status": 200}}]
    )
    assert capability_preflight.unsupported(sc, set(AdbDriver.CAPABILITIES)) == []


def test_condition_wait_is_not_gated() -> None:
    # Waits are polled by the orchestrator, so conditionWait is unused — never gate on it.
    sc = _sc(steps=[{"wait": {"until": "screenChanged", "timeout": 5}}])
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []


def test_baseline_query_is_required() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}])
    assert any(
        "query" in r for r in capability_preflight.unsupported(sc, {base.Capability.ELEMENTS})
    )


def test_pinch_nested_in_for_each_is_detected() -> None:
    # `if` / `forEach` are runtime control flow (not expanded away like `use`), so the preflight
    # must recurse into their nested steps — else a pinch there slips past and fails late.
    sc = _sc(
        steps=[
            {
                "forEach": {
                    "sel": {"idMatches": "row.*"},
                    "as": "row",
                    "steps": [{"pinch": {"sel": {"id": "map"}, "scale": 2.0}}],
                }
            }
        ]
    )
    assert any("multiTouch" in r for r in capability_preflight.unsupported(sc, _LEAN_IOS))


def test_visual_nested_in_if_branch_is_detected() -> None:
    sc = _sc(
        steps=[
            {
                "if": {
                    "condition": {"exists": {"id": "banner"}},
                    "then": [{"assert": [{"visual": {"baseline": "banner"}}]}],
                }
            }
        ]
    )
    no_shot = _LEAN_IOS - {base.Capability.SCREENSHOT}
    assert any("screenshot" in r for r in capability_preflight.unsupported(sc, no_shot))


def test_aggregates_every_unsupported_construct() -> None:
    # pinch (multiTouch) + visual (screenshot), on a backend with neither → both reported at once.
    sc = _sc(
        steps=[{"pinch": {"sel": {"id": "map"}, "scale": 2.0}}],
        expect=[{"visual": {"baseline": "home"}}],
    )
    reasons = capability_preflight.unsupported(
        sc, {base.Capability.QUERY, base.Capability.ELEMENTS}
    )
    assert any("multiTouch" in r for r in reasons)
    assert any("screenshot" in r for r in reasons)


# --- Device-control steps require their per-operation token (BE-0212, split from BE-0128) ---

# One representative step per DeviceControl operation, paired with the per-operation capability
# token it needs (BE-0212 split the coarse `deviceControl` family into these). `relaunch` is
# excluded: it is gated by the injected RelaunchFn, not DeviceControl.
_DEVICE_CONTROL_STEPS = (
    ({"setLocation": {"lat": 35.0, "lon": 139.0}}, base.Capability.DC_SET_LOCATION),
    ({"push": {"payload": {"aps": {"alert": "hi"}}}}, base.Capability.DC_PUSH),
    ({"clearKeychain": {}}, base.Capability.DC_CLEAR_KEYCHAIN),
    ({"clearClipboard": {}}, base.Capability.DC_CLIPBOARD),
    ({"setClipboard": {"text": "x"}}, base.Capability.DC_CLIPBOARD),
    ({"background": {}}, base.Capability.DC_APP_LIFECYCLE),
    ({"foreground": {}}, base.Capability.DC_APP_LIFECYCLE),
    ({"overrideStatusBar": {"time": "9:41"}}, base.Capability.DC_STATUS_BAR),
    ({"clearStatusBar": {}}, base.Capability.DC_STATUS_BAR),
)


@pytest.mark.parametrize(("step", "token"), _DEVICE_CONTROL_STEPS)
def test_device_control_step_requires_its_token(step: dict[str, object], token: str) -> None:
    # Each device-control step needs its own operation token; a backend missing exactly that token
    # is rejected up front (naming it), and the full-family backend runs it.
    sc = _sc(steps=[step])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS - {token})
    assert reasons and any(token in r for r in reasons)
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []


def test_device_control_reason_includes_step_index() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}, {"push": {"payload": {"aps": {"alert": "hi"}}}}])
    reasons = capability_preflight.unsupported(sc, _NO_CONTROL)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 2: ")
    assert base.Capability.DC_PUSH in reasons[0]


def test_device_control_nested_in_for_each_is_detected() -> None:
    sc = _sc(
        steps=[
            {
                "forEach": {
                    "sel": {"idMatches": "row.*"},
                    "as": "row",
                    "steps": [{"setLocation": {"lat": 1.0, "lon": 2.0}}],
                }
            }
        ]
    )
    reasons = capability_preflight.unsupported(sc, _NO_CONTROL)
    assert len(reasons) == 1
    assert "step 1 > forEach[0]" in reasons[0]
    assert base.Capability.DC_SET_LOCATION in reasons[0]


def test_multiple_device_control_steps_yield_multiple_reasons() -> None:
    sc = _sc(
        steps=[
            {"push": {"payload": {"aps": {"alert": "a"}}}},
            {"tap": {"id": "ok"}},
            {"clearKeychain": {}},
        ]
    )
    reasons = capability_preflight.unsupported(sc, _NO_CONTROL)
    paths = [r.split(":")[0] for r in reasons]
    assert "step 1" in paths
    assert "step 3" in paths


def test_relaunch_is_not_gated_by_device_control() -> None:
    # `relaunch` runs through the injected RelaunchFn, not DeviceControl, so a backend lacking the
    # device-control family must not reject it.
    sc = _sc(steps=[{"relaunch": {}}])
    assert capability_preflight.unsupported(sc, _NO_CONTROL) == []


# --- A backend that supports only a subset of the family (Android emulator, BE-0212) ---


@pytest.mark.parametrize(
    "step",
    [
        {"setLocation": {"lat": 1.0, "lon": 2.0}},
        {"setClipboard": {"text": "x"}},
        {"clearClipboard": {}},
    ],
)
def test_subset_backend_admits_its_supported_operations(step: dict[str, object]) -> None:
    # The Android subset advertises setLocation + clipboard, so preflight lets those steps through.
    assert capability_preflight.unsupported(_sc(steps=[step]), _SUBSET) == []


@pytest.mark.parametrize(
    ("step", "token"),
    [
        ({"push": {"payload": {"aps": {"alert": "hi"}}}}, base.Capability.DC_PUSH),
        ({"clearKeychain": {}}, base.Capability.DC_CLEAR_KEYCHAIN),
        ({"background": {}}, base.Capability.DC_APP_LIFECYCLE),
        ({"foreground": {}}, base.Capability.DC_APP_LIFECYCLE),
        ({"overrideStatusBar": {"time": "9:41"}}, base.Capability.DC_STATUS_BAR),
        ({"clearStatusBar": {}}, base.Capability.DC_STATUS_BAR),
    ],
)
def test_subset_backend_rejects_its_unsupported_operations(
    step: dict[str, object], token: str
) -> None:
    # Operations the emulator can't honor are failed fast, each named by its own token.
    reasons = capability_preflight.unsupported(_sc(steps=[step]), _SUBSET)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 1: ")
    assert token in reasons[0]


def test_subset_backend_admits_supported_and_names_only_unsupported() -> None:
    # setLocation (supported) + push (unsupported) + clipboard (supported): only push is rejected,
    # named individually — the per-operation split keeps fail-fast precise for a partial backend.
    sc = _sc(
        steps=[
            {"setLocation": {"lat": 1.0, "lon": 2.0}},
            {"push": {"payload": {"aps": {"alert": "hi"}}}},
            {"setClipboard": {"text": "x"}},
        ]
    )
    reasons = capability_preflight.unsupported(sc, _SUBSET)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 2: ")
    assert base.Capability.DC_PUSH in reasons[0]


# --- permissions gated per service, not per field (BE-0276) ---


def test_permissions_supported_on_every_service_passes() -> None:
    # Android supports the whole vocabulary, including notifications.
    sc = _sc(permissions={"camera": "grant", "notifications": "revoke"}, steps=[])
    assert capability_preflight.unsupported(sc, _SUBSET) == []


def test_permissions_names_each_unsupported_service_individually() -> None:
    # a simctl-backed iOS backend has no TCC service for notifications; a scenario naming it alongside a supported service
    # is rejected for notifications only, named by its own per-service token.
    sc = _sc(permissions={"camera": "grant", "notifications": "grant"}, steps=[])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert "permissions.notifications" in reasons[0]
    assert base.permission_capability("notifications") in reasons[0]
    assert "camera" not in reasons[0]


def test_permissions_whole_mechanism_unsupported_names_every_service() -> None:
    # A backend with no permission capability at all (web, fake) names every requested service.
    sc = _sc(permissions={"camera": "grant", "location": "revoke"}, steps=[])
    reasons = capability_preflight.unsupported(sc, _NO_CONTROL)
    assert len(reasons) == 2
    assert any("permissions.camera" in r for r in reasons)
    assert any("permissions.location" in r for r in reasons)


def test_permissions_empty_field_never_gated() -> None:
    # No permissions entry, so nothing to check even on a backend with zero permission support.
    sc = _sc(steps=[])
    assert capability_preflight.unsupported(sc, _NO_CONTROL) == []


# --- Path hints in reason strings (BE-0024) ---


def test_pinch_reason_includes_step_index() -> None:
    # A pinch at step 3 (1-indexed) must surface the path in the reason.
    sc = _sc(
        steps=[
            {"tap": {"id": "ok"}},
            {"tap": {"id": "next"}},
            {"pinch": {"sel": {"id": "map"}, "scale": 2.0}},
        ]
    )
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 3: ")
    assert "multiTouch" in reasons[0]


def test_rotate_reason_includes_step_index() -> None:
    sc = _sc(steps=[{"rotate": {"sel": {"id": "dial"}, "radians": 1.57}}])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 1: ")


def test_visual_in_expect_reason_includes_expect_path() -> None:
    sc = _sc(
        steps=[{"tap": {"id": "ok"}}],
        expect=[{"exists": {"id": "ok"}}, {"visual": {"baseline": "home"}}],
    )
    no_shot = _LEAN_IOS - {base.Capability.SCREENSHOT}
    reasons = capability_preflight.unsupported(sc, no_shot)
    assert len(reasons) == 1
    assert "expect[1]" in reasons[0]
    assert "screenshot" in reasons[0]


def test_visual_in_step_assert_reason_includes_path() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}, {"assert": [{"visual": {"baseline": "home"}}]}])
    no_shot = _LEAN_IOS - {base.Capability.SCREENSHOT}
    reasons = capability_preflight.unsupported(sc, no_shot)
    assert len(reasons) == 1
    assert "step 2" in reasons[0]
    assert "screenshot" in reasons[0]


def test_pinch_nested_in_if_then_path() -> None:
    sc = _sc(
        steps=[
            {
                "if": {
                    "condition": {"exists": {"id": "banner"}},
                    "then": [{"pinch": {"sel": {"id": "map"}, "scale": 2.0}}],
                }
            }
        ]
    )
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert "step 1 > if > then[0]" in reasons[0]


def test_pinch_nested_in_if_else_path() -> None:
    sc = _sc(
        steps=[
            {
                "if": {
                    "condition": {"exists": {"id": "banner"}},
                    "then": [{"tap": {"id": "ok"}}],
                    "else": [{"pinch": {"sel": {"id": "map"}, "scale": 2.0}}],
                }
            }
        ]
    )
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert "step 1 > if > else[0]" in reasons[0]


def test_pinch_nested_in_for_each_path() -> None:
    sc = _sc(
        steps=[
            {
                "forEach": {
                    "sel": {"idMatches": "row.*"},
                    "as": "row",
                    "steps": [
                        {"tap": {"id": "ok"}},
                        {"pinch": {"sel": {"id": "map"}, "scale": 2.0}},
                    ],
                }
            }
        ]
    )
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert "step 1 > forEach[1]" in reasons[0]


def test_visual_in_if_condition_path() -> None:
    sc = _sc(
        steps=[
            {
                "if": {
                    "condition": {"visual": {"baseline": "home"}},
                    "then": [{"tap": {"id": "ok"}}],
                }
            }
        ]
    )
    no_shot = _LEAN_IOS - {base.Capability.SCREENSHOT}
    reasons = capability_preflight.unsupported(sc, no_shot)
    assert len(reasons) == 1
    assert "step 1 > if > condition" in reasons[0]


def test_multiple_pinches_yield_multiple_reasons() -> None:
    # Each occurrence generates its own reason with its own path.
    sc = _sc(
        steps=[
            {"pinch": {"sel": {"id": "map"}, "scale": 2.0}},
            {"tap": {"id": "ok"}},
            {"pinch": {"sel": {"id": "map2"}, "scale": 3.0}},
        ]
    )
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    paths = [r.split(":")[0] for r in reasons]
    assert "step 1" in paths
    assert "step 3" in paths


def test_baseline_reasons_have_no_path() -> None:
    # The baseline capability reasons (query/elements) are backend-level, not step-specific.
    sc = _sc(steps=[{"tap": {"id": "ok"}}])
    reasons = capability_preflight.unsupported(sc, {base.Capability.ELEMENTS})
    assert any("query" in r for r in reasons)
    # Baseline reasons should NOT have a step prefix.
    assert all(not r.startswith("step ") for r in reasons)


# --- selectOption capability gating (BE-0191) ---


_WEB = _LEAN_IOS | {base.Capability.SELECT_OPTION}


def test_select_option_requires_select_option_capability() -> None:
    # A selectOption step on a backend without SELECT_OPTION (e.g. adb/xcuitest) must be
    # rejected at preflight — not run every earlier step on the real device first and fail late.
    sc = _sc(steps=[{"selectOption": {"sel": {"id": "nav.theme-picker"}, "option": "midnight"}}])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert reasons and any("selectOption" in r for r in reasons)
    # A web backend advertising SELECT_OPTION runs it without issue.
    assert capability_preflight.unsupported(sc, _WEB) == []


def test_select_option_reason_includes_step_index() -> None:
    sc = _sc(
        steps=[
            {"tap": {"id": "ok"}},
            {"selectOption": {"sel": {"id": "nav.theme-picker"}, "option": "midnight"}},
        ]
    )
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 2: ")
    assert "selectOption" in reasons[0]


# --- textSelection capability gating (select / copy; BE-0280) ---


_TEXT = _LEAN_IOS | {base.Capability.TEXT_SELECTION}


@pytest.mark.parametrize(
    "step",
    [{"select": {"into": {"id": "field"}}}, {"copy": {}}],
)
def test_select_and_copy_require_text_selection(step: dict[str, object]) -> None:
    # select-all / copy actuate only on a backend that can select natively; a coordinate-only backend (
    # no TEXT_SELECTION) is rejected up front rather than left to fail late mid-run (BE-0280).
    sc = _sc(steps=[step])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert reasons and any("textSelection" in r for r in reasons)
    # A backend advertising TEXT_SELECTION runs it without issue.
    assert capability_preflight.unsupported(sc, _TEXT) == []


def test_delete_and_clear_are_not_gated_by_text_selection() -> None:
    # Every backend actuates delete_text (a run of backspaces), so delete/clear need no token —
    # which lacks TEXT_SELECTION, still runs them (BE-0280).
    sc = _sc(
        steps=[
            {"delete": {"into": {"id": "field"}, "count": 3}},
            {"clear": {"into": {"id": "field"}}},
        ]
    )
    assert capability_preflight.unsupported(sc, _LEAN_IOS) == []


def test_select_reason_includes_step_index() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}, {"select": {"into": {"id": "field"}}}])
    reasons = capability_preflight.unsupported(sc, _LEAN_IOS)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 2: ")
    assert "textSelection" in reasons[0]


# --- CLI doctor --scenario integration (BE-0024) ---


def test_doctor_scenario_check_detects_unsupported_capabilities(tmp_path: Path) -> None:
    # A selectOption scenario on xcuitest (no selectOption — a web-only capability) must surface the
    # unsupported capability. Tests the check_scenarios helper used by the CLI.
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    scn_file = tmp_path / "select.yaml"
    scn_file.write_text(
        "- name: select test\n  steps:\n    - selectOption: { sel: { id: theme }, option: dark }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn_file, capabilities_for("xcuitest"))
    assert len(reasons) == 1
    assert "selectOption" in reasons[0]
    assert "select test" in reasons[0]


def test_doctor_scenario_check_no_issue_when_supported(tmp_path: Path) -> None:
    # A plain tap scenario on xcuitest — no unsupported capabilities.
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    scn_file = tmp_path / "tap.yaml"
    scn_file.write_text(
        "- name: tap test\n  steps:\n    - tap: { id: ok }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn_file, capabilities_for("xcuitest"))
    assert reasons == []


def test_doctor_scenario_check_multiple_scenarios(tmp_path: Path) -> None:
    # Multiple scenarios: only the one with selectOption should produce a reason.
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    scn_file = tmp_path / "mixed.yaml"
    scn_file.write_text(
        "- name: ok scenario\n"
        "  steps:\n"
        "    - tap: { id: ok }\n"
        "- name: select scenario\n"
        "  steps:\n"
        "    - selectOption: { sel: { id: theme }, option: dark }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn_file, capabilities_for("xcuitest"))
    assert len(reasons) == 1
    assert "select scenario" in reasons[0]
    assert "ok scenario" not in reasons[0]


def test_doctor_scenario_check_missing_file(tmp_path: Path) -> None:
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    # A missing scenario file should raise (not silently skip).
    with pytest.raises(FileNotFoundError):
        check_scenarios(tmp_path / "missing.yaml", capabilities_for("xcuitest"))


def test_doctor_scenario_flag_rejects_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--scenario pointing at a directory must exit 2 with a clean message, not crash."""
    from typer.testing import CliRunner

    from bajutsu.cli import app

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        "  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    # The sandbox has no device tooling, so skip the actuator-availability gate to reach the scenario check.
    monkeypatch.setattr("bajutsu.cli.commands.doctor.select_actuator", lambda _: "xcuitest")
    # tmp_path itself is a directory — use it as the --scenario argument.
    scenario_dir = tmp_path / "subdir"
    scenario_dir.mkdir()
    r = CliRunner().invoke(
        app,
        ["doctor", "--target", "demo", "--config", str(cfg), "--scenario", str(scenario_dir)],
    )
    assert r.exit_code == 2
    assert "scenario not found" in r.output
