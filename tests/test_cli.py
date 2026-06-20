"""Tests for the CLI error/loading paths (the device paths need a Simulator).

The sandbox has no idb on PATH, so backend selection fails cleanly with exit code
2 — which lets us drive run/doctor right up to the device boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli.commands.crawl import _ai_credential_gap

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
        "apps:\n"
        f"  demo: {{ bundleId: com.example.demo, idNamespaces: [home], scenarios: {scn_dir} }}\n"
        "  bare: { bundleId: com.example.bare, idNamespaces: [home] }\n"
        f"  empty: {{ bundleId: com.example.empty, idNamespaces: [home], scenarios: {empty_dir} }}\n",
        encoding="utf-8",
    )
    scn = tmp_path / "s.yaml"
    scn.write_text(SCENARIO, encoding="utf-8")
    return cfg, scn


def test_run_missing_config(tmp_path: Path) -> None:
    r = runner.invoke(app, ["run", "--app", "demo", "--config", str(tmp_path / "nope.yaml")])
    assert r.exit_code == 2
    assert "config not found" in r.output


def test_run_unknown_app(tmp_path: Path) -> None:
    cfg, scn = _write(tmp_path)
    r = runner.invoke(app, ["run", "--scenario", str(scn), "--app", "ghost", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "unknown app" in r.output


def test_run_missing_scenario(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            str(tmp_path / "missing.yaml"),
            "--app",
            "demo",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "scenario not found" in r.output


def test_run_no_backend_available(tmp_path: Path) -> None:
    # An unknown backend is never available -> clean exit 2 (independent of PATH).
    cfg, scn = _write(tmp_path)
    r = runner.invoke(
        app,
        ["run", "--scenario", str(scn), "--app", "demo", "--backend", "nope", "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_run_reads_configured_dir(tmp_path: Path) -> None:
    # No --scenario: run loads the app's configured scenarios dir, then hits the backend gate.
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", "--app", "demo", "--backend", "nope", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_run_no_scenarios_dir(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", "--app", "bare", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no scenarios dir" in r.output


def test_run_empty_scenarios_dir(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", "--app", "empty", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no scenarios found" in r.output


def test_doctor_no_backend_available(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["doctor", "--app", "demo", "--backend", "nope", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_record_no_backend_available(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    out = tmp_path / "rec.yaml"
    r = runner.invoke(
        app,
        [
            "record",
            "--out",
            str(out),
            "--app",
            "demo",
            "--goal",
            "open settings",
            "--backend",
            "nope",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_record_no_scenarios_dir(tmp_path: Path) -> None:
    # No --out and the app has no scenarios dir -> can't decide where to write.
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["record", "--app", "bare", "--goal", "x", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no scenarios dir" in r.output


def test_record_unknown_app(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(
        app,
        [
            "record",
            "--out",
            str(tmp_path / "rec.yaml"),
            "--app",
            "ghost",
            "--goal",
            "x",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "unknown app" in r.output


def test_doctor_unknown_app(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["doctor", "--app", "ghost", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "unknown app" in r.output


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
        ["crawl", "--app", "demo", "--backend", "nope", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert "no available actuator" in r.output
    assert not out.exists()


def test_crawl_unknown_app(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["crawl", "--app", "ghost", "--config", str(cfg)])
    assert r.exit_code == 2


def test_crawl_unknown_agent(tmp_path: Path) -> None:
    # An invalid --agent is rejected before any device work (clean exit 2).
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["crawl", "--app", "demo", "--agent", "bad", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "unknown --agent" in r.output


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
    developer's local .env can't re-inject ANTHROPIC_API_KEY / a provider, and clear those vars."""
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    for var in ("ANTHROPIC_API_KEY", "BAJUTSU_AI_PROVIDER", "BAJUTSU_BEDROCK_MODEL"):
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
        ["crawl", "--app", "demo", "--backend", "fake", "--out", str(out), "--config", str(cfg)],
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
    monkeypatch.setattr("bajutsu.env.resolve_udid", lambda u: "booted")

    def _no_device(*_args: object, **_kwargs: object) -> object:
        raise _benv.DeviceError("device boundary reached (no Simulator in test)")

    monkeypatch.setattr("bajutsu.cli.commands.crawl.launch_driver", _no_device)
    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        ["crawl", "--app", "demo", "--backend", "fake", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert (
        "device boundary reached" in r.output
    )  # passed the credential gate, reached device launch
    assert "ANTHROPIC_API_KEY" not in r.output


def test_crawl_bedrock_needs_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # On Bedrock the gate asks for BAJUTSU_BEDROCK_MODEL (the bare Anthropic id is not a valid
    # Bedrock model id), not ANTHROPIC_API_KEY.
    _no_dotenv(monkeypatch)
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    cfg, _ = _write(tmp_path)
    out = tmp_path / "crawlrun"
    r = runner.invoke(
        app,
        ["crawl", "--app", "demo", "--backend", "fake", "--out", str(out), "--config", str(cfg)],
    )
    assert r.exit_code == 2
    assert "BAJUTSU_BEDROCK_MODEL" in r.output
    assert "ANTHROPIC_API_KEY" not in r.output
    assert not out.exists()


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
