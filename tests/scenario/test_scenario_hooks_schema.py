"""The scenario's own `hooks` block: its schema, and how the loaders carry it (BE-0501)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.config import load_config
from bajutsu.common.scenario import dump_scenarios, load_scenarios
from bajutsu.common.scenario.load_expanded import load_expanded_scenarios


def _scenario(hooks: str, extra: str = "") -> str:
    return f"- name: s\n{extra}  hooks:\n{hooks}  steps:\n    - back: {{}}\n"


def test_the_two_lists_parse_with_the_target_entry_fields() -> None:
    (s,) = load_scenarios(
        _scenario(
            "    beforeScenario:\n"
            "      - {name: card, cmd: ./seed.sh, cwd: sub, env: {A: b}, timeout: 30, mask: true}\n"
            "      - {use: prepare}\n"
            "    afterScenario:\n"
            "      - {on: failure, cmd: ./clean.sh}\n"
        )
    )
    assert s.hooks is not None
    card, prepare = s.hooks.before_scenario
    assert (card.name, card.cmd, card.cwd, card.env) == ("card", "./seed.sh", "sub", {"A": "b"})
    assert (card.timeout, card.mask, card.on) == (30, True, "always")
    assert prepare.use == "prepare"
    assert s.hooks.after_scenario[0].on == "failure"


@pytest.mark.parametrize(
    ("key", "place"),
    [
        ("beforeRun", "targets.<name>.hooks"),
        ("afterRun", "targets.<name>.hooks"),
        ("sandbox", "targets.<name>.hooks"),
        ("setup", "top-level `setup`"),
        ("cleanup", "top-level `cleanup`"),
        ("interrupts", "top-level `interrupts`"),
    ],
)
def test_a_misplaced_key_names_where_it_belongs(key: str, place: str) -> None:
    with pytest.raises(ValueError, match="cannot hold") as e:
        load_scenarios(_scenario(f"    {key}: []\n"))
    assert f"`{key}`" in str(e.value)
    assert place in str(e.value)


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ValueError, match="beforeScenrio"):
        load_scenarios(_scenario("    beforeScenrio: []\n"))


def test_a_before_entry_cannot_set_on() -> None:
    with pytest.raises(ValueError, match="cannot set `on`"):
        load_scenarios(_scenario("    beforeScenario: [{cmd: x, on: always}]\n"))


def test_two_entries_of_one_scenario_cannot_share_a_name() -> None:
    with pytest.raises(ValueError, match="two entries are named 'card'"):
        load_scenarios(
            _scenario(
                "    beforeScenario: [{name: card, cmd: x}]\n"
                "    afterScenario: [{name: card, cmd: y}]\n"
            )
        )


def test_a_scenario_without_hooks_dumps_none() -> None:
    (s,) = load_scenarios("- name: s\n  steps:\n    - back: {}\n")
    assert s.hooks is None
    assert "hooks" not in dump_scenarios([s])


def test_hooks_survive_a_dump() -> None:
    (s,) = load_scenarios(
        _scenario(
            "    beforeScenario: [{name: a, cmd: x}]\n    afterScenario: [{on: success, cmd: y}]\n"
        )
    )
    assert load_scenarios(dump_scenarios([s])) == [s]


def test_a_data_driven_scenario_keeps_its_hooks_per_row(tmp_path: Path) -> None:
    path = tmp_path / "flow.yaml"
    path.write_text(
        _scenario(
            "    beforeScenario: [{name: user, cmd: 'seed ${row.id}'}]\n"
            "    afterScenario: [{cmd: clean}]\n",
            extra="  data: [{id: one}, {id: two}]\n",
        ),
        encoding="utf-8",
    )
    rows = load_expanded_scenarios(path)
    assert [r.hooks.before_scenario[0].cmd for r in rows if r.hooks] == ["seed one", "seed two"]
    assert [r.source_path for r in rows] == [path, path]


def test_a_relative_source_path_becomes_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("flow.yaml").write_text("- name: s\n  steps: [{back: {}}]\n", encoding="utf-8")
    (s,) = load_expanded_scenarios(Path("flow.yaml"))
    assert s.source_path is not None and s.source_path.is_absolute()


def test_a_target_cannot_take_the_reserved_scenario_level_name() -> None:
    with pytest.raises(ValueError, match="name is reserved"):
        load_config(
            "targets:\n  _scenario:\n    platform: web\n    subject: {url: 'http://127.0.0.1/'}\n"
        )
