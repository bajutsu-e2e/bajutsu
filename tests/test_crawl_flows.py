"""Tests for candidate flow scenario emission from a crawl (bajutsu/crawl/flows.py, BE-0038).

Each discovered screen carries the replayable path that reached it; turning those paths into draft
scenarios is a pure, deterministic, model-free function of the `ScreenMap` — no device, no LLM,
never a verdict. The drafts are proposals a user can promote into a real Tier-2 test.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.crawl import Action, ScreenMap, screenmap_dict, screenmap_from_dict
from bajutsu.crawl.flows import write_flows
from bajutsu.evidence.redaction import PLACEHOLDER
from bajutsu.evidence.sink import RunArtifactWriter
from bajutsu.scenario.load import load_scenarios


def _map(paths: dict[str, tuple[Action, ...]]) -> ScreenMap:
    return ScreenMap(paths=paths)


def test_writes_one_scenario_per_reachable_screen(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    sm = _map(
        {
            "aaa": (Action(kind="tap", target="login"),),
            "bbb": (Action(kind="tap", target="login"), Action(kind="tap", target="settings")),
        }
    )
    written = write_flows(run_sink, sm)
    assert len(written) == 2
    for name in written:
        path = tmp_path / name
        assert path.exists()
        assert len(load_scenarios(path.read_text(encoding="utf-8"))) == 1


def test_entry_screen_with_empty_path_is_skipped(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    # The entry screen is reached with no actions, so there is no flow to author for it.
    sm = _map({"entry": (), "aaa": (Action(kind="tap", target="go"),)})
    written = write_flows(run_sink, sm)
    assert len(written) == 1


def test_unreplayable_path_is_skipped(tmp_path: Path, run_sink: RunArtifactWriter) -> None:
    # A path that taps a normalized coordinate can't be faithfully replayed, so it emits nothing.
    sm = _map(
        {
            "aaa": (Action(kind="tap", target="ok"),),
            "bbb": (Action(kind="tap_point", point=(0.5, 0.9)),),
        }
    )
    written = write_flows(run_sink, sm)
    assert len(written) == 1
    reloaded = load_scenarios((tmp_path / written[0]).read_text(encoding="utf-8"))
    assert reloaded[0].steps[0].tap is not None and reloaded[0].steps[0].tap.id == "ok"


def test_numbering_is_sequential_over_written_flows(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    # An interleaved unreplayable path must not leave a gap in the flow file numbers.
    sm = _map(
        {
            "aaa": (Action(kind="tap", target="a"),),
            "bad": (Action(kind="tap_point", point=(0.1, 0.2)),),
            "ccc": (Action(kind="tap", target="c"),),
        }
    )
    written = write_flows(run_sink, sm)
    assert sorted(written) == ["flows/flow-001.yaml", "flows/flow-002.yaml"]


def test_shorter_flows_are_numbered_first(tmp_path: Path, run_sink: RunArtifactWriter) -> None:
    sm = _map(
        {
            "long": (Action(kind="tap", target="a"), Action(kind="tap", target="b")),
            "short": (Action(kind="tap", target="c"),),
        }
    )
    write_flows(run_sink, sm)
    first = load_scenarios((tmp_path / "flows" / "flow-001.yaml").read_text(encoding="utf-8"))
    assert len(first[0].steps) == 1  # the shorter flow


def test_fill_and_type_flow_round_trips(tmp_path: Path, run_sink: RunArtifactWriter) -> None:
    sm = _map(
        {
            "form": (
                Action(kind="fill", fields=(("email", "a@b.com"), ("password", "hunter2"))),
                Action(kind="tap", target="submit"),
            )
        }
    )
    written = write_flows(run_sink, sm)
    assert len(written) == 1
    reloaded = load_scenarios((tmp_path / written[0]).read_text(encoding="utf-8"))
    steps = reloaded[0].steps
    assert len(steps) == 3
    assert steps[0].type is not None and steps[0].type.text == "a@b.com"
    assert steps[2].tap is not None and steps[2].tap.id == "submit"


def test_flow_records_the_dummy_where_a_default_masks_the_typed_value(
    tmp_path: Path, run_sink: RunArtifactWriter
) -> None:
    # BE-0331: a crawl carries no `redact:`, and an AI-invented password matches no backstop shape,
    # so only the two element defaults can reach it. The login path is a `path_to` prefix for every
    # screen behind it, so an unredacted flow would hold in plaintext what `screenmap.json` masks
    # in the same run directory. The step keeps a runnable dummy, never the placeholder.
    sm = _map(
        {
            "home": (
                Action(kind="type", target="auth.email", value="ai@example.com"),
                Action(kind="type", target="auth.password", value="MyR3alistic!Pass", secure=True),
                Action(kind="fill", fields=(("settings.apikey", "invented-by-the-guide"),)),
                Action(kind="tap", target="submit"),
            )
        }
    )
    written = write_flows(run_sink, sm)
    text = (tmp_path / written[0]).read_text(encoding="utf-8")
    assert "MyR3alistic!Pass" not in text and "invented-by-the-guide" not in text
    assert PLACEHOLDER not in text  # masked, but still a scenario `run` can replay
    steps = load_scenarios(text)[0].steps
    assert [s.type and s.type.text for s in steps[:3]] == [
        "ai@example.com",  # no default names this field, so what the crawl typed is kept
        "Test1234!",  # the platform marked it a masked input
        "test",  # `apikey` names a credential
    ]


def test_no_paths_writes_nothing(tmp_path: Path, run_sink: RunArtifactWriter) -> None:
    assert write_flows(run_sink, ScreenMap()) == []
    assert not (tmp_path / "flows").exists()  # no directory when there is nothing to write


def test_screenmap_json_round_trips_paths() -> None:
    sm = _map(
        {
            "entry": (),
            "aaa": (Action(kind="tap", target="login"), Action(kind="type", target="q", value="x")),
        }
    )
    restored = screenmap_from_dict(screenmap_dict(sm))
    assert restored.paths["entry"] == ()
    actions = restored.paths["aaa"]
    assert [a.kind for a in actions] == ["tap", "type"]
    assert actions[0].target == "login"
    assert actions[1].value == "x"


def test_old_screenmap_json_without_paths_loads_safely() -> None:
    restored = screenmap_from_dict({"nodes": []})
    assert restored.paths == {}
