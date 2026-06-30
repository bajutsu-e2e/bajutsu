"""Preflight capability check (BE-0082): before any device work, a scenario that needs a capability
the chosen backend lacks fails fast and clearly, rather than partway through a run.

Pure over (scenario, capability set) — no device, no clock. Gates only the true hard requirements
(multiTouch for pinch/rotate, screenshot for visual, query/elements baseline). network and
conditionWait are deliberately NOT gated: the orchestrator polls for waits (conditionWait unused),
and idb captures network via the app-side collector despite not advertising the `network`
capability — gating either would reject scenarios that actually run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu import capability_preflight
from bajutsu.drivers import base
from bajutsu.scenario import Scenario

_IDB = {base.Capability.QUERY, base.Capability.ELEMENTS, base.Capability.SCREENSHOT}
_FULL = _IDB | {
    base.Capability.SEMANTIC_TAP,
    base.Capability.CONDITION_WAIT,
    base.Capability.MULTI_TOUCH,
    base.Capability.NETWORK,
}


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", **body})


def test_plain_scenario_is_runnable_on_idb() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}], expect=[{"exists": {"id": "ok"}}])
    assert capability_preflight.unsupported(sc, _IDB) == []


def test_pinch_requires_multitouch() -> None:
    sc = _sc(steps=[{"pinch": {"sel": {"id": "map"}, "scale": 2.0}}])
    reasons = capability_preflight.unsupported(sc, _IDB)
    assert reasons and any("multiTouch" in r for r in reasons)
    assert capability_preflight.unsupported(sc, _FULL) == []


def test_rotate_requires_multitouch() -> None:
    sc = _sc(steps=[{"rotate": {"sel": {"id": "dial"}, "radians": 1.57}}])
    assert any("multiTouch" in r for r in capability_preflight.unsupported(sc, _IDB))


def test_visual_assertion_requires_screenshot() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}], expect=[{"visual": {"baseline": "home"}}])
    # idb has screenshot, so it's fine there; a backend without it is rejected.
    assert capability_preflight.unsupported(sc, _IDB) == []
    no_shot = _IDB - {base.Capability.SCREENSHOT}
    assert any("screenshot" in r for r in capability_preflight.unsupported(sc, no_shot))


def test_visual_in_step_assert_is_detected() -> None:
    # An inline step assertion (a step whose sole field is `assert`) is scanned too, not just expect.
    sc = _sc(steps=[{"tap": {"id": "ok"}}, {"assert": [{"visual": {"baseline": "home"}}]}])
    no_shot = _IDB - {base.Capability.SCREENSHOT}
    assert any("screenshot" in r for r in capability_preflight.unsupported(sc, no_shot))


def test_network_assertions_are_not_gated() -> None:
    # idb lacks the `network` capability but captures traffic via the app-side collector, so a
    # request/event/requestSequence/responseSchema assertion still runs — must not be rejected.
    sc = _sc(
        steps=[{"tap": {"id": "go"}}],
        expect=[
            {"request": {"path": "/api/items", "status": 200}},
            {"event": {"path": "/track", "body": {"name": "tap"}}},
        ],
    )
    assert capability_preflight.unsupported(sc, _IDB) == []


def test_request_wait_is_not_gated() -> None:
    sc = _sc(steps=[{"wait": {"until": {"request": {"path": "/api/x"}}, "timeout": 5}}])
    assert capability_preflight.unsupported(sc, _IDB) == []


def test_condition_wait_is_not_gated() -> None:
    # Waits are polled by the orchestrator, so conditionWait is unused — never gate on it.
    sc = _sc(steps=[{"wait": {"until": "screenChanged", "timeout": 5}}])
    assert capability_preflight.unsupported(sc, _IDB) == []


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
    assert any("multiTouch" in r for r in capability_preflight.unsupported(sc, _IDB))


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
    no_shot = _IDB - {base.Capability.SCREENSHOT}
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
    reasons = capability_preflight.unsupported(sc, _IDB)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 3: ")
    assert "multiTouch" in reasons[0]


def test_rotate_reason_includes_step_index() -> None:
    sc = _sc(steps=[{"rotate": {"sel": {"id": "dial"}, "radians": 1.57}}])
    reasons = capability_preflight.unsupported(sc, _IDB)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 1: ")


def test_visual_in_expect_reason_includes_expect_path() -> None:
    sc = _sc(
        steps=[{"tap": {"id": "ok"}}],
        expect=[{"exists": {"id": "ok"}}, {"visual": {"baseline": "home"}}],
    )
    no_shot = _IDB - {base.Capability.SCREENSHOT}
    reasons = capability_preflight.unsupported(sc, no_shot)
    assert len(reasons) == 1
    assert "expect[1]" in reasons[0]
    assert "screenshot" in reasons[0]


def test_visual_in_step_assert_reason_includes_path() -> None:
    sc = _sc(steps=[{"tap": {"id": "ok"}}, {"assert": [{"visual": {"baseline": "home"}}]}])
    no_shot = _IDB - {base.Capability.SCREENSHOT}
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
    reasons = capability_preflight.unsupported(sc, _IDB)
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
    reasons = capability_preflight.unsupported(sc, _IDB)
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
    reasons = capability_preflight.unsupported(sc, _IDB)
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
    no_shot = _IDB - {base.Capability.SCREENSHOT}
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
    reasons = capability_preflight.unsupported(sc, _IDB)
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


# --- CLI doctor --scenario integration (BE-0024) ---


def test_doctor_scenario_check_detects_unsupported_capabilities(tmp_path: Path) -> None:
    # A scenario with pinch on an idb backend (no multiTouch) must surface the unsupported
    # capability. Tests the check_scenarios helper used by the CLI.
    from bajutsu.cli.commands.doctor import check_scenarios

    scn_file = tmp_path / "pinch.yaml"
    scn_file.write_text(
        "- name: pinch test\n  steps:\n    - pinch: { sel: { id: map }, scale: 2.0 }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn_file, "idb")
    assert len(reasons) == 1
    assert "multiTouch" in reasons[0]
    assert "pinch test" in reasons[0]


def test_doctor_scenario_check_no_issue_when_supported(tmp_path: Path) -> None:
    # A plain tap scenario on idb — no unsupported capabilities.
    from bajutsu.cli.commands.doctor import check_scenarios

    scn_file = tmp_path / "tap.yaml"
    scn_file.write_text(
        "- name: tap test\n  steps:\n    - tap: { id: ok }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn_file, "idb")
    assert reasons == []


def test_doctor_scenario_check_multiple_scenarios(tmp_path: Path) -> None:
    # Multiple scenarios: only the one with pinch should produce a reason.
    from bajutsu.cli.commands.doctor import check_scenarios

    scn_file = tmp_path / "mixed.yaml"
    scn_file.write_text(
        "- name: ok scenario\n"
        "  steps:\n"
        "    - tap: { id: ok }\n"
        "- name: pinch scenario\n"
        "  steps:\n"
        "    - pinch: { sel: { id: map }, scale: 2.0 }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn_file, "idb")
    assert len(reasons) == 1
    assert "pinch scenario" in reasons[0]
    assert "ok scenario" not in reasons[0]


def test_doctor_scenario_check_missing_file(tmp_path: Path) -> None:
    from bajutsu.cli.commands.doctor import check_scenarios

    # A missing scenario file should raise (not silently skip).
    with pytest.raises(FileNotFoundError):
        check_scenarios(tmp_path / "missing.yaml", "idb")
