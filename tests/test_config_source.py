"""Acquiring a config from a Git source (bajutsu/config_source.py, BE-0063).

Spec parsing is pure; materialization talks to a Git host, so its tests inject a fake transport
(the one external dependency) — no network, no `git` binary, no Simulator.
"""

from __future__ import annotations

import io
import tarfile
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from bajutsu.config import IosConfig
from bajutsu.config_source import (
    GitConfigSpec,
    GitHubAccessError,
    Materialized,
    _GitHubTransport,
    github_http_error_message,
    materialize,
    parse_config_spec,
    resolve_github_credential,
    source_provenance,
)

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


def test_parse_keeps_a_dotted_repo_name() -> None:
    # A dot is legitimate inside a repo name (`repo.js`); only a bare `.`/`..` segment is a traversal.
    spec = parse_config_spec("github:acme/my.repo.js")
    assert spec is not None and (spec.owner, spec.repo) == ("acme", "my.repo.js")


def test_parse_rejects_a_traversal_repo_segment() -> None:
    # `.` / `..` as the whole repo segment would climb out of the `<host>/<owner>/<repo>/<sha>/` cache
    # layout, so it fails to parse the same way an unrecognized string does (BE-0124).
    assert parse_config_spec("github:acme/..") is None
    assert parse_config_spec("github:acme/.") is None
    assert parse_config_spec("git+https://git.example.com/acme/..") is None
    assert parse_config_spec("git+https://git.example.com/acme/.") is None


def test_parse_rejects_percent_in_owner_or_repo() -> None:
    # Percent-encoding has no legitimate role in an owner/repo segment; reject it outright (BE-0124).
    assert parse_config_spec("github:acme/repo%2e%2e") is None
    assert parse_config_spec("github:ac%6de/repo") is None
    assert parse_config_spec("git+https://git.example.com/acme/repo%2e%2e.git") is None
    assert parse_config_spec("git+https://git.example.com/ac%6de/repo.git") is None


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


def test_materialize_pinned_cache_hit_resolves_no_credential(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The credential is resolved (and a GitHub App token possibly minted) only when a network op is
    # needed — never on a pinned-SHA cache hit — so re-serving a cached config does no auth work and
    # `--config-offline` stays truly offline (BE-0224). The real path is exercised (transport=None).
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    root = tmp_path / "github.com" / "acme" / "repo" / sha
    root.mkdir(parents=True)
    (root / "bajutsu.config.yaml").write_text("defaults: {}\n", encoding="utf-8")

    def fail(spec):  # type: ignore[no-untyped-def]
        raise AssertionError("credential must not be resolved on a cache hit")

    monkeypatch.setattr("bajutsu.config_source.resolve_github_credential", fail)
    spec = parse_config_spec(f"github:acme/repo@{sha}")
    assert spec is not None
    mat = materialize(spec, cache_root=tmp_path)
    assert mat.sha == sha and mat.config_path.read_text(encoding="utf-8") == "defaults: {}\n"


def test_materialize_offline_uses_a_cached_pinned_sha_without_network(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # --config-offline: a pinned SHA already in the cache runs with no transport calls at all.
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    transport = _FakeTransport(sha, _tarball(sha, {"bajutsu.config.yaml": "x\n"}))
    spec = parse_config_spec(f"github:acme/mobile-tests@{sha}")
    assert spec is not None
    materialize(spec, transport=transport, cache_root=tmp_path)  # warm the cache (online)
    transport.tarball_calls = transport.commit_calls = 0
    mat = materialize(spec, transport=transport, cache_root=tmp_path, offline=True)
    assert mat.sha == sha and transport.tarball_calls == 0 and transport.commit_calls == 0


def test_materialize_offline_cache_miss_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    transport = _FakeTransport(sha, _tarball(sha, {"bajutsu.config.yaml": "x\n"}))
    spec = parse_config_spec(f"github:acme/mobile-tests@{sha}")
    assert spec is not None
    with pytest.raises(ValueError, match="offline"):
        materialize(spec, transport=transport, cache_root=tmp_path, offline=True)


def test_materialize_offline_branch_ref_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A branch can't be resolved to a SHA without the network, so offline refuses it before any call.
    transport = _FakeTransport("deadbeef", b"")
    spec = parse_config_spec("github:acme/mobile-tests@main")
    assert spec is not None
    with pytest.raises(ValueError, match="offline"):
        materialize(spec, transport=transport, cache_root=tmp_path, offline=True)
    assert transport.commit_calls == 0


def test_materialize_rejects_non_github_host(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The git+https form parses for any host, but only GitHub is implemented — fail clearly rather
    # than silently hitting github.com (default transport only).
    spec = parse_config_spec("git+https://gitlab.example.com/acme/repo.git@main")
    assert spec is not None and spec.host == "gitlab.example.com"
    with pytest.raises(ValueError, match="only github"):
        materialize(spec, cache_root=tmp_path)


def test_materialize_refuses_a_path_escaping_the_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A spec component or in-repo path that climbs out of the cache (`..`) is refused before any fetch.
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    transport = _FakeTransport(sha, _tarball(sha, {"bajutsu.config.yaml": "x\n"}))
    spec = GitConfigSpec("github.com", "acme", "repo", sha, "../../../../etc/passwd")
    with pytest.raises(ValueError, match="outside the cache"):
        materialize(spec, transport=transport, cache_root=tmp_path)
    assert transport.tarball_calls == 0  # refused before fetching


def test_materialize_corrupt_tarball_raises_value_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A truncated/corrupt body (a rate-limit page, a proxy interstitial) is a clean ValueError, not a
    # bare tarfile.ReadError — so callers' fetch-error handling catches it instead of a traceback.
    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    transport = _FakeTransport(sha, b"not a gzip tarball at all")
    spec = parse_config_spec(f"github:acme/mobile-tests@{sha}")
    assert spec is not None
    with pytest.raises(ValueError, match="could not read the repository tarball"):
        materialize(spec, transport=transport, cache_root=tmp_path)


def test_source_provenance_records_repo_and_resolved_sha() -> None:
    # A branch-based run records the exact commit it executed (BE-0063), so the manifest is
    # reproducible after the fact.
    spec = GitConfigSpec("github.com", "acme", "tests", "main", "e2e/cfg.yaml")
    mat = Materialized(Path("/c/e2e/cfg.yaml"), Path("/c"), "deadbeef")
    assert source_provenance(spec, mat) == {
        "host": "github.com",
        "owner": "acme",
        "repo": "tests",
        "ref": "main",
        "sha": "deadbeef",
    }
    # no @ref ⇒ the default branch is labeled rather than left blank
    assert source_provenance(GitConfigSpec("github.com", "a", "b", None, None), mat)["ref"] == (
        "(default)"
    )


# --- private-repo credential resolution + auth diagnostics (BE-0224) ---

_SPEC = GitConfigSpec("github.com", "acme", "repo", None, None)


def _headers(**kw: str) -> Message:
    m = Message()
    for key, value in kw.items():
        m[key.replace("_", "-")] = value
    return m


@pytest.mark.parametrize(
    ("status", "headers", "needle"),
    [
        (403, _headers(X_RateLimit_Remaining="0"), "rate limit"),
        (403, _headers(Retry_After="60"), "rate limit"),
        (429, _headers(Retry_After="60"), "rate limit"),  # secondary rate limit is a 429
        (429, _headers(), "rate limit"),
        (403, _headers(X_GitHub_SSO="required"), "single sign-on"),
        (401, _headers(), "rejected"),
        (404, _headers(), "not found, or access not granted"),
        (403, _headers(), "not found, or access not granted"),  # a plain 403 falls through
        (500, _headers(), "unexpected 500"),  # a 5xx outage is NOT reported as missing access
        (422, _headers(), "unexpected 422"),
    ],
)
def test_github_http_error_message_names_the_real_cause(
    status: int, headers: Message, needle: str
) -> None:
    msg = github_http_error_message(status, headers, _SPEC)
    assert needle in msg
    assert "acme/repo" in msg  # the message always names the repo the grant is needed for


def test_rate_limit_403_is_not_reported_as_missing_access() -> None:
    # A 403 that is really a rate limit must not steer the operator to grant more repo permission —
    # checking the sub-type before the catch-all is the whole point (BE-0224 #4).
    msg = github_http_error_message(403, _headers(X_RateLimit_Remaining="0"), _SPEC)
    assert "Contents: read" not in msg


def test_transport_get_wraps_httperror_as_access_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A raw HTTPError from urllib becomes a GitHubAccessError carrying the cause-naming message, so a
    # private-repo 404 never reaches a caller as a bare traceback.
    def raise_404(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", _headers(), None)

    monkeypatch.setattr("urllib.request.urlopen", raise_404)
    with pytest.raises(GitHubAccessError, match="access not granted"):
        _GitHubTransport("tok").commit_sha(_SPEC, "main")


def test_resolve_credential_prefers_env_token_when_no_app(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("BAJUTSU_GIT_CONFIG_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "tok-env")
    assert resolve_github_credential(_SPEC) == "tok-env"


def test_resolve_credential_ui_token_wins_over_ambient(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The bajutsu-owned var (a UI-entered credential) is checked before an operator's ambient token,
    # so a serve-entered credential wins and clearing it never touches GITHUB_TOKEN (BE-0224).
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_ID", raising=False)
    monkeypatch.setenv("BAJUTSU_GIT_CONFIG_TOKEN", "ui-token")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient")
    assert resolve_github_credential(_SPEC) == "ui-token"


def test_resolve_credential_ignores_stale_app_key_file_without_id(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A leftover key-file var without an App id must NOT trigger the App path (no file read, no
    # cryptography import) — it falls through to the env token (BE-0224 review fix).
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_ID", raising=False)
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE", str(tmp_path / "does-not-exist.pem"))
    monkeypatch.delenv("BAJUTSU_GIT_CONFIG_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "tok-env")
    assert resolve_github_credential(_SPEC) == "tok-env"  # no FileNotFoundError, no App attempt


def test_resolve_credential_app_missing_key_file_is_a_clean_error(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    # With the App id set but the key file unreadable, the error names the cause instead of leaking a
    # raw OSError traceback.
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_ID", "123")
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(GitHubAccessError, match="cannot read the GitHub App private key"):
        resolve_github_credential(_SPEC)


def test_resolve_credential_app_id_without_key_falls_through(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # An App id with no key at all is not a half-attempt — it falls through to the env token.
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_ID", "123")
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("BAJUTSU_GIT_CONFIG_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "tok-env")
    assert resolve_github_credential(_SPEC) == "tok-env"


def test_resolve_credential_prefers_a_configured_github_app(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # An App credential wins over a PAT in the env (the documented precedence, BE-0224 #2); the App
    # module is only reached when both an id and a key are present.
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_ID", "123")
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY", "----KEY----")
    monkeypatch.setenv("GITHUB_TOKEN", "tok-env")
    seen = {}

    def fake_installation_token(app_id, key, spec, *, installation_id=None):  # type: ignore[no-untyped-def]
        seen["app_id"], seen["key"] = app_id, key
        return "ghs_apptoken"

    monkeypatch.setattr("bajutsu.github_app.installation_token", fake_installation_token)
    assert resolve_github_credential(_SPEC) == "ghs_apptoken"
    assert seen == {"app_id": "123", "key": "----KEY----"}


def test_resolve_credential_app_key_from_a_file(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    key_file = tmp_path / "app.pem"
    key_file.write_text("----FILE-KEY----", encoding="utf-8")
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_ID", "123")
    monkeypatch.delenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE", str(key_file))
    monkeypatch.setattr(
        "bajutsu.github_app.installation_token",
        lambda app_id, key, spec, *, installation_id=None: f"tok:{key}",
    )
    assert resolve_github_credential(_SPEC) == "tok:----FILE-KEY----"


def test_load_effective_git_access_error_exits_cleanly(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A private-repo access/auth failure gets the same friendly exit-2 as a missing config, with the
    # cause message rather than a raw HTTPError traceback (BE-0224 #4).
    import typer

    from bajutsu.cli import _shared

    def boom(spec, *, offline=False):  # type: ignore[no-untyped-def]
        raise GitHubAccessError("cannot access acme/repo (404): ... provide Contents: read")

    monkeypatch.setattr(_shared, "materialize", boom)
    with pytest.raises(typer.Exit) as exc:
        _shared._load_effective("github:acme/repo@main", "demo")
    assert exc.value.exit_code == 2


# --- _load_effective wiring: a Git-sourced config rebases its relative paths against the checkout ---


def test_load_effective_rebases_paths_against_git_checkout(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.cli import _shared

    # A materialized checkout: the config plus its relative scenarios/appPath, as they'd sit in a repo.
    root = tmp_path / "co"
    (root / "e2e" / "scenarios").mkdir(parents=True)
    (root / "e2e" / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        "    scenarios: e2e/scenarios\n    appPath: build/Demo.app\n",
        encoding="utf-8",
    )

    def fake_materialize(spec, *, offline=False):  # type: ignore[no-untyped-def]
        return Materialized(root / "e2e" / "bajutsu.config.yaml", root, "deadbeef")

    monkeypatch.setattr(_shared, "materialize", fake_materialize)

    eff = _shared._load_effective("github:acme/mobile-tests@main:e2e/bajutsu.config.yaml", "demo")
    # relative config entries are now absolute under the checkout root, not the caller's cwd
    assert eff.evidence_dirs.scenarios == str(root / "e2e/scenarios")
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.app_path == str(root / "build/Demo.app")


def test_load_effective_local_config_rebases_against_the_config_dir(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A local config's relative paths resolve against the config file's own directory, independent of
    # the caller's cwd (BE-0242) — chdir elsewhere and the resolution must not move with it.
    from bajutsu.cli import _shared

    cfg_dir = tmp_path / "proj"
    (cfg_dir / "scn").mkdir(parents=True)
    (cfg_dir / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        "    scenarios: scn\n    appPath: build/Demo.app\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)  # deliberately not the config's directory
    eff = _shared._load_effective(str(cfg_dir / "bajutsu.config.yaml"), "demo")
    assert eff.evidence_dirs.scenarios == str(
        cfg_dir / "scn"
    )  # anchored at the config dir, not cwd (tmp_path)
    assert isinstance(eff.platform_config, IosConfig)
    assert eff.platform_config.app_path == str(cfg_dir / "build/Demo.app")


def test_load_effective_local_config_allows_a_path_outside_its_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A local file is operator-trusted (BE-0121), so — unlike a fetched Git config — it may point at a
    # sibling outside its own directory: the `..` resolves, it is not a confinement exit-2 (BE-0242).
    from bajutsu.cli import _shared

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n    scenarios: ../shared/scn\n",
        encoding="utf-8",
    )
    eff = _shared._load_effective(str(proj / "bajutsu.config.yaml"), "demo")
    assert Path(eff.evidence_dirs.scenarios or "").resolve() == (tmp_path / "shared" / "scn")


def test_load_effective_local_config_returns_no_checkout_root(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The rebase anchor must not leak into the third tuple element: `checkout_root` stays None for a
    # local config so it keeps reading as "local, not a read-only Git checkout" — otherwise `run`'s
    # on-demand build_if_missing and record/crawl's _refuse_out_in_checkout would switch on (BE-0242).
    from bajutsu.cli import _shared

    proj = tmp_path / "proj"
    (proj / "scn").mkdir(parents=True)
    (proj / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n    scenarios: scn\n",
        encoding="utf-8",
    )
    _eff, source, checkout_root = _shared._load_effective_with_source(
        str(proj / "bajutsu.config.yaml"), "demo"
    )
    assert source is None and checkout_root is None  # local config: no Git provenance, no checkout


def test_load_effective_git_wrong_path_exits_cleanly(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A materialized tree that doesn't hold the requested config path gets the same friendly exit-2
    # as a missing local config — not a raw FileNotFoundError.
    import typer

    from bajutsu.cli import _shared

    root = tmp_path / "co"
    root.mkdir()  # the checkout exists, but bajutsu.config.yaml does not

    monkeypatch.setattr(
        _shared,
        "materialize",
        lambda spec, *, offline=False: Materialized(root / "bajutsu.config.yaml", root, "sha"),
    )
    with pytest.raises(typer.Exit) as exc:
        _shared._load_effective("github:acme/mobile-tests@main", "demo")
    assert exc.value.exit_code == 2


def test_require_pinned_rejects_a_non_sha_ref(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # --require-pinned-config: a gate must run an immutable commit. A branch/tag/default ref is
    # refused before any fetch; only a full commit SHA is accepted.
    import typer

    from bajutsu.cli import _shared

    called = False

    def must_not_materialize(spec, *, offline=False):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        raise AssertionError("materialize must not run when the ref is rejected")

    monkeypatch.setattr(_shared, "materialize", must_not_materialize)
    with pytest.raises(typer.Exit) as exc:
        _shared._load_effective_with_source("github:acme/repo@main", "demo", require_pinned=True)
    assert exc.value.exit_code == 2 and not called


def test_require_pinned_allows_a_full_sha(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.cli import _shared

    sha = "9f3c1ab2c3d4e5f60718293a4b5c6d7e8f901234"
    root = tmp_path / "co"
    root.mkdir()
    (root / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        _shared,
        "materialize",
        lambda spec, *, offline=False: Materialized(root / "bajutsu.config.yaml", root, sha),
    )
    _eff, source, checkout_root = _shared._load_effective_with_source(
        f"github:acme/repo@{sha}", "demo", require_pinned=True
    )
    assert source is not None and source["sha"] == sha  # accepted; the pinned SHA is recorded
    assert checkout_root == root  # the materialized checkout root is returned for the build step


def test_load_effective_git_config_escaping_path_exits_cleanly(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A Git config whose scenarios path climbs out of the checkout is refused with a clean exit-2,
    # not a traceback (confinement, BE-0051).
    import typer

    from bajutsu.cli import _shared

    root = tmp_path / "co"
    root.mkdir()
    (root / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n    scenarios: ../../../etc\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _shared,
        "materialize",
        lambda spec, *, offline=False: Materialized(root / "bajutsu.config.yaml", root, "sha"),
    )
    with pytest.raises(typer.Exit) as exc:
        _shared._load_effective("github:acme/repo@main", "demo")
    assert exc.value.exit_code == 2


def test_run_builds_a_git_sourced_app_from_the_checkout_root(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A Git-sourced run builds the missing binary on demand, with the checkout root as the working
    # directory — the config's `build` and relative `appPath` are rooted there (BE-0063).
    from typer.testing import CliRunner

    from bajutsu.cli import _shared, app

    root = tmp_path / "co"
    (root / "e2e").mkdir(parents=True)
    (root / "bajutsu.config.yaml").write_text(
        "targets:\n"
        "  demo:\n"
        "    bundleId: com.example.demo\n"
        "    appPath: build/Demo.app\n"  # relative → rebased under the checkout root
        "    build: mkdir -p build/Demo.app\n"  # rooted at the checkout, not the test's cwd
        "    scenarios: e2e\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _shared,
        "materialize",
        lambda spec, *, offline=False: Materialized(root / "bajutsu.config.yaml", root, "sha"),
    )
    # `--backend nope` makes the run exit cleanly after the build (the sandbox has no Simulator),
    # so the test asserts only that the build ran — producing the binary under the checkout root.
    r = CliRunner().invoke(
        app, ["run", "--target", "demo", "--backend", "nope", "--config", "github:acme/repo@main"]
    )
    assert (root / "build" / "Demo.app").exists()  # built under the checkout, not the process cwd
    assert r.exit_code == 2  # then the unavailable backend exits cleanly
