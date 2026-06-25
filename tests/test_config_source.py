"""Acquiring a config from a Git source (bajutsu/config_source.py, BE-0063).

Spec parsing is pure; materialization talks to a Git host, so its tests inject a fake transport
(the one external dependency) — no network, no `git` binary, no Simulator.
"""

from __future__ import annotations

import io
import tarfile

import pytest

from bajutsu.config_source import GitConfigSpec, materialize, parse_config_spec

# --- parse_config_spec ---


def test_parse_local_path_is_not_a_git_spec() -> None:
    assert parse_config_spec("bajutsu.config.yaml") is None
    assert parse_config_spec("./e2e/bajutsu.config.yaml") is None
    assert parse_config_spec("/abs/path/config.yaml") is None


def test_parse_github_shorthand_full() -> None:
    spec = parse_config_spec("github:acme/mobile-tests@main:e2e/bajutsu.config.yaml")
    assert spec == GitConfigSpec(
        host="github.com",
        owner="acme",
        repo="mobile-tests",
        ref="main",
        path="e2e/bajutsu.config.yaml",
    )


def test_parse_github_shorthand_defaults_ref_and_path() -> None:
    # No @ref and no :path — the default branch and the root DEFAULT_CONFIG.
    spec = parse_config_spec("github:acme/mobile-tests")
    assert spec is not None
    assert (spec.owner, spec.repo) == ("acme", "mobile-tests")
    assert spec.ref is None and spec.path is None


def test_parse_github_shorthand_ref_without_path() -> None:
    spec = parse_config_spec("github:acme/mobile-tests@v1.4.0")
    assert spec is not None and spec.ref == "v1.4.0" and spec.path is None


def test_parse_github_shorthand_path_without_ref() -> None:
    spec = parse_config_spec("github:acme/mobile-tests:e2e/cfg.yaml")
    assert spec is not None and spec.ref is None and spec.path == "e2e/cfg.yaml"


def test_parse_general_git_url() -> None:
    spec = parse_config_spec("git+https://git.example.com/acme/repo.git@dev#sub/cfg.yaml")
    assert spec == GitConfigSpec(
        host="git.example.com", owner="acme", repo="repo", ref="dev", path="sub/cfg.yaml"
    )


# --- materialize (fake transport) ---


def _tarball(sha: str, files: dict[str, str]) -> bytes:
    """A GitHub-style tar.gz: every entry under a single `<owner>-<repo>-<sha7>/` wrapper dir."""
    buf = io.BytesIO()
    root = f"acme-mobile-tests-{sha[:7]}"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{root}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeTransport:
    """Stands in for the GitHub API + tarball endpoint; records call counts so a cache hit is observable."""

    def __init__(self, sha: str, tarball: bytes) -> None:
        self.sha = sha
        self.tarball = tarball
        self.commit_calls = 0
        self.tarball_calls = 0

    def commit_sha(self, spec: GitConfigSpec, ref: str) -> str:
        self.commit_calls += 1
        return self.sha

    def tarball_bytes(self, spec: GitConfigSpec, sha: str) -> bytes:
        self.tarball_calls += 1
        return self.tarball


def test_materialize_extracts_tree_and_locates_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    tb = _tarball(
        sha, {"bajutsu.config.yaml": "defaults: {}\n", "scenarios/smoke.yaml": "- name: s\n"}
    )
    transport = _FakeTransport(sha, tb)
    spec = parse_config_spec("github:acme/mobile-tests@main")
    assert spec is not None

    mat = materialize(spec, transport=transport, cache_root=tmp_path)
    assert mat.sha == sha
    assert mat.config_path.name == "bajutsu.config.yaml"
    assert mat.config_path.read_text(encoding="utf-8") == "defaults: {}\n"
    # the wrapper dir was stripped: the scenarios tree sits directly under the checkout root
    assert (mat.root / "scenarios" / "smoke.yaml").exists()


def test_materialize_is_cached_by_sha(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    tb = _tarball(sha, {"bajutsu.config.yaml": "defaults: {}\n"})
    transport = _FakeTransport(sha, tb)
    spec = parse_config_spec("github:acme/mobile-tests@main")
    assert spec is not None

    materialize(spec, transport=transport, cache_root=tmp_path)
    materialize(spec, transport=transport, cache_root=tmp_path)
    # the immutable-SHA cache means the second call re-resolves the ref but never re-downloads
    assert transport.tarball_calls == 1


def test_materialize_pinned_sha_skips_the_commits_api(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A full 40-hex SHA is already the immutable id, so no commits-API call is needed; a cache hit
    # is then fully offline (the determinism anchor the design promises).
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    transport = _FakeTransport(sha, _tarball(sha, {"bajutsu.config.yaml": "defaults: {}\n"}))
    spec = parse_config_spec(f"github:acme/mobile-tests@{sha}")
    assert spec is not None and spec.ref == sha

    materialize(spec, transport=transport, cache_root=tmp_path)
    assert transport.commit_calls == 0  # the SHA is used directly, not resolved
    materialize(spec, transport=transport, cache_root=tmp_path)
    assert transport.tarball_calls == 1 and transport.commit_calls == 0  # cache hit, fully offline


# --- _load_effective wiring: a Git-sourced config rebases its relative paths against the checkout ---


def test_load_effective_rebases_paths_against_git_checkout(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.cli import _shared
    from bajutsu.config_source import Materialized

    # A materialized checkout: the config plus its relative scenarios/appPath, as they'd sit in a repo.
    root = tmp_path / "co"
    (root / "e2e" / "scenarios").mkdir(parents=True)
    (root / "e2e" / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        "    scenarios: e2e/scenarios\n    appPath: build/Demo.app\n",
        encoding="utf-8",
    )

    def fake_materialize(spec):  # type: ignore[no-untyped-def]
        return Materialized(root / "e2e" / "bajutsu.config.yaml", root, "deadbeef")

    monkeypatch.setattr(_shared, "materialize", fake_materialize)

    eff = _shared._load_effective("github:acme/mobile-tests@main:e2e/bajutsu.config.yaml", "demo")
    # relative config entries are now absolute under the checkout root, not the caller's cwd
    assert eff.scenarios == str(root / "e2e/scenarios")
    assert eff.app_path == str(root / "build/Demo.app")


def test_load_effective_git_wrong_path_exits_cleanly(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A materialized tree that doesn't hold the requested config path gets the same friendly exit-2
    # as a missing local config — not a raw FileNotFoundError.
    import typer

    from bajutsu.cli import _shared
    from bajutsu.config_source import Materialized

    root = tmp_path / "co"
    root.mkdir()  # the checkout exists, but bajutsu.config.yaml does not

    monkeypatch.setattr(
        _shared, "materialize", lambda spec: Materialized(root / "bajutsu.config.yaml", root, "sha")
    )
    with pytest.raises(typer.Exit) as exc:
        _shared._load_effective("github:acme/mobile-tests@main", "demo")
    assert exc.value.exit_code == 2
