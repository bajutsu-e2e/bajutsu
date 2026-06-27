"""Preflight capability check (BE-0082): before any device work, a scenario that needs a capability
the chosen backend lacks fails fast and clearly, rather than partway through a run.

Pure over (scenario, capability set) — no device, no clock. Gates only the true hard requirements
(multiTouch for pinch/rotate, screenshot for visual, query/elements baseline). network and
conditionWait are deliberately NOT gated: the orchestrator polls for waits (conditionWait unused),
and idb captures network via the app-side collector despite not advertising the `network`
capability — gating either would reject scenarios that actually run.
"""

from __future__ import annotations

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
