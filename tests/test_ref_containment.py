"""Path-containment for a scenario's component / data refs (BE-0174).

A scenario must never make the loader read a file outside its suite root. These exercise the real
filesystem loaders (`load_expanded_scenarios` / `load_scenarios_dir`) with actual files on
`tmp_path` — no mocks — and the CLI `run` path's `_expand_file`, so every choke point is covered.
The distinctive marker ``TOPSECRET`` lets a leak test assert the rejection never echoes the target
file's contents, only the offending ref.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.scenario import load_expanded_scenarios, load_scenarios_dir


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_in_root_relative_component_still_loads(tmp_path: Path) -> None:
    # A shared-component layout: scenarios/ and a sibling components/ under one suite root. The
    # `../components` ref climbs out of scenarios/ but stays inside the root, so it must resolve.
    root = tmp_path / "suite"
    _write(root / "components" / "login.yaml", "steps:\n  - tap: { id: login }\n")
    scenario = _write(
        root / "scenarios" / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: ../components/login.yaml }\n",
    )
    scns = load_expanded_scenarios(scenario, root=root)
    assert scns[0].steps[0].tap.id == "login"  # type: ignore[union-attr]


def test_in_root_data_file_still_loads(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    _write(root / "data" / "cases.csv", "target\nbtn.a\nbtn.b\n")
    scenario = _write(
        root / "scenarios" / "s.yaml",
        '- name: s\n  dataFile: ../data/cases.csv\n  steps:\n    - tap: { id: "${row.target}" }\n',
    )
    out = load_expanded_scenarios(scenario, root=root)
    assert [s.steps[0].tap.id for s in out] == ["btn.a", "btn.b"]  # type: ignore[union-attr]


def test_in_root_seed_photos_still_loads(tmp_path: Path) -> None:
    # `preconditions.seedPhotos` resolves the same way `dataFile` does: file-relative, contained by
    # the suite root — not the base a path joins against (roadmap item).
    root = tmp_path / "suite"
    _write(root / "fixtures" / "red.png", "not a real png, path resolution only")
    scenario = _write(
        root / "scenarios" / "s.yaml",
        "- name: s\n  preconditions: { erase: true, seedPhotos: [../fixtures/red.png] }\n"
        "  steps:\n    - tap: { id: a }\n",
    )
    out = load_expanded_scenarios(scenario, root=root)
    resolved = out[0].preconditions.seed_photos
    assert resolved == [str((root / "fixtures" / "red.png").resolve())]


def test_parent_chain_seed_photos_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "secret.png", "TOPSECRET")
    scenario = _write(
        tmp_path / "suite" / "s.yaml",
        "- name: s\n  preconditions: { erase: true, seedPhotos: [../secret.png] }\n"
        "  steps:\n    - tap: { id: a }\n",
    )
    with pytest.raises(ValueError, match="outside the suite root") as ei:
        load_expanded_scenarios(scenario)
    assert "TOPSECRET" not in str(ei.value)


def test_load_expanded_scenarios_sets_source_stem_from_the_file_name(tmp_path: Path) -> None:
    # BE-0417: every expanded scenario carries the loaded file's own stem, independent of its name:.
    scenario = _write(
        tmp_path / "login_flow.yaml",
        "- name: Login succeeds with a valid password\n  steps:\n    - tap: { id: ok }\n",
    )
    out = load_expanded_scenarios(scenario)
    assert [s.source_stem for s in out] == ["login_flow"]


def test_load_expanded_scenarios_data_driven_rows_all_share_the_source_stem(tmp_path: Path) -> None:
    # Every row a data-driven scenario expands into is set *after* expand_data, so it carries the
    # same stem as the file it came from, not just the pre-expansion source scenario.
    root = tmp_path / "suite"
    _write(root / "data" / "cases.csv", "target\nbtn.a\nbtn.b\n")
    scenario = _write(
        root / "scenarios" / "login_flow.yaml",
        '- name: s\n  dataFile: ../data/cases.csv\n  steps:\n    - tap: { id: "${row.target}" }\n',
    )
    out = load_expanded_scenarios(scenario, root=root)
    assert [s.source_stem for s in out] == ["login_flow", "login_flow"]


def test_absolute_component_ref_rejected(tmp_path: Path) -> None:
    secret = _write(tmp_path / "outside" / "secret.yaml", "steps:\n  - tap: { id: TOPSECRET }\n")
    scenario = _write(
        tmp_path / "suite" / "s.yaml",
        f"- name: s\n  steps:\n    - use: {{ component: {secret} }}\n",
    )
    with pytest.raises(ValueError, match="outside the suite root") as ei:
        load_expanded_scenarios(scenario)
    assert "TOPSECRET" not in str(ei.value)


def test_parent_chain_component_ref_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "secret.yaml", "steps:\n  - tap: { id: TOPSECRET }\n")
    scenario = _write(
        tmp_path / "suite" / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: ../secret.yaml }\n",
    )
    with pytest.raises(ValueError, match="outside the suite root") as ei:
        load_expanded_scenarios(scenario)
    assert "TOPSECRET" not in str(ei.value)


def test_symlink_component_escape_rejected(tmp_path: Path) -> None:
    secret = _write(tmp_path / "outside" / "secret.yaml", "steps:\n  - tap: { id: TOPSECRET }\n")
    root = tmp_path / "suite"
    root.mkdir(parents=True, exist_ok=True)
    (root / "link.yaml").symlink_to(secret)
    scenario = _write(
        root / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: link.yaml }\n",
    )
    with pytest.raises(ValueError, match="outside the suite root") as ei:
        load_expanded_scenarios(scenario)
    assert "TOPSECRET" not in str(ei.value)


def test_parent_chain_data_file_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "secret.csv", "col\nTOPSECRET\n")
    scenario = _write(
        tmp_path / "suite" / "s.yaml",
        '- name: s\n  dataFile: ../secret.csv\n  steps:\n    - tap: { id: "${row.col}" }\n',
    )
    with pytest.raises(ValueError, match="outside the suite root") as ei:
        load_expanded_scenarios(scenario)
    assert "TOPSECRET" not in str(ei.value)


def test_load_scenarios_dir_rejects_escape(tmp_path: Path) -> None:
    # `load_scenarios_dir` passes the scenarios dir as the root, so a top-level scenario cannot
    # climb out of it via `..`.
    _write(tmp_path / "secret.yaml", "steps:\n  - tap: { id: TOPSECRET }\n")
    scenarios_dir = tmp_path / "scenarios"
    _write(
        scenarios_dir / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: ../secret.yaml }\n",
    )
    with pytest.raises(ValueError, match="outside the suite root") as ei:
        load_scenarios_dir(scenarios_dir)
    assert "TOPSECRET" not in str(ei.value)


def test_run_expand_file_rejects_escape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The CLI `run` path has its own resolver (`_expand_file`); it must share the same containment.
    # It reports the rejection as a clean `typer.Exit(2)`, and the message names only the ref.
    import typer

    from bajutsu.common.config import Effective, WebConfig
    from bajutsu.common.scenario import Redact
    from bajutsu.run.cli import _expand_file

    _write(tmp_path / "secret.yaml", "steps:\n  - tap: { id: TOPSECRET }\n")
    scenario = _write(
        tmp_path / "suite" / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: ../secret.yaml }\n",
    )
    eff = Effective(
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
        setup=None,
        capture=[],
        redact=Redact(),
    )
    with pytest.raises(typer.Exit):
        _expand_file(scenario, eff, root=scenario.parent)
    assert "TOPSECRET" not in capsys.readouterr().out


def test_run_expand_file_in_root_component_loads(tmp_path: Path) -> None:
    from bajutsu.common.config import Effective, WebConfig
    from bajutsu.common.scenario import Redact
    from bajutsu.run.cli import _expand_file

    root = tmp_path / "suite"
    _write(root / "components" / "login.yaml", "steps:\n  - tap: { id: login }\n")
    scenario = _write(
        root / "scenarios" / "s.yaml",
        "- name: s\n  steps:\n    - use: { component: ../components/login.yaml }\n",
    )
    eff = Effective(
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
        setup=None,
        capture=[],
        redact=Redact(),
    )
    scenarios, _desc, _plan_sources = _expand_file(scenario, eff, root=root)
    assert scenarios[0].steps[0].tap.id == "login"  # type: ignore[union-attr]


def test_run_expand_file_resolves_seed_photos(tmp_path: Path) -> None:
    # `bajutsu run`'s own loader resolves `seedPhotos` the same way as the device-free readers
    # (`contained_ref`, roadmap item) — not a second implementation that could drift from it.
    from bajutsu.common.config import Effective, WebConfig
    from bajutsu.common.scenario import Redact
    from bajutsu.run.cli import _expand_file

    root = tmp_path / "suite"
    _write(root / "fixtures" / "red.png", "not a real png, path resolution only")
    scenario = _write(
        root / "scenarios" / "s.yaml",
        "- name: s\n  preconditions: { erase: true, seedPhotos: [../fixtures/red.png] }\n"
        "  steps:\n    - tap: { id: a }\n",
    )
    eff = Effective(
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
        setup=None,
        capture=[],
        redact=Redact(),
    )
    scenarios, _desc, _plan_sources = _expand_file(scenario, eff, root=root)
    assert scenarios[0].preconditions.seed_photos == [
        str((root / "fixtures" / "red.png").resolve())
    ]
