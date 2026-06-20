"""Tests for the CLI error/loading paths (the device paths need a Simulator).

The sandbox has no idb on PATH, so backend selection fails cleanly with exit code
2 — which lets us drive run/doctor right up to the device boundary.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bajutsu.cli import app

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


def test_doctor_unknown_app(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["doctor", "--app", "ghost", "--config", str(cfg)])
    assert r.exit_code == 2


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
