"""File-scoped components declared inline in a scenario file (BE-0422).

A `use: { component: <ref> }` ref resolves by its own shape: a bare name against the scenario
file's own `components:` map, anything path-like as a component file exactly as BE-0030 resolves
it today. These exercise the real filesystem loaders on `tmp_path` — `load_expanded_scenarios` /
`load_scenarios_dir` for the device-free readers and `run/cli.py`'s `_expand_file` for the
deterministic `run` gate — so the two agree on every case, which is the point of the shared
`ComponentResolver`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer

from bajutsu.common.config import Effective, WebConfig
from bajutsu.common.scenario import (
    Component,
    ComponentResolver,
    Redact,
    Scenario,
    load_expanded_scenarios,
    load_scenarios_dir,
)
from bajutsu.run.cli import _expand_file


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _eff(setup: str | None = None) -> Effective:
    return Effective(
        target="web",
        platform_config=WebConfig(base_url=None),
        backend=["playwright"],
        device="",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=setup,
        capture=[],
        redact=Redact(),
    )


def _dump(scenarios: list[Scenario]) -> list[dict[str, Any]]:
    return [s.model_dump(by_alias=True, exclude_none=True) for s in scenarios]


def _tap_ids(scenario: Scenario) -> list[str | list[str] | None]:
    return [s.tap.id if s.tap is not None else None for s in scenario.steps]


INLINE = """\
components:
  search:
    params: [query]
    steps:
      - type: { text: "${params.query}", into: { id: home.search }, submit: true }
      - tap: { id: home.go }

scenarios:
  - name: search returns dogs
    steps:
      - use: { component: search, with: { query: dog } }
      - tap: { id: home.results }
"""

DUPLICATED = """\
- name: search returns dogs
  steps:
    - type: { text: dog, into: { id: home.search }, submit: true }
    - tap: { id: home.go }
    - tap: { id: home.results }
"""


def test_file_scoped_component_expands_like_hand_duplicated_steps(tmp_path: Path) -> None:
    inline = load_expanded_scenarios(_write(tmp_path / "inline" / "s.yaml", INLINE))
    duplicated = load_expanded_scenarios(_write(tmp_path / "flat" / "s.yaml", DUPLICATED))
    assert _dump(inline) == _dump(duplicated)
    assert all(s.use is None for s in inline[0].steps)


def test_file_scoped_and_file_based_components_coexist(tmp_path: Path) -> None:
    # One `component:` field, two resolutions: the bare name is local, the `.yaml` ref is a file.
    _write(tmp_path / "shared.yaml", "steps:\n  - tap: { id: from.file }\n")
    scenario = _write(
        tmp_path / "s.yaml",
        """\
components:
  local:
    steps:
      - tap: { id: from.inline }

scenarios:
  - name: s
    steps:
      - use: { component: local }
      - use: { component: shared.yaml }
""",
    )
    assert _tap_ids(load_expanded_scenarios(scenario)[0]) == ["from.inline", "from.file"]


def test_undefined_bare_name_fails_with_a_clear_error(tmp_path: Path) -> None:
    # A bare name is never a filesystem probe: it fails loudly rather than guessing a file.
    scenario = _write(
        tmp_path / "s.yaml",
        "scenarios:\n  - name: s\n    steps:\n      - use: { component: nope }\n",
    )
    with pytest.raises(ValueError, match=r"component 'nope' is not defined: no such entry"):
        load_expanded_scenarios(scenario)


def test_a_name_stays_invisible_from_a_sibling_file(tmp_path: Path) -> None:
    # The map is read per file and never merged across the suite dir, so declaring `helper` in
    # a.yaml leaves b.yaml's bare `helper` undefined. Cross-file reuse stays BE-0030's path-ref job.
    suite = tmp_path / "scenarios"
    _write(
        suite / "a.yaml",
        """\
components:
  helper:
    steps:
      - tap: { id: helped }

scenarios:
  - name: a
    steps:
      - use: { component: helper }
""",
    )
    _write(
        suite / "b.yaml",
        "scenarios:\n  - name: b\n    steps:\n      - use: { component: helper }\n",
    )
    # The message names the file that came up short, so a suite-wide load says which one.
    with pytest.raises(ValueError, match=r"component 'helper' is not defined.*b\.yaml"):
        load_scenarios_dir(suite)


def test_run_and_the_device_free_loader_expand_identically(tmp_path: Path) -> None:
    # The two loaders built separate resolvers before BE-0422; the shared one is what keeps the
    # deterministic `run` gate and every device-free reader seeing the same expanded steps.
    scenario = _write(tmp_path / "s.yaml", INLINE)
    from_run, _desc, _plans = _expand_file(scenario, _eff(), root=tmp_path)
    assert _dump(from_run) == _dump(load_expanded_scenarios(scenario, root=tmp_path))


def test_a_file_scoped_component_may_use_both_kinds(tmp_path: Path) -> None:
    # A file-scoped component's own steps expand under the *same* resolver as their caller, so it
    # still sees the file's other local names — and a path ref reaches a file as it always did,
    # including one component file referencing the next.
    _write(tmp_path / "nested.yaml", "steps:\n  - tap: { id: from.nested }\n")
    _write(
        tmp_path / "shared.yaml",
        "steps:\n  - use: { component: nested.yaml }\n  - tap: { id: from.file }\n",
    )
    scenario = _write(
        tmp_path / "s.yaml",
        """\
components:
  inner:
    params: [target]
    steps:
      - tap: { id: "${params.target}" }
  outer:
    steps:
      - use: { component: inner, with: { target: from.inner } }
      - use: { component: shared.yaml }

scenarios:
  - name: s
    steps:
      - use: { component: outer }
      - use: { component: shared.yaml }
""",
    )
    assert _tap_ids(load_expanded_scenarios(scenario)[0]) == [
        "from.inner",
        "from.nested",
        "from.file",
        "from.nested",
        "from.file",
    ]


def test_a_bare_use_inside_a_component_file_is_always_undefined(tmp_path: Path) -> None:
    # Crossing into a component file swaps to a resolver bound to no local names. The scenario
    # expands `helper` locally *first*, so this also pins that the per-ref cache is per resolver:
    # a shared one would hand the cached local `helper` to the component file.
    _write(tmp_path / "wrapper.yaml", "steps:\n  - use: { component: helper }\n")
    scenario = _write(
        tmp_path / "s.yaml",
        """\
components:
  helper:
    steps:
      - tap: { id: local.helper }

scenarios:
  - name: s
    steps:
      - use: { component: helper }
      - use: { component: wrapper.yaml }
""",
    )
    with pytest.raises(
        ValueError, match=r"component 'helper' is not defined: a component file declares no"
    ):
        load_expanded_scenarios(scenario)


def test_a_cycle_through_a_component_file_is_still_caught(tmp_path: Path) -> None:
    # The scope swap happens inside `expand`'s own recursion, so `stack` keeps spanning the whole
    # chain. Restarting it at the swap — what a fresh top-level `expand_components` call would do —
    # turns this cycle into a RecursionError, which `run` does not catch and reports as a traceback.
    # The file must declare a `components:` block, or no swap happens and the case goes untested.
    _write(tmp_path / "a.yaml", "steps:\n  - use: { component: b.yaml }\n")
    _write(tmp_path / "b.yaml", "steps:\n  - use: { component: a.yaml }\n")
    scenario = _write(
        tmp_path / "s.yaml",
        """\
components:
  unused:
    steps:
      - tap: { id: x }

scenarios:
  - name: s
    steps:
      - use: { component: a.yaml }
""",
    )
    with pytest.raises(ValueError, match=r"component cycle detected"):
        load_expanded_scenarios(scenario)


def test_a_yml_ref_resolves_as_a_file_and_a_yml_key_is_rejected(tmp_path: Path) -> None:
    # `.yml` is the other file suffix the shape test accepts; it must behave exactly like `.yaml`.
    _write(tmp_path / "shared.yml", "steps:\n  - tap: { id: from.yml }\n")
    scenario = _write(
        tmp_path / "s.yaml",
        "scenarios:\n  - name: s\n    steps:\n      - use: { component: shared.yml }\n",
    )
    assert _tap_ids(load_expanded_scenarios(scenario)[0]) == ["from.yml"]

    rejected = _write(
        tmp_path / "bad.yaml",
        "components:\n  shared.yml:\n    steps:\n      - tap: { id: x }\n\nscenarios: []\n",
    )
    with pytest.raises(ValueError, match=r"components: keys must be bare names"):
        load_expanded_scenarios(rejected)


def test_a_component_file_scope_refuses_to_carry_names(tmp_path: Path) -> None:
    # The scope swap's whole job is to hand over a resolver with no local names. Building one that
    # carries names anyway would reopen the leak silently, so the constructor refuses it.
    with pytest.raises(ValueError, match=r"component-file scope declares no"):
        ComponentResolver(
            {"helper": Component(steps=[])}, root=tmp_path, base=tmp_path, source=None
        )


def test_a_prelude_resolves_bare_names_against_its_own_components(tmp_path: Path) -> None:
    # A prelude's `use` steps are expanded at the call site, in the prelude's own scope, before
    # being spliced — so a same-named entry in the calling file's map cannot capture them.
    _write(
        tmp_path / "prelude.yaml",
        """\
components:
  greet:
    steps:
      - tap: { id: prelude.greet }

scenarios:
  - name: login
    steps:
      - use: { component: greet }
""",
    )
    scenario = _write(
        tmp_path / "s.yaml",
        """\
components:
  greet:
    steps:
      - tap: { id: caller.greet }

scenarios:
  - name: s
    preconditions: { setup: prelude.yaml }
    steps:
      - use: { component: greet }
""",
    )
    scenarios, _desc, _plans = _expand_file(scenario, _eff(), root=tmp_path)
    assert _tap_ids(scenarios[0]) == ["prelude.greet", "caller.greet"]


def test_a_path_shaped_components_key_is_rejected_at_load(tmp_path: Path) -> None:
    # `use` dispatches on the ref's shape, so such a key is not merely dead: a real file of that
    # name resolves in its place and the declared steps never run. Refuse it instead.
    scenario = _write(
        tmp_path / "s.yaml",
        """\
components:
  login.yaml:
    steps:
      - tap: { id: from.inline }

scenarios:
  - name: s
    steps:
      - use: { component: login.yaml }
""",
    )
    _write(tmp_path / "login.yaml", "steps:\n  - tap: { id: from.file }\n")
    with pytest.raises(ValueError, match=r"components: keys must be bare names"):
        load_expanded_scenarios(scenario)


def test_a_prelude_resolves_path_refs_against_its_own_directory(tmp_path: Path) -> None:
    # The same filename sits beside the prelude and beside the scenario, so only a prelude-relative
    # base picks the right one. Before BE-0422 the prelude's steps were spliced unexpanded and
    # resolved against the caller's directory instead.
    _write(tmp_path / "helper.yaml", "steps:\n  - tap: { id: beside.caller }\n")
    _write(tmp_path / "sub" / "helper.yaml", "steps:\n  - tap: { id: beside.prelude }\n")
    _write(
        tmp_path / "sub" / "prelude.yaml",
        "- name: p\n  steps:\n    - use: { component: helper.yaml }\n",
    )
    scenario = _write(
        tmp_path / "s.yaml",
        "- name: s\n  preconditions: { setup: sub/prelude.yaml }\n  steps:\n    - tap: { id: own }\n",
    )
    scenarios, _desc, _plans = _expand_file(scenario, _eff(), root=tmp_path)
    assert _tap_ids(scenarios[0]) == ["beside.prelude", "own"]


def test_run_reports_a_malformed_setup_prelude_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A prelude is loaded through the same normalization as a component file, so a YAML syntax
    # error there exits 2 naming the file instead of escaping `run` as a PyYAML traceback (BE-0150).
    _write(tmp_path / "prelude.yaml", "- name: p\n  steps:\n    - tap: { id: x\n")
    scenario = _write(
        tmp_path / "s.yaml",
        "- name: s\n  preconditions: { setup: prelude.yaml }\n  steps:\n    - tap: { id: y }\n",
    )
    with pytest.raises(typer.Exit):
        _expand_file(scenario, _eff(), root=tmp_path)
    assert "invalid YAML in" in capsys.readouterr().out


def test_run_reports_a_malformed_component_file_the_same_way(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # BE-0150's normalized message reached only the device-free loader while `run` built its own
    # resolver; sharing one gives `run` the same clean error instead of a raw YAMLError traceback.
    _write(tmp_path / "broken.yaml", "steps:\n  - tap: { id: x\n")
    scenario = _write(
        tmp_path / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: broken.yaml }\n",
    )
    with pytest.raises(ValueError, match=r"invalid YAML in .*broken\.yaml"):
        load_expanded_scenarios(scenario, root=tmp_path)
    with pytest.raises(typer.Exit):
        _expand_file(scenario, _eff(), root=tmp_path)
    assert "invalid YAML in" in capsys.readouterr().out
