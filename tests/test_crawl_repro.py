"""Tests for deterministic crash-repro scenario emission from a crawl (bajutsu/crawl/repro.py, BE-0038).

A crash records the exact action path that collapsed the app UI. Turning that path back into a
runnable `Scenario` is a pure, deterministic, model-free function of the `ScreenMap`: no device, no
LLM, never a verdict — the emitted scenario is something `run` can replay to reproduce the crash.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.common.evidence.redaction import PLACEHOLDER
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.scenario.load import load_scenarios
from bajutsu.crawl import Action, Crash, ScreenMap, screenmap_dict, screenmap_from_dict
from bajutsu.crawl.repro import crash_scenario, write_repros


def _crash(*actions: Action) -> Crash:
    return Crash(tuple(a.describe() for a in actions), tuple(actions))


def test_tap_path_becomes_tap_steps() -> None:
    crash = _crash(Action(kind="tap", target="login"), Action(kind="tap", target="submit"))
    scenario = crash_scenario(crash, name="crash-1")
    assert scenario is not None
    assert scenario.name == "crash-1"
    assert [s.tap and s.tap.id for s in scenario.steps] == ["login", "submit"]


def test_type_action_carries_text_and_target() -> None:
    crash = _crash(Action(kind="type", target="email", value="a@b.com"))
    scenario = crash_scenario(crash, name="crash-1")
    assert scenario is not None
    step = scenario.steps[0]
    assert step.type is not None
    assert step.type.text == "a@b.com"
    assert step.type.into is not None
    assert step.type.into.id == "email"


def test_idless_element_uses_label_and_index() -> None:
    crash = _crash(Action(kind="tap", label="Buy", index=2))
    scenario = crash_scenario(crash, name="crash-1")
    assert scenario is not None
    assert scenario.steps[0].tap is not None
    assert scenario.steps[0].tap.label == "Buy"
    assert scenario.steps[0].tap.index == 2


def test_fill_expands_to_one_type_step_per_field() -> None:
    crash = _crash(Action(kind="fill", fields=(("email", "a@b.com"), ("password", "hunter2"))))
    scenario = crash_scenario(crash, name="crash-1")
    assert scenario is not None
    assert len(scenario.steps) == 2
    first, second = scenario.steps
    assert first.type is not None and first.type.into is not None
    assert (first.type.into.id, first.type.text) == ("email", "a@b.com")
    assert second.type is not None and second.type.into is not None
    # `password` names a credential, so BE-0331's default masks what the crawl typed into it. The
    # step carries the field's own deterministic dummy instead of the placeholder, so the emitted
    # repro is still runnable — the value itself never reaches the file.
    assert (second.type.into.id, second.type.text) == ("password", "test")


def test_tap_point_path_is_unsupported() -> None:
    # A normalized coordinate has no selector to address, so the path can't be faithfully replayed.
    crash = _crash(Action(kind="tap", target="menu"), Action(kind="tap_point", point=(0.5, 0.9)))
    assert crash_scenario(crash, name="crash-1") is None


def test_empty_path_yields_no_scenario() -> None:
    assert crash_scenario(Crash((), ()), name="crash-1") is None


def test_selectorless_action_is_unsupported() -> None:
    # An action with no target/label can't be addressed, so it has no faithful scenario form —
    # it returns None rather than building an invalid (empty) selector that would raise.
    crash = _crash(Action(kind="tap"))
    assert crash_scenario(crash, name="crash-1") is None


def test_write_repros_skips_selectorless_crash_without_raising(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    sm = ScreenMap(crashes=[_crash(Action(kind="tap"))])
    assert write_repros(run_sink, sm) == []


def test_emitted_yaml_round_trips_through_load() -> None:
    crash = _crash(
        Action(kind="tap", target="login"),
        Action(kind="type", target="email", value="a@b.com"),
    )
    scenario = crash_scenario(crash, name="crash-1")
    assert scenario is not None
    from bajutsu.common.scenario.serialize import dump_scenario_file

    reloaded = load_scenarios(dump_scenario_file([scenario]))
    assert len(reloaded) == 1
    assert reloaded[0].steps[0].tap is not None and reloaded[0].steps[0].tap.id == "login"
    assert reloaded[0].steps[1].type is not None and reloaded[0].steps[1].type.text == "a@b.com"


def test_write_repros_writes_one_file_per_supported_crash(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    sm = ScreenMap(
        crashes=[
            _crash(Action(kind="tap", target="a")),
            _crash(Action(kind="tap", target="b"), Action(kind="tap_point", point=(0.1, 0.2))),
            _crash(Action(kind="tap", target="c")),
        ]
    )
    written = write_repros(run_sink, sm)
    # The middle crash hits an unsupported tap_point, so only two repros land.
    assert len(written) == 2
    for name in written:
        path = tmp_path / name
        assert path.exists()
        reloaded = load_scenarios(path.read_text(encoding="utf-8"))
        assert len(reloaded) == 1


def test_write_repros_records_the_dummy_where_a_default_masks_the_typed_value(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    # BE-0331: the same default that masks the value in `screenmap.json` must reach the repro that
    # records the same action, and it emits the field's own dummy so the repro still replays.
    sm = ScreenMap(
        crashes=[
            _crash(
                Action(kind="type", target="auth.password", value="MyR3alistic!Pass", secure=True),
                Action(kind="tap", target="submit"),
            )
        ]
    )
    written = write_repros(run_sink, sm)
    text = (tmp_path / written[0]).read_text(encoding="utf-8")
    assert "MyR3alistic!Pass" not in text and PLACEHOLDER not in text
    steps = load_scenarios(text)[0].steps
    assert steps[0].type is not None and steps[0].type.text == "Test1234!"


def test_write_repros_with_no_crashes_writes_nothing(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    assert write_repros(run_sink, ScreenMap()) == []


# --- app-crash artifacts (BE-0424) -----------------------------------------------------------------


def test_write_repros_writes_app_crash_artifacts_beside_the_repro(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    crash = Crash(
        ("tap login",),
        (Action(kind="tap", target="login"),),
        (("logcat-crash.txt", b"FATAL EXCEPTION: main\n"),),
    )
    write_repros(run_sink, ScreenMap(crashes=[crash]))

    written = (tmp_path / "crashes" / "crash-001" / "app-crash" / "logcat-crash.txt").read_text(
        encoding="utf-8"
    )
    assert "FATAL EXCEPTION" in written


def test_write_repros_writes_artifacts_even_for_a_non_replayable_crash(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    # The artifact write sits before the `continue`: a non-replayable crash (a `tap_point` with no
    # selector) is exactly the one whose platform report is worth the most, since there is no repro
    # to run instead — it must not lose its artifacts along with its repro.
    crash = Crash(
        ("tap menu", "tapPoint"),
        (Action(kind="tap", target="menu"), Action(kind="tap_point", point=(0.5, 0.9))),
        (("logcat-crash.txt", b"FATAL EXCEPTION: main\n"),),
    )
    written = write_repros(run_sink, ScreenMap(crashes=[crash]))

    assert written == []  # no repro: the path cannot be faithfully replayed
    assert (tmp_path / "crashes" / "crash-001" / "app-crash" / "logcat-crash.txt").exists()


def test_write_repros_writes_no_app_crash_directory_with_no_artifacts(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    write_repros(run_sink, ScreenMap(crashes=[_crash(Action(kind="tap", target="a"))]))
    assert not (tmp_path / "crashes" / "crash-001" / "app-crash").exists()


def test_write_repros_redacts_the_artifact_content(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    # The same free-text scrub `run`'s own copy uses: a crash report is text a crashing app can echo
    # a secret into.
    secret = b"token=ghp_0123456789abcdefghijklmnopqrstuvwxyz\n"
    crash = Crash(("tap a",), (Action(kind="tap", target="a"),), (("report.txt", secret),))
    write_repros(run_sink, ScreenMap(crashes=[crash]))

    written = (tmp_path / "crashes" / "crash-001" / "app-crash" / "report.txt").read_text(
        encoding="utf-8"
    )
    assert "ghp_0123456789abcdefghijklmnopqrstuvwxyz" not in written


def test_the_artifacts_field_is_excluded_from_the_screenmap_round_trip() -> None:
    # Raw `bytes` has no JSON encoding: deliberately left out of `serialize.py` in both directions
    # rather than base64-widening every other field's dump. A carried-forward `Crash` reload always
    # gets `artifacts=()`, the dataclass default.
    from bajutsu.crawl import screenmap_dict, screenmap_from_dict

    crash = Crash(("tap a",), (Action(kind="tap", target="a"),), (("r.txt", b"x"),))
    reloaded = screenmap_from_dict(screenmap_dict(ScreenMap(crashes=[crash])))
    assert reloaded.crashes[0].artifacts == ()
    assert reloaded.crashes[0].path == ("tap a",)  # everything else still round-trips


def test_screenmap_json_round_trips_crash_actions() -> None:
    sm = ScreenMap(
        crashes=[
            _crash(
                Action(kind="tap", target="login"), Action(kind="type", target="email", value="x")
            )
        ]
    )
    restored = screenmap_from_dict(screenmap_dict(sm))
    assert len(restored.crashes) == 1
    actions = restored.crashes[0].actions
    assert [a.kind for a in actions] == ["tap", "type"]
    assert actions[0].target == "login"
    assert actions[1].value == "x"


def test_old_screenmap_json_without_actions_loads_safely() -> None:
    # A map saved before crashes carried structured actions still loads (actions default empty).
    restored = screenmap_from_dict({"crashes": [{"path": ["tap login"]}]})
    assert restored.crashes[0].path == ("tap login",)
    assert restored.crashes[0].actions == ()
