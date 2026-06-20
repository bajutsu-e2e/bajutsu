"""Tests for the ScenarioStore seam (BE-0015 local/server parity, PR4).

`ScenarioStore` is the one point where scenario resolution diverges between local and server
hosting: the local store confines everything to the app's scenarios dir on disk
(`LocalScenarioStore`), while a server store would fetch by id from per-project storage. The
arbitrary-path-execution guard (`resolve_runnable`) and the read/write containment live here, in
one place.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu import serve as srv

SCENARIO = "- name: a\n  steps:\n    - tap: { id: home.title }\n"


def _store(tmp_path: Path) -> srv.ScenarioStore:
    """A LocalScenarioStore over a single app 'demo' whose scenarios dir holds smoke.yaml."""
    scn_dir = tmp_path / "scn"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    dirs = {"demo": scn_dir}
    return srv.LocalScenarioStore(lambda app: dirs.get(app or ""))


def test_scope_is_none_for_app_without_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.scope("ghost") is None
    assert store.scope("demo") is not None


def test_resolve_runnable_matches_by_name_only(tmp_path: Path) -> None:
    scope = _store(tmp_path).scope("demo")
    assert scope is not None
    runnable = scope.resolve_runnable("smoke.yaml")
    assert runnable is not None and runnable.name == "smoke.yaml" and runnable.is_file()
    # the UI may pass a full path; only the basename is honoured (then matched against the dir)
    assert scope.resolve_runnable("/etc/passwd/smoke.yaml") == runnable


def test_resolve_runnable_rejects_escapes_and_missing(tmp_path: Path) -> None:
    secret = tmp_path / "secret.yaml"
    secret.write_text(SCENARIO, encoding="utf-8")
    scope = _store(tmp_path).scope("demo")
    assert scope is not None
    for bad in ("../secret.yaml", str(secret), "missing.yaml", "smoke.txt"):
        assert scope.resolve_runnable(bad) is None, bad


def test_read_returns_text_inside_dir_else_none(tmp_path: Path) -> None:
    scope = _store(tmp_path).scope("demo")
    assert scope is not None
    assert scope.read("smoke.yaml") == SCENARIO
    assert scope.read("../secret.yaml") is None  # escapes the dir
    assert scope.read("missing.yaml") is None  # not present


def test_save_writes_a_confined_file_and_rejects_escapes(tmp_path: Path) -> None:
    scope = _store(tmp_path).scope("demo")
    assert scope is not None
    saved = scope.save("new.yaml", SCENARIO)  # need not exist yet (saving); scope owns the write
    assert saved is not None and saved.endswith("new.yaml")
    assert (tmp_path / "scn" / "new.yaml").read_text(encoding="utf-8") == SCENARIO
    assert scope.save("../escape.yaml", SCENARIO) is None
    assert scope.save("note.txt", SCENARIO) is None  # not a .yaml
    assert not (tmp_path / "scn" / "note.txt").exists()  # a rejected save writes nothing


def test_save_creates_scenarios_dir_when_missing(tmp_path: Path) -> None:
    # A fresh project whose scenarios dir does not exist yet: save must create it (mirrors out_path),
    # not raise FileNotFoundError — save's contract allows creating a new file.
    target_dir = tmp_path / "fresh"
    store = srv.LocalScenarioStore(lambda app: target_dir if app == "demo" else None)
    scope = store.scope("demo")
    assert scope is not None
    saved = scope.save("smoke.yaml", SCENARIO)
    assert saved is not None and saved.endswith("smoke.yaml")
    assert (target_dir / "smoke.yaml").read_text(encoding="utf-8") == SCENARIO


def test_invalid_path_with_nul_is_none_not_error(tmp_path: Path) -> None:
    # A client path with an embedded NUL must resolve to None (404/400), never raise (500).
    scope = _store(tmp_path).scope("demo")
    assert scope is not None
    assert scope.read("a\x00.yaml") is None
    assert scope.save("a\x00.yaml", SCENARIO) is None


def test_resolve_runnable_rejects_symlink_out_of_dir(tmp_path: Path) -> None:
    # A *.yaml in the dir that symlinks outside must not be runnable (containment, BE-0051).
    outside = tmp_path / "outside.yaml"
    outside.write_text(SCENARIO, encoding="utf-8")
    scope = _store(tmp_path).scope("demo")
    assert scope is not None
    link = tmp_path / "scn" / "evil.yaml"
    link.symlink_to(outside)
    assert link.is_file()  # the symlink itself resolves to a real file…
    assert scope.resolve_runnable("evil.yaml") is None  # …but escapes the dir, so it's rejected


def test_out_path_makes_unique_yaml_and_creates_dir(tmp_path: Path) -> None:
    # A fresh app whose dir does not exist yet: out_path must create it and return a *.yaml.
    target_dir = tmp_path / "fresh"
    store = srv.LocalScenarioStore(lambda app: target_dir if app == "demo" else None)
    scope = store.scope("demo")
    assert scope is not None
    out = scope.out_path("login flow")
    assert out.parent == target_dir and target_dir.is_dir() and out.suffix == ".yaml"
