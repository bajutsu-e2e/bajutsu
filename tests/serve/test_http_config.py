"""Tests for the bajutsu serve scenario save and config binding endpoints (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _shared import (
    _get_json,
    _post,
    _serve,
    project,
)

from bajutsu import serve as srv
from bajutsu.serve.operations import config as config_ops
from bajutsu.serve.operations import orgs as org_ops

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine


def test_http_scenario_save_validates_and_writes(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        target = scn_dir / "smoke.yaml"
        edited = "- name: edited\n  steps:\n    - tap: { id: y }\n"
        body = json.dumps({"path": str(target), "yaml": edited}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenario",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is True
        assert target.read_text(encoding="utf-8") == edited  # the edit landed on disk

        bad = json.dumps({"path": str(target), "yaml": "steps: [not, a, scenario, list"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenario",
            data=bad,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError, match="400"):
            urllib.request.urlopen(req)
        assert target.read_text(encoding="utf-8") == edited  # rejected save left the file intact
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenario_save_reports_overwritten(tmp_path: Path) -> None:
    # BE-0340: the response signals whether a save replaced an existing file, so the Author editor's
    # Save button (and the Replay upload control) can tell a fresh add from an overwrite.
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        target = scn_dir / "fresh.yaml"
        text = "- name: alpha\n  steps:\n    - tap: { id: x }\n"
        status, resp = _post(port, "/api/scenario", {"path": str(target), "yaml": text})
        assert status == 200 and resp["overwritten"] is False

        status, resp = _post(port, "/api/scenario", {"path": str(target), "yaml": text})
        assert status == 200 and resp["overwritten"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenario_save_overwrites_a_non_utf8_file_without_500ing(tmp_path: Path) -> None:
    # The overwritten probe reads the existing file to check whether it's there; a file that
    # exists but can't be decoded (bad encoding, a stray binary) must still count as "there to
    # overwrite" rather than letting the probe itself fail the save (mirrors
    # LocalTreeScenarioStorage.read's own leniency for the same reason).
    scn_dir, cfg, runs = project(tmp_path)
    target = scn_dir / "corrupt.yaml"
    target.write_bytes(b"\xff\xfe not valid utf-8")
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        text = "- name: alpha\n  steps:\n    - tap: { id: x }\n"
        status, resp = _post(port, "/api/scenario", {"path": str(target), "yaml": text})
        assert status == 200 and resp["overwritten"] is True
        assert target.read_text(encoding="utf-8") == text
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenario_save_reports_bad_path_before_bad_yaml(tmp_path: Path) -> None:
    # When both the path and the YAML are invalid, the path error wins (a non-saveable ref is
    # reported before the scenario is parsed), so the client learns where to save first.
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post(
            port, "/api/scenario", {"path": "note.txt", "yaml": "steps: [not, a, list"}
        )
        assert status == 400 and "path must be" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_open_config_binds_and_lists_apps(tmp_path: Path) -> None:
    _, _, runs = project(tmp_path)
    # No config bound at startup; the browse root is the project dir.
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/config")["hasConfig"] is False
        assert _get_json(port, "/api/targets") == []  # nothing until a config is opened
        status, resp = _post(port, "/api/config", {"path": "bajutsu.common.config.yaml"})
        assert status == 200 and resp["ok"] is True and resp["targets"] == ["demo", "other"]
        assert _get_json(port, "/api/config")["hasConfig"] is True
        assert [a["name"] for a in _get_json(port, "/api/targets")] == ["demo", "other"]
        # The file browser lists (and posts back) the *absolute* path it built from list_fs's cwd, so
        # an absolute path inside the browse root is the normal case and must bind, not be rejected.
        abs_in_root = str((tmp_path / "bajutsu.common.config.yaml").resolve())
        status, resp = _post(port, "/api/config", {"path": abs_in_root})
        assert status == 200 and resp["ok"] is True and resp["targets"] == ["demo", "other"]
        # A path outside the browse root is rejected.
        status, _ = _post(port, "/api/config", {"path": "/etc/hosts"})
        assert status == 400
        # A path inside root but not a config file → 404.
        status, _ = _post(port, "/api/config", {"path": "nope.yaml"})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_http_open_local_config_from_subdir_binds_config_dir(tmp_path: Path) -> None:
    # Binding a local config that lives in a *subdirectory* of the browse root repoints state.cwd to
    # that subdirectory, so the config's relative paths resolve from beside it, not from serve's launch
    # dir (BE-0242) — the local counterpart of the Git bind below. The config sits under a subdir (not
    # directly at the root) so the config dir genuinely differs from the launch dir.
    proj = tmp_path / "proj"
    (proj / "scn").mkdir(parents=True)
    (proj / "scn" / "smoke.yaml").write_text(
        "- name: s\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    cfg = proj / "bajutsu.common.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, scenarios: scn }\n",  # relative to the config
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    state = srv.ServeState(
        runs_dir=runs, root=tmp_path, cwd=tmp_path
    )  # launch dir = root, not proj
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/config", {"path": str(cfg)})
        assert status == 200 and resp["ok"] is True and resp["targets"] == ["demo"]
        assert state.config == cfg
        assert state.cwd == proj  # cwd repointed to the config's own directory, not the launch dir
        # The relative `scenarios: scn` resolves against proj/, so the listing finds smoke.yaml.
        assert _get_json(port, "/api/scenarios?target=demo")[0]["names"] == ["s"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_open_config_from_git_binds_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The "from Git" picker: a github: spec materializes a checkout, binds its config, and repoints
    # state.cwd to the checkout root so build/scenarios resolve there (BE-0063).
    import bajutsu.serve.operations.config as ops  # bind_git_config resolves `materialize` here
    from bajutsu.common.config_source import Materialized

    _, _, runs = project(tmp_path)
    checkout = tmp_path / "gitsrc"
    # A scenarios dir relative to the checkout root (the common case) — it must resolve against the
    # checkout, not serve's launch dir, for the UI listing to find these files.
    (checkout / "e2e").mkdir(parents=True)
    (checkout / "e2e" / "smoke.yaml").write_text(
        "- name: s\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    git_cfg = checkout / "bajutsu.common.config.yaml"
    git_cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n  fromgit: { bundleId: com.example.fromgit, scenarios: e2e }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ops, "materialize", lambda spec, **kw: Materialized(git_cfg, checkout, "deadbeefcafe")
    )
    state = srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/config", {"git": "github:acme/repo@main"})
        assert status == 200 and resp["ok"] is True
        assert resp["targets"] == ["fromgit"]  # targets come from the fetched config
        assert resp["source"]["sha"] == "deadbeefcafe"  # the resolved commit is surfaced
        assert state.config == git_cfg  # config repointed to the checkout
        assert state.cwd == checkout  # cwd repointed so the checkout's relative paths resolve
        # The relative `scenarios: e2e` resolves against the checkout, so the listing finds smoke.yaml.
        assert _get_json(port, "/api/scenarios?target=fromgit")[0]["names"] == ["s"]
        assert _get_json(port, "/api/config")["hasConfig"] is True
        # The bind stamps the resolved commit into state, and /api/config/content surfaces the raw
        # YAML plus that provenance (BE-0063) — so the UI can confirm which commit is actually bound,
        # not just the opaque cache path.
        assert state.config_provenance is not None
        assert state.config_provenance["sha"] == "deadbeefcafe"
        content = _get_json(port, "/api/config/content")
        assert content["content"] == git_cfg.read_text(encoding="utf-8")
        assert content["provenance"]["sha"] == "deadbeefcafe"
        # A value with no recognized scheme is not a Git spec → a clear 400, not a local-path read.
        status, resp = _post(port, "/api/config", {"git": "/etc/passwd"})
        assert status == 400
        # An explicit but empty `git` routes to the Git binder (key presence, not truthiness), so it
        # gets the "spec is required" 400 rather than silently falling back to the local file binder.
        status, resp = _post(port, "/api/config", {"git": ""})
        assert status == 400 and "spec is required" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_content_returns_local_yaml_without_provenance(tmp_path: Path) -> None:
    # A local file config: /api/config/content returns its verbatim YAML and no Git provenance.
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        d = _get_json(port, "/api/config/content")
        assert d["config"] == str(cfg)
        assert d["content"] == cfg.read_text(encoding="utf-8")
        assert d["provenance"] is None  # a local file has no commit to point at
        # The parsed structure powers the UI's collapsible tree; it mirrors the file faithfully
        # (targets keyed by name), with no env interpolation applied.
        assert set(d["parsed"]) == {"defaults", "targets"}
        assert set(d["parsed"]["targets"]) == {"demo", "other"}
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_content_uses_restricted_loader_and_stays_json_safe(tmp_path: Path) -> None:
    # The parsed structure must use the project's restricted YAML loader (so an `on:` key stays the
    # string "on", not the bool True as YAML 1.1 would coerce it) and must be JSON-serializable — a
    # bare `date` from a timestamp would otherwise make the handler's json.dumps 500. config_content
    # reads state.config directly (no schema re-validation), so a tricky file exercises both.
    _, _, runs = project(tmp_path)
    cfg = tmp_path / "tricky.yaml"
    cfg.write_text("on: 1\nreleased: 2020-01-02\ndefaults: { backend: [ios] }\n", encoding="utf-8")
    server, port = _serve(srv.ServeState(config=cfg, runs_dir=runs, cwd=tmp_path))
    try:
        d = _get_json(port, "/api/config/content")  # would raise on a 500 (non-JSON-safe payload)
        assert "on" in d["parsed"]  # restricted loader kept the key a string, not True
        assert d["parsed"]["released"] == "2020-01-02"  # the date was coerced to a string for JSON
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_content_404_when_no_config_bound(tmp_path: Path) -> None:
    # Nothing bound yet (the UI opens the picker instead): the content endpoint 404s rather than
    # returning an empty body the viewer would render as a blank config.
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, cwd=tmp_path))
    try:
        with pytest.raises(urllib.error.HTTPError, match="404"):
            _get_json(port, "/api/config/content")
    finally:
        server.shutdown()
        server.server_close()


def test_http_git_config_with_escaping_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fetched config whose path field climbs out of the checkout is rejected at bind, so serve's
    # (unconfined) scenario/build resolution never sees a host path outside the tree (BE-0063/BE-0051).
    import bajutsu.serve.operations.config as ops  # bind_git_config resolves `materialize` here
    from bajutsu.common.config_source import Materialized

    _, _, runs = project(tmp_path)
    checkout = tmp_path / "gitsrc"
    checkout.mkdir()
    git_cfg = checkout / "bajutsu.common.config.yaml"
    git_cfg.write_text(
        "targets:\n  evil: { bundleId: com.example.evil, scenarios: ../../../etc }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ops, "materialize", lambda spec, **kw: Materialized(git_cfg, checkout, "sha")
    )
    state = srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/config", {"git": "github:acme/repo@main"})
        assert status == 400 and "invalid config" in resp["error"]
        assert state.config is None  # the escaping config was not bound
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_rejects_absolute_traversal_outside_root(tmp_path: Path) -> None:
    # An absolute path with `..` that resolves outside the browse root must be rejected, not read
    # (CodeQL py/path-injection): the containment check resolves the path first, so the literal
    # parent of the unresolved path can't slip it through.
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.yaml"  # outside root, but inside tmp_path
    secret.write_text("targets: {evil: {bundleId: x}}\n", encoding="utf-8")
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=root, cwd=tmp_path))
    try:
        escape = str(root / ".." / "secret.yaml")  # absolute, resolves to tmp_path/secret.yaml
        status, resp = _post(port, "/api/config", {"path": escape})
        assert status == 400 and "outside the browse root" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_local_config_with_escaping_path_is_refused(tmp_path: Path) -> None:
    # A local config file whose path fields (scenarios/appPath/etc) climb out of `--root` itself is
    # rejected at bind, so serve's scenario/build resolution never sees a host path outside the
    # browse root (matches the confinement applied to Git and uploaded configs). `secret` sits
    # outside `root` entirely — not merely outside the config's own subdirectory — so this exercises
    # a genuine `--root` escape, not an in-root sibling reference (see the sibling test below).
    root = tmp_path / "root"
    config_dir = root / "configs"
    config_dir.mkdir(parents=True)
    secret = tmp_path / "secret"  # outside `root`
    secret.mkdir()
    local_cfg = config_dir / "bajutsu.common.config.yaml"
    local_cfg.write_text(
        "targets:\n  evil: { bundleId: com.example.evil, scenarios: ../../secret }\n",
        encoding="utf-8",
    )
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=root, cwd=tmp_path))
    try:
        status, resp = _post(port, "/api/config", {"path": str(local_cfg)})
        assert status == 400 and "config path validation failed" in resp["error"]
        # Verify the config was not bound due to the invalid path.
        assert _get_json(port, "/api/config")["hasConfig"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_http_local_config_sibling_within_root_is_accepted(tmp_path: Path) -> None:
    # A config nested in a subdirectory of `--root` may still reference a sibling tree elsewhere
    # under `--root` via `../` — the path never leaves the browse root, so it must bind exactly as
    # the CLI's unconfined `--config` would resolve it, not be rejected as if it were an escape.
    root = tmp_path / "root"
    config_dir = root / "configs"
    config_dir.mkdir(parents=True)
    scenarios_dir = root / "scenarios"  # a sibling of `configs/`, still inside `root`
    scenarios_dir.mkdir()
    (scenarios_dir / "smoke.yaml").write_text(
        "- name: smoke\n  steps:\n    - tap: { id: x }\n", encoding="utf-8"
    )
    local_cfg = config_dir / "bajutsu.common.config.yaml"
    local_cfg.write_text(
        "targets:\n  demo: { bundleId: com.example.demo, scenarios: ../scenarios }\n",
        encoding="utf-8",
    )
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=root, cwd=tmp_path))
    try:
        status, resp = _post(port, "/api/config", {"path": str(local_cfg)})
        assert status == 200 and resp["ok"] is True and resp["targets"] == ["demo"]
        assert _get_json(port, "/api/config")["hasConfig"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_sources_local_offers_all_three(tmp_path: Path) -> None:
    # The local backend offers every config source, including the file browser, and surfaces the
    # browse root the fs source needs (BE-0108).
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        info = _get_json(port, "/api/config")
        assert info["configSources"] == ["git", "upload", "fs"]
        assert info["root"] == str(tmp_path.resolve())
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_sources_hosted_omits_fs_and_root(tmp_path: Path) -> None:
    # A hosted deployment (server backend) drops the file browser: the remote user has no
    # filesystem relationship to the host, so only Git and upload are offered — and the browse root,
    # dead information without the fs source, is withheld rather than leaking the host path (BE-0108).
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path, hosted=True))
    try:
        info = _get_json(port, "/api/config")
        assert info["configSources"] == ["git", "upload"]
        assert info["root"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_http_hosted_refuses_path_bind_but_git_still_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Server-side enforcement (defense in depth): when hosted, the path branch of POST /api/config is
    # refused even by a hand-crafted request, while the Git branch is unaffected (BE-0108).
    import bajutsu.serve.operations.config as ops
    from bajutsu.common.config_source import Materialized

    _, _, runs = project(tmp_path)
    checkout = tmp_path / "gitsrc"
    checkout.mkdir()
    git_cfg = checkout / "bajutsu.common.config.yaml"
    git_cfg.write_text("targets:\n  fromgit: { bundleId: com.example.fromgit }\n", encoding="utf-8")
    monkeypatch.setattr(
        ops, "materialize", lambda spec, **kw: Materialized(git_cfg, checkout, "sha")
    )
    state = srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path, hosted=True)
    server, port = _serve(state)
    try:
        status, resp = _post(port, "/api/config", {"path": "bajutsu.common.config.yaml"})
        assert status == 403 and "file browser is disabled" in resp["error"]
        assert state.config is None  # the refused path bind changed nothing
        # The Git branch still binds — hosting keeps the two remote-usable sources.
        status, resp = _post(port, "/api/config", {"git": "github:acme/repo@main"})
        assert status == 200 and resp["ok"] is True and resp["targets"] == ["fromgit"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenarios_by_app_from_config(tmp_path: Path) -> None:
    _, cfg, runs = project(tmp_path)
    # Config-driven (no --scenarios override): the dir comes from the selected app.
    server, port = _serve(srv.ServeState(runs_dir=runs, config=cfg, root=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/scenarios?target=demo")[0]["names"] == ["alpha", "beta"]
        assert _get_json(port, "/api/scenarios?target=other") == []  # app has no scenarios dir
        assert _get_json(port, "/api/scenarios") == []  # no app → nothing
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_capabilities_local_offers_capture(tmp_path: Path) -> None:
    # The local backend serves every `/api/capture/*` route, so the boot read says Capture is
    # available and the Author tab offers the mode (#1721).
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        capture = _get_json(port, "/api/config")["capabilities"]["capture"]
        assert capture == {"available": True, "reason": None}
    finally:
        server.shutdown()
        server.server_close()


def test_http_config_capabilities_hosted_withholds_capture_with_a_reason(tmp_path: Path) -> None:
    # Every `/api/capture/*` route is `local_only`, so a hosted deployment would 404 the mode's
    # first call. The flag says so up front, and carries the reason the disabled control shows —
    # the UI must be able to explain the absence, not just grey a button out (#1721).
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path, hosted=True))
    try:
        capture = _get_json(port, "/api/config")["capabilities"]["capture"]
        assert capture["available"] is False
        assert capture["reason"] == config_ops.CAPTURE_HOSTED_REASON
    finally:
        server.shutdown()
        server.server_close()


def test_capabilities_withhold_capture_where_the_transport_serves_no_local_route(
    tmp_path: Path,
) -> None:
    # `serve --asgi` runs the FastAPI transport with the *local* backend: `hosted` stays False, but
    # `make_app` registers no `local_only` route, so every capture call 404s there too. Keyed to
    # `hosted` the flag would report capture as available on a server that cannot serve it — the
    # exact mismatch the issue exists to remove. The reason names the transport, since a local
    # deployment's devices are right there (#1721).
    asgi = srv.ServeState(
        runs_dir=tmp_path / "runs", root=tmp_path, cwd=tmp_path, serves_local_routes=False
    )
    assert config_ops.serve_capabilities(asgi)["capture"] == {
        "available": False,
        "reason": config_ops.CAPTURE_NO_LOCAL_ROUTES_REASON,
    }
    # A hosted deployment reaches the same transport, and is still reported as hosted: there the
    # devices really are elsewhere, so naming `--asgi` would send the reader after the wrong cause.
    hosted = srv.ServeState(
        runs_dir=tmp_path / "runs",
        root=tmp_path,
        cwd=tmp_path,
        hosted=True,
        serves_local_routes=False,
    )
    assert config_ops.serve_capabilities(hosted)["capture"]["reason"] == (
        config_ops.CAPTURE_HOSTED_REASON
    )


def test_http_config_capabilities_withhold_orgs_without_a_database(tmp_path: Path) -> None:
    # A database-less serve is single-user by construction and has no tenant to administer, so
    # `GET /api/orgs` can only answer 400. The flag reports it with the endpoint's own wording, so
    # the UI never makes the call it would lose (#1721).
    _, _, runs = project(tmp_path)
    server, port = _serve(srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path))
    try:
        orgs = _get_json(port, "/api/config")["capabilities"]["orgs"]
        assert orgs["available"] is False
        assert orgs["reason"] == org_ops.NO_ORG_STORE_ERROR
    finally:
        server.shutdown()
        server.server_close()


def test_capabilities_track_the_role_gate_on_the_orgs_roster(
    tmp_path: Path, serve_engine: Callable[..., Engine]
) -> None:
    # With a database wired, the roster is admin-only — the flag has to follow the caller, not just
    # the deployment, or a viewer would be offered a tab whose every load 403s. Read through the
    # same gate the transport applies, so the two cannot disagree (#1721).
    from bajutsu.serve.operations.config import ORGS_ADMIN_ONLY_REASON, serve_capabilities
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine(foreign_keys=True)
    Base.metadata.create_all(engine)
    repository = SqlRepository(engine)
    repository.ensure_org("acme", slug="acme", name="Acme")
    repository.upsert_user("root", org_id="acme", github_login="root", email="root@x", role="admin")
    repository.upsert_user(
        "view", org_id="acme", github_login="view", email="view@x", role="viewer"
    )
    state = srv.ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path, repository=repository)

    assert serve_capabilities(state, "root")["orgs"] == {"available": True, "reason": None}
    viewer = serve_capabilities(state, "view")["orgs"]
    assert viewer == {"available": False, "reason": ORGS_ADMIN_ONLY_REASON}
    # No identity at all (a shared-token session) leaves no role to fail, exactly as the transport
    # gate has it: it applies only to an OAuth-authenticated session.
    assert serve_capabilities(state, None)["orgs"]["available"] is True
