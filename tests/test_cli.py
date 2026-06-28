"""Tests for the CLI error/loading paths (the device paths need a Simulator).

The sandbox has no idb on PATH, so backend selection fails cleanly with exit code
2 — which lets us drive run/doctor right up to the device boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli._shared import _resolve_browser
from bajutsu.cli.commands.crawl import _ai_credential_gap
from bajutsu.config import Effective, load_config, resolve

runner = CliRunner()

SCENARIO = """
- name: demo
  steps:
    - tap: { id: home.title }
"""


def _write(tmp_path: Path) -> tuple[Path, Path]:
    """Lay out a config + scenarios under tmp_path and return (config, standalone scenario).

    Three apps exercise the config-driven `run`/`record` paths: `demo` has a scenarios dir
    with one file, `bare` has none, `empty` points at an empty dir. The scenarios paths are
    absolute so the config resolves them regardless of the test process's cwd."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "demo.yaml").write_text(SCENARIO, encoding="utf-8")
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [idb] }\n"
        "targets:\n"
        f"  demo: {{ bundleId: com.example.demo, idNamespaces: [home], scenarios: {scn_dir} }}\n"
        "  bare: { bundleId: com.example.bare, idNamespaces: [home] }\n"
        f"  empty: {{ bundleId: com.example.empty, idNamespaces: [home], scenarios: {empty_dir} }}\n",
        encoding="utf-8",
    )
    scn = tmp_path / "s.yaml"
    scn.write_text(SCENARIO, encoding="utf-8")
    return cfg, scn


def _argv(
    command: str, *, cfg: Path, scn: Path, out: Path, app: str, backend: str = ""
) -> list[str]:
    """The argv for *command* against *app*, carrying each command's own required flags. Used by the
    error-path tests that only differ by command (unknown target / no available backend)."""
    base = {
        "run": ["run", "--scenario", str(scn), "--target", app],
        "record": ["record", "--out", str(out), "--target", app, "--goal", "x"],
        "doctor": ["doctor", "--target", app],
        "crawl": ["crawl", "--target", app],
    }[command]
    if backend:
        base += ["--backend", backend]
    return [*base, "--config", str(cfg)]


@pytest.mark.parametrize("command", ["run", "record", "doctor", "crawl"])
def test_unknown_app_exits_cleanly(tmp_path: Path, command: str) -> None:
    cfg, scn = _write(tmp_path)
    r = runner.invoke(app, _argv(command, cfg=cfg, scn=scn, out=tmp_path / "rec.yaml", app="ghost"))
    assert r.exit_code == 2
    assert "unknown target" in r.output


# BE-0047 fail-closed: an AI entry point with no usable credential exits 2 with a clear, provider-
# specific message and never constructs an SDK client that would fall back to a hosted default.


def test_record_fails_closed_without_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)  # no .env key leak-in
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # If a client were ever constructed it would fail loudly here, proving it never is.
    monkeypatch.setattr(
        "anthropic.Anthropic",
        lambda *a, **k: pytest.fail("client constructed despite missing credential"),
    )
    cfg, _ = _write(tmp_path)
    r = runner.invoke(
        app,
        [
            "record",
            "--out",
            str(tmp_path / "rec.yaml"),
            "--target",
            "demo",
            "--goal",
            "x",
            "--no-dismiss-alerts",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "no AI credential" in r.output and "ANTHROPIC_API_KEY" in r.output


def test_record_fails_closed_uses_configured_key_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The message names the env var from ai.keyEnv, not the default — the user's configured source.
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults:\n  ai: { keyEnv: MY_GATEWAY_KEY }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        app,
        [
            "record",
            "--out",
            str(tmp_path / "rec.yaml"),
            "--target",
            "demo",
            "--goal",
            "x",
            "--no-dismiss-alerts",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "MY_GATEWAY_KEY" in r.output


def test_triage_ai_fails_closed_without_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "anthropic.Anthropic",
        lambda *a, **k: pytest.fail("client constructed despite missing credential"),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        '{"scenarios": [{"scenario": "s", "ok": false, "failure": "boom", "steps": []}]}',
        encoding="utf-8",
    )
    r = runner.invoke(app, ["triage", str(run_dir), "--ai"])
    assert r.exit_code == 2
    assert "no AI credential" in r.output


def test_legacy_app_flag_is_rejected(tmp_path: Path) -> None:
    # Hard cutover (BE-0057): there is no `--app` alias — the old flag exits 2 (unknown option).
    cfg, scn = _write(tmp_path)
    r = runner.invoke(app, ["run", "--scenario", str(scn), "--app", "sample", "--config", str(cfg)])
    assert r.exit_code == 2


@pytest.mark.parametrize("command", ["run", "record", "doctor"])
def test_no_backend_available_exits_cleanly(
    tmp_path: Path, command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unknown backend is never available -> clean exit 2, independent of PATH. (crawl has its own
    # test below: it additionally checks the run dir isn't created by the gate.)
    # record/dismiss-alerts now fail closed first (BE-0047), so give it a credential to reach the
    # backend gate this test exercises.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg, scn = _write(tmp_path)
    r = runner.invoke(
        app, _argv(command, cfg=cfg, scn=scn, out=tmp_path / "rec.yaml", app="demo", backend="nope")
    )
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_run_missing_config(tmp_path: Path) -> None:
    r = runner.invoke(app, ["run", "--target", "demo", "--config", str(tmp_path / "nope.yaml")])
    assert r.exit_code == 2
    assert "config not found" in r.output


def test_run_missing_scenario(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            str(tmp_path / "missing.yaml"),
            "--target",
            "demo",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "scenario not found" in r.output


def test_run_reads_configured_dir(tmp_path: Path) -> None:
    # No --scenario: run loads the app's configured scenarios dir, then hits the backend gate.
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", "--target", "demo", "--backend", "nope", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_run_no_scenarios_dir(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", "--target", "bare", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no scenarios dir" in r.output


def test_run_empty_scenarios_dir(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", "--target", "empty", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no scenarios found" in r.output


def test_record_no_scenarios_dir(tmp_path: Path) -> None:
    # No --out and the app has no scenarios dir -> can't decide where to write.
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["record", "--target", "bare", "--goal", "x", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no scenarios dir" in r.output


def _web_eff(browser: str) -> Effective:
    cfg = load_config(f"targets: {{ web: {{ baseUrl: 'http://x/', browser: {browser} }} }}")
    return resolve(cfg, "web")


def test_resolve_browser_flag_overrides_config() -> None:
    # Precedence (BE-0076): an explicit --browser flag wins over the target's config.
    eff = _web_eff("firefox")  # config says firefox
    assert _resolve_browser(eff, "webkit").browser == "webkit"  # flag wins


def test_resolve_browser_empty_flag_keeps_config() -> None:
    # No flag: the resolved config value (here firefox) stands.
    assert _resolve_browser(_web_eff("firefox"), "").browser == "firefox"


def test_resolve_browser_default_is_chromium() -> None:
    # No flag and no config: chromium, today's behaviour.
    eff = resolve(load_config("targets: { web: { baseUrl: 'http://x/' } }"), "web")
    assert _resolve_browser(eff, "").browser == "chromium"


@pytest.mark.parametrize("command", ["run", "record"])
def test_unknown_browser_engine_exits_cleanly(tmp_path: Path, command: str) -> None:
    # An unknown --browser engine exits 2 before reaching Playwright (BE-0076), with a usable hint.
    cfg, scn = _write(tmp_path)
    argv = _argv(command, cfg=cfg, scn=scn, out=tmp_path / "rec.yaml", app="demo")
    r = runner.invoke(app, [*argv, "--browser", "safari"])
    assert r.exit_code == 2
    assert "unknown --browser" in r.output


def test_parse_browsers_dedupes_and_validates() -> None:
    # --browsers parses a comma list, trims/drops blanks, and de-dupes while keeping order (BE-0076).
    from bajutsu.cli.commands.run import _parse_browsers

    assert _parse_browsers("chromium, firefox ,webkit") == ["chromium", "firefox", "webkit"]
    assert _parse_browsers("chromium,chromium") == ["chromium"]  # de-duped
    assert _parse_browsers("") == []  # absent → no matrix


def test_parse_browsers_rejects_unknown_engine() -> None:
    import typer

    from bajutsu.cli.commands.run import _parse_browsers

    with pytest.raises(typer.Exit) as exc:
        _parse_browsers("chromium,safari")
    assert exc.value.exit_code == 2


def test_browsers_unknown_engine_exits_cleanly(tmp_path: Path) -> None:
    # The matrix flag validates the same way --browser does: an unknown engine exits 2 up front.
    cfg, scn = _write(tmp_path)
    argv = _argv("run", cfg=cfg, scn=scn, out=tmp_path / "rec.yaml", app="demo")
    r = runner.invoke(app, [*argv, "--browsers", "chromium,safari"])
    assert r.exit_code == 2
    assert "safari" in r.output


def test_doctor_web_target_requires_base_url() -> None:
    # Forcing the web backend on a target with no baseUrl (e.g. an iOS target) exits cleanly (2)
    # with a fixable message, rather than constructing a browser with nowhere to navigate.
    import typer

    from bajutsu.cli.commands.doctor import _current_screen
    from bajutsu.config import Effective
    from bajutsu.scenario import Redact

    eff = Effective(
        target="web",
        bundle_id="com.example.demo",  # iOS-shaped target: no baseUrl
        deeplink_scheme=None,
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
    with pytest.raises(typer.Exit) as exc:
        _current_screen("playwright", "booted", eff)
    assert exc.value.exit_code == 2


def test_serve_refuses_non_loopback_without_token() -> None:
    # Binding a non-loopback host with no token would expose an unauthenticated server (BE-0051).
    r = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2
    assert "without a token" in r.output


def test_serve_emit_launchagent_prints_plist_and_exits() -> None:
    # --emit-launchagent prints a plist and exits 0 without binding a server (BE-0016 Tier A).
    r = runner.invoke(app, ["serve", "--emit-launchagent", "--config", "bajutsu.config.yaml"])
    assert r.exit_code == 0
    assert "<plist" in r.output
    assert "com.bajutsu.serve" in r.output
    assert "bajutsu" in r.output and "serve" in r.output


def test_serve_config_from_git_binds_checkout(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # `serve --config github:…` materializes the checkout at startup and serves from its root, so
    # the config's relative paths resolve against the fetched tree (BE-0063).
    import bajutsu.config_source as cs
    import bajutsu.serve as srv

    checkout = tmp_path / "co"
    checkout.mkdir()
    cfg = checkout / "bajutsu.config.yaml"
    cfg.write_text("targets: { demo: { bundleId: com.example.demo } }\n", encoding="utf-8")
    monkeypatch.setattr(
        cs, "materialize", lambda spec, **kw: cs.Materialized(cfg, checkout, "sha1")
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: captured.update(kw))  # don't start a server
    r = runner.invoke(app, ["serve", "--config", "github:acme/repo@main"])
    assert r.exit_code == 0
    assert captured["config"] == cfg  # bound to the checkout's config
    assert captured["cwd"] == checkout  # served from the checkout root


def test_serve_rejects_invalid_upload_exec() -> None:
    # An unknown --upload-exec mode fails loud at the boundary (BE-0090), never silently defaults.
    r = runner.invoke(app, ["serve", "--upload-exec", "bogus", "--config", "bajutsu.config.yaml"])
    assert r.exit_code == 2
    assert "upload-exec" in r.output


def test_serve_upload_exec_env_mirror_and_flag_precedence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The flag wins; absent a flag the BAJUTSU_UPLOAD_EXEC env var is honoured (hosted backend).
    import bajutsu.serve as srv

    captured: dict[str, object] = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: captured.update(kw))
    monkeypatch.setenv("BAJUTSU_UPLOAD_EXEC", "deny")
    r = runner.invoke(app, ["serve", "--config", "bajutsu.config.yaml"])
    assert r.exit_code == 0 and captured["upload_exec"] == "deny"  # env honoured when no flag
    captured.clear()
    r = runner.invoke(app, ["serve", "--upload-exec", "reuse", "--config", "bajutsu.config.yaml"])
    assert r.exit_code == 0 and captured["upload_exec"] == "reuse"  # flag wins over env


def test_serve_loopback_detection() -> None:
    from bajutsu.cli.commands.serve import _is_loopback

    assert _is_loopback("127.0.0.1")
    assert _is_loopback("127.0.0.2")  # the whole 127/8 block is loopback
    assert _is_loopback("localhost")
    assert _is_loopback("::1")
    assert _is_loopback("0:0:0:0:0:0:0:1")  # fully-expanded ::1
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("192.168.1.10")
    assert not _is_loopback("example.com")  # a non-IP literal (no DNS lookup) -> non-loopback


def test_crawl_no_backend_available(tmp_path: Path) -> None:
    # The backend gate runs before any device work, so an unknown backend exits 2 cleanly and,
    # crucially, before the run dir is created (no stray runs/ side effect from the gate).
    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        ["crawl", "--target", "demo", "--backend", "nope", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert "no available actuator" in r.output
    assert not out.exists()


def test_crawl_unknown_agent(tmp_path: Path) -> None:
    # An invalid --agent is rejected before any device work (clean exit 2).
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["crawl", "--target", "demo", "--agent", "bad", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "unknown --agent" in r.output


def test_crawl_agent_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # With no --agent, crawl resolves the kind from $BAJUTSU_AGENT (set by serve's Settings
    # selector), mirroring record. An invalid env value surfaces the same validation error, which
    # proves the env is consulted — and it fails before any device work.
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("BAJUTSU_AGENT", "bad")
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["crawl", "--target", "demo", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "unknown --agent 'bad'" in r.output


# --- crawl AI-provider credential gate (BE-0053: crawl is a Tier-1 Bedrock path) ----------------
# `--agent api` reaches Claude through the configured provider, so the credential it needs depends
# on the provider: ANTHROPIC_API_KEY for Anthropic, a provider-prefixed BAJUTSU_BEDROCK_MODEL for
# Bedrock (AWS credentials authenticate there, not an Anthropic key). `claude-code` brings its own.


def test_ai_credential_gap_anthropic_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _ai_credential_gap("api") == "anthropic-key"


def test_ai_credential_gap_anthropic_with_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert _ai_credential_gap("api") is None


def test_ai_credential_gap_bedrock_does_not_need_anthropic_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fix: on Bedrock, --agent api authenticates via AWS credentials + a Bedrock model id, so a
    # missing ANTHROPIC_API_KEY must NOT block the crawl (it did before — record never gated on it).
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("BAJUTSU_BEDROCK_MODEL", "global.anthropic.claude-opus-4-6-v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _ai_credential_gap("api") is None


def test_ai_credential_gap_bedrock_needs_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.delenv("BAJUTSU_BEDROCK_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _ai_credential_gap("api") == "bedrock-model"


def test_ai_credential_gap_claude_code_brings_own_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    # claude-code uses its own subscription auth, so it never needs provider credentials.
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _ai_credential_gap("claude-code") is None


def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the credential-gate CLI tests hermetic: stub the @app.callback .env load so a
    developer's local .env can't re-inject ANTHROPIC_API_KEY / a provider, and clear those vars.
    BAJUTSU_AGENT is cleared too — crawl now resolves a blank --agent from it, so a leaked
    claude-code would skip the Anthropic-key gate these tests assert on."""
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    for var in (
        "ANTHROPIC_API_KEY",
        "BAJUTSU_AI_PROVIDER",
        "BAJUTSU_BEDROCK_MODEL",
        "BAJUTSU_AGENT",
    ):
        monkeypatch.delenv(var, raising=False)


def test_crawl_api_agent_needs_anthropic_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On the Anthropic provider, --agent api still requires ANTHROPIC_API_KEY. The gate fires after
    # backend selection (the `fake` actuator is always available) and before any device work, so the
    # run dir is not created.
    _no_dotenv(monkeypatch)
    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        ["crawl", "--target", "demo", "--backend", "fake", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert "ANTHROPIC_API_KEY" in r.output
    assert not out.exists()


def test_crawl_bedrock_does_not_require_anthropic_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Bedrock fix end-to-end: with the provider set to bedrock + a model id, the crawl passes
    # the credential gate without ANTHROPIC_API_KEY. The device boundary is mocked so the test needs
    # no Simulator — reaching it (the DeviceError below) proves the gate didn't block the crawl.
    import bajutsu.env as _benv

    _no_dotenv(monkeypatch)
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("BAJUTSU_BEDROCK_MODEL", "global.anthropic.claude-opus-4-6-v1")
    monkeypatch.setattr("bajutsu.env.resolve_udid", lambda u, run=None: "booted")

    def _no_device(*_args: object, **_kwargs: object) -> object:
        raise _benv.DeviceError("device boundary reached (no Simulator in test)")

    monkeypatch.setattr("bajutsu.cli.commands.crawl.launch_driver", _no_device)
    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        ["crawl", "--target", "demo", "--backend", "fake", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert (
        "device boundary reached" in r.output
    )  # passed the credential gate, reached device launch
    assert "ANTHROPIC_API_KEY" not in r.output


def test_crawl_empty_udid_pool_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty --udid resolves to no device, so the crawl fails loudly (exit 2) with a fixable message
    # instead of crashing later on the first lane (BE-0009 Slice 3 review). Bedrock + model passes the
    # credential gate, so the run reaches the lane-planning guard before any device work.
    _no_dotenv(monkeypatch)
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("BAJUTSU_BEDROCK_MODEL", "global.anthropic.claude-opus-4-6-v1")
    cfg, _ = _write(tmp_path)
    r = runner.invoke(
        app,
        [
            "crawl",
            "--target",
            "demo",
            "--backend",
            "fake",
            "--udid",
            "",
            "--out",
            str(tmp_path / "crawlrun"),
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "empty pool" in r.output


def test_crawl_bedrock_needs_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # On Bedrock the gate asks for BAJUTSU_BEDROCK_MODEL (the bare Anthropic id is not a valid
    # Bedrock model id), not ANTHROPIC_API_KEY.
    _no_dotenv(monkeypatch)
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        ["crawl", "--target", "demo", "--backend", "fake", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert "BAJUTSU_BEDROCK_MODEL" in r.output
    assert "ANTHROPIC_API_KEY" not in r.output
    assert not out.exists()


def test_crawl_web_builds_one_browser_lane_per_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Web has no devices, so `--workers N` launches N browser-process lanes that share one screen
    map (BE-0077) — not the old single-lane pin — and wires the browser-relaunch `recover` hook. The
    browser launch, target server, and crawl engine are mocked so no Chromium is needed."""
    import bajutsu.crawl as crawl_engine

    _no_dotenv(monkeypatch)
    monkeypatch.setattr("bajutsu.cli.commands.crawl.ensure_web_runtime", lambda *a, **k: None)
    monkeypatch.setattr("bajutsu.cli.commands.crawl.select_actuator", lambda *a, **k: "playwright")
    monkeypatch.setattr(
        "bajutsu.cli.commands.crawl.start_launch_server", lambda *a, **k: ((lambda: None), None)
    )

    launched = {"n": 0}

    def fake_launch(*_a: object, **_k: object) -> object:
        launched["n"] += 1
        return object()  # the engine is mocked, so no driver method is ever called

    monkeypatch.setattr("bajutsu.cli.commands.crawl.launch_driver", fake_launch)

    captured: dict[str, object] = {}

    def fake_crawl(driver: object, reset: object, **kwargs: object) -> crawl_engine.ScreenMap:
        captured.update(kwargs)
        return crawl_engine.ScreenMap()

    monkeypatch.setattr(crawl_engine, "crawl", fake_crawl)

    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        [
            "crawl",
            "--target",
            "demo",
            "--backend",
            "web",
            "--workers",
            "3",
            "--agent",
            "claude-code",
            "--out",
            str(out),
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 0, r.output
    # Only the primary lane is built eagerly (on the main thread, for bootstrap); the other two are
    # factories the engine calls on each worker's own thread (BE-0077: a Playwright browser must be
    # created on the thread that drives it).
    assert launched["n"] == 1
    extra = captured["extra_workers"]
    assert isinstance(extra, list) and len(extra) == 2  # primary + 2 extra-worker factories = 3
    # Each factory builds a real browser lane when invoked — three lanes in total, not pinned to one.
    for make_lane in extra:
        make_lane()
    assert launched["n"] == 3
    assert captured["recover"] is not None  # the browser-relaunch recover hook is wired for web


def _write_visual_run(runs: Path, run_id: str, *, ok: bool) -> Path:
    import json

    run_dir = runs / run_id
    (run_dir / "00-home").mkdir(parents=True)
    (run_dir / "00-home" / "visual-actual.png").write_bytes(b"PNGDATA")
    manifest = {
        "runId": run_id,
        "ok": ok,
        "scenarios": [
            {
                "scenario": "home",
                "ok": ok,
                "expect_results": [
                    {
                        "ok": ok,
                        "kind": "visual",
                        "detail": "visual ≈ home.png",
                        "reason": "" if ok else "baseline not found: home.png",
                        "visual": {
                            "baseline_name": "home.png",
                            "actual": "00-home/visual-actual.png",
                            "baseline": None,
                            "diff": None,
                            "diff_pct": None,
                            "missing": not ok,
                        },
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


def test_approve_promotes_failing_visual(tmp_path: Path) -> None:
    run_dir = _write_visual_run(tmp_path / "runs", "20260610-1", ok=False)
    baselines = tmp_path / "baselines"
    r = runner.invoke(app, ["approve", str(run_dir), "--baselines", str(baselines)])
    assert r.exit_code == 0
    assert (baselines / "home.png").read_bytes() == b"PNGDATA"
    assert "approved home.png" in r.output


def test_approve_skips_passing_without_all(tmp_path: Path) -> None:
    run_dir = _write_visual_run(tmp_path / "runs", "20260610-1", ok=True)
    baselines = tmp_path / "baselines"
    r = runner.invoke(app, ["approve", str(run_dir), "--baselines", str(baselines)])
    assert r.exit_code == 1  # nothing to approve (the check passed)
    assert not (baselines / "home.png").exists()
    # --all refreshes the passing baseline too.
    r = runner.invoke(app, ["approve", str(run_dir), "--baselines", str(baselines), "--all"])
    assert r.exit_code == 0
    assert (baselines / "home.png").read_bytes() == b"PNGDATA"


def test_approve_no_run_found(tmp_path: Path) -> None:
    r = runner.invoke(app, ["approve", "--baselines", str(tmp_path / "b"), "--runs", str(tmp_path)])
    assert r.exit_code == 2
    assert "no run found" in r.output


def test_serve_server_backend_without_extras_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    # `serve --backend=server` needs the optional extras; with one missing (redis stood in here) it
    # must print an install hint and exit 2 — never a raw ImportError traceback.
    import sys

    monkeypatch.setitem(sys.modules, "redis", None)
    r = runner.invoke(app, ["serve", "--backend", "server"])
    assert r.exit_code == 2
    assert "extra" in r.output.lower()


def test_worker_without_extra_exits_cleanly() -> None:
    # On the gate the `worker` extra (redis/rq) isn't installed, so `bajutsu worker` must fail with
    # a clear "install the extra" message and exit 2 — never a raw ImportError traceback.
    import importlib.util

    # Skip only when the whole extra is present — `worker` imports both redis and rq, so with
    # either missing it still hits the intended "install the extra" path (exit 2).
    if importlib.util.find_spec("rq") is not None and importlib.util.find_spec("redis") is not None:
        pytest.skip("the worker extra is installed; the Redis-connect path isn't gate-testable")
    r = runner.invoke(app, ["worker"])
    assert r.exit_code == 2
    assert "worker" in r.output.lower() and "extra" in r.output.lower()
