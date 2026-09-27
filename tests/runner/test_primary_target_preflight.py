"""Tests for the per-target capability preflight's narrowing under `primaryTarget` (BE-0436).

`_steps_for_target` hands each declared target only the steps and `expect` entries routed to it, so
BE-0082's preflight judges each backend against its own constructs alone. A step or `expect` entry
that omits `target` under a declared primary must land in the primary's narrowed scenario alone,
never in every target's, which is what reading `None` as "routed everywhere" would do.
"""

from __future__ import annotations

from bajutsu.common.runner.pipeline import _steps_for_target
from bajutsu.common.scenario import Scenario


def _tap_ids(s: Scenario) -> list[object]:
    return [st.tap.id for st in s.steps if st.tap is not None and st.tap.id is not None]


def _scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "primaryTarget": "app",
            "before": [{"tap": {"id": "before.app"}}],
            "steps": [
                {"tap": {"id": "app.1"}},
                {"target": "web", "tap": {"id": "web.1"}},
                {"target": "app", "tap": {"id": "app.2"}},
            ],
            "after": [{"on": "always", "steps": [{"tap": {"id": "after.app"}}]}],
            "expect": [
                {"exists": {"id": "on.app"}},
                {"target": "web", "exists": {"id": "on.web"}},
            ],
        }
    )


def test_a_primary_resolved_step_is_narrowed_into_the_primary_alone() -> None:
    s = _scenario()
    app, web = _steps_for_target(s, "app"), _steps_for_target(s, "web")
    assert _tap_ids(app) == ["app.1", "app.2"]
    assert _tap_ids(web) == ["web.1"]
    assert len(app.before) == 1 and web.before == []
    assert len(app.after[0].steps) == 1 and web.after[0].steps == []


def test_an_omitted_expect_entry_is_narrowed_into_the_primary_alone() -> None:
    s = _scenario()
    omitted, on_web = s.expect
    assert _steps_for_target(s, "app").expect == [omitted]
    assert _steps_for_target(s, "web").expect == [on_web]


def test_one_declared_target_keeps_every_omitted_entry() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app"],
            "primaryTarget": "app",
            "steps": [{"tap": {"id": "a"}}],
            "expect": [{"exists": {"id": "a"}}],
        }
    )
    narrowed = _steps_for_target(s, "app")
    assert _tap_ids(narrowed) == ["a"]
    assert len(narrowed.expect) == 1


def test_known_limitation_a_nested_step_follows_its_wrapper_into_the_preflight() -> None:
    # Known limitation, predating BE-0436 (BE-0428 never closed it either): `_steps_for_target`
    # narrows top-level entries only, so a nested step travels with its wrapper. Here the nested
    # step resolves to "app" but sits inside an `if` routed to "web", so it is judged against the
    # web backend's capabilities, not the app backend it actually runs on. Pinned so the gap stays
    # visible; whichever item makes the narrowing recursive should flip these assertions.
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "primaryTarget": "app",
            "steps": [
                {
                    "target": "web",
                    "if": {
                        "condition": {"exists": {"id": "x"}},
                        "then": [{"tap": {"id": "nested.app"}}],
                    },
                },
            ],
        }
    )
    web = _steps_for_target(s, "web")
    app = _steps_for_target(s, "app")
    assert len(web.steps) == 1
    nested = web.steps[0].if_
    assert nested is not None
    assert nested.then[0].resolved_target == "app"
    assert app.steps == []
