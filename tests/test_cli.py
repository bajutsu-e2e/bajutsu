"""Tests for the CLI error/loading paths (the device paths need a Simulator).

These exercise the config / target / credential error paths, which exit cleanly (code 2) before any
device work — so they run anywhere, with or without Xcode. The one backend-availability test forces
an unknown backend explicitly rather than leaning on the host's toolchain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli._shared import _resolve_browser, _resolve_language
from bajutsu.config import Effective, IosConfig, WebConfig, load_config, resolve

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
        "defaults: { backend: [ios] }\n"
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


def test_credential_gap_messages_for_ant_are_actionable() -> None:
    # The ant provider's fail-closed messages (BE-0163) name the fix, mirroring bedrock's.
    from bajutsu.agents.anthropic_client import ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED
    from bajutsu.cli._shared import _credential_gap_message
    from bajutsu.config import load_config, resolve

    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    assert "ant auth login" in _credential_gap_message(ANT_CLI_MISSING, eff)
    assert "ant auth login" in _credential_gap_message(ANT_CLI_UNAUTHENTICATED, eff)


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


def test_record_exposes_token_budget_flags() -> None:
    # BE-0194 §3: the record CLI surfaces the loop's max_steps / with_screenshot knobs. Introspect
    # the command's declared options rather than the rendered `--help` text — Rich formats help in an
    # environment-dependent way (terminal width, TTY detection), so its output is not a stable
    # substring to assert on.
    import typer.main

    record_cmd = typer.main.get_command(app).commands["record"]  # type: ignore[attr-defined]
    flags = {opt for p in record_cmd.params for opt in (*p.opts, *p.secondary_opts)}
    assert "--max-steps" in flags
    assert "--no-screenshot" in flags


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


def _fake_run(tmp_path: Path, *, tags: str = "") -> tuple[Path, Path]:
    """A fake-backend config + a one-step scenario, for driving `run` through to its verdict
    device-free. The tap targets an element absent from the fake's (empty) screen, so the run
    reaches a deterministic FAIL — enough to exercise the whole command body up to the verdict."""
    scn = tmp_path / "s.yaml"
    tag_line = f"  tags: [{tags}]\n" if tags else ""
    scn.write_text(
        f"- name: demo\n{tag_line}  steps:\n    - tap: {{ id: home.title }}\n", encoding="utf-8"
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    return cfg, scn


def test_record_writes_the_authored_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The record command's option handling and output-path selection, device- and AI-free: the
    # authoring loop, driver launch, and launchServer are the external boundaries, stubbed here so
    # the surrounding command body (target/browser/out resolution, then the file write) is covered.
    # A dummy key clears the credential gate and --no-dismiss-alerts skips the alert guard, so no
    # model client is ever built on this deterministic path.
    import bajutsu.cli.commands.record as rec
    from bajutsu.scenario import load_scenarios

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    authored = load_scenarios("- name: authored\n  steps:\n    - tap: { id: home.title }\n")[0]
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    monkeypatch.setattr(rec, "make_agent", lambda *a, **k: object())
    monkeypatch.setattr(rec, "launch_driver", lambda *a, **k: (object(), None))
    monkeypatch.setattr(
        "bajutsu.cli._shared.start_launch_server", lambda *a, **k: (lambda: None, None)
    )
    monkeypatch.setattr(rec, "record_loop", lambda *a, **k: authored)

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    out = tmp_path / "rec.yaml"
    r = runner.invoke(
        app,
        [
            "record",
            "--target",
            "demo",
            "--backend",
            "fake",
            "--goal",
            "do x",
            "--out",
            str(out),
            "--no-dismiss-alerts",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 0
    assert "recorded 1 steps" in r.output
    # record announces the resolved AI provider and model up front (here the defaults). Disclosure is
    # per-provider (BE-0176 follow-up): the Anthropic SDK has no reasoning-effort knob, so its line
    # names only provider and model — never an "effort" that would not take effect.
    assert "AI: api-key" in r.output
    assert "model claude-opus-4-8" in r.output
    assert "effort" not in r.output
    assert out.is_file() and "name: authored" in out.read_text(encoding="utf-8")


def test_record_needs_human_handoff_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0179: a needs-human turn with no responder (the CI / non-interactive path) is a clean,
    # labeled non-zero exit — distinct from the credential/device exit 2 — never a hang or a guess.
    import bajutsu.cli.commands.record as rec
    from bajutsu.handoff import HumanHandoffUnavailable

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    monkeypatch.setattr(rec, "make_agent", lambda *a, **k: object())
    monkeypatch.setattr(rec, "launch_driver", lambda *a, **k: (object(), None))
    monkeypatch.setattr(
        "bajutsu.cli._shared.start_launch_server", lambda *a, **k: (lambda: None, None)
    )

    def _needs_human(*_a: object, **_k: object) -> object:
        raise HumanHandoffUnavailable("solve the CAPTCHA")

    monkeypatch.setattr(rec, "record_loop", _needs_human)

    cfg = _fake_record_config(tmp_path)
    r = runner.invoke(
        app,
        [
            "record",
            "--target",
            "demo",
            "--backend",
            "fake",
            "--goal",
            "log in",
            "--out",
            str(tmp_path / "rec.yaml"),
            "--no-dismiss-alerts",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 3
    assert "needs human handoff" in r.output and "re-record interactively" in r.output


def _fake_record_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    return cfg


def test_record_device_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A device failure while bringing the app up is reported and exits 2, not an uncaught traceback.
    import bajutsu.cli.commands.record as rec
    from bajutsu import simctl as _simctl

    def no_device(*_a: object, **_k: object) -> object:
        raise _simctl.DeviceError("no booted simulator")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # clear the credential gate
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    monkeypatch.setattr(rec, "make_agent", lambda *a, **k: object())
    monkeypatch.setattr(
        "bajutsu.cli._shared.start_launch_server", lambda *a, **k: (lambda: None, None)
    )
    monkeypatch.setattr(rec, "launch_driver", no_device)
    r = runner.invoke(
        app,
        [
            "record",
            "--target",
            "demo",
            "--backend",
            "fake",
            "--goal",
            "do x",
            "--out",
            str(tmp_path / "rec.yaml"),
            "--no-dismiss-alerts",
            "--config",
            str(_fake_record_config(tmp_path)),
        ],
    )
    assert r.exit_code == 2
    assert "no booted simulator" in r.output


def _stub_execution(
    monkeypatch: pytest.MonkeyPatch, *, results: list[object], manifest: Path
) -> None:
    """Stub the runner boundary so the `run` command body runs device-free and deterministically.

    The command's dispatch, verdict, and post-verdict logic is what these tests cover; the pool and
    the pipeline it hands off to are exercised in `tests/runner/`. Stubbing them here also keeps the
    test off `simctl` (udid resolution) and off the GitHub-Actions summary side effect, so it passes
    identically on the Linux gate and locally.
    """
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    monkeypatch.delenv(
        "GITHUB_ACTIONS", raising=False
    )  # keep github.emit a no-op (no summary write)
    monkeypatch.setattr(
        "bajutsu.cli.commands.run.device_pool", lambda *a, **k: (object(), lambda: None)
    )
    monkeypatch.setattr(
        "bajutsu.cli.commands.run.run_and_report", lambda *a, **k: (results, manifest)
    )


def _manifest_at(tmp_path: Path) -> Path:
    manifest = tmp_path / "runs" / "20260101-000000" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    return manifest


def _run_argv(cfg: Path, scn: Path, tmp_path: Path, *extra: str) -> list[str]:
    return [
        "run",
        "--scenario",
        str(scn),
        "--target",
        "demo",
        "--backend",
        "fake",
        *extra,
        "--config",
        str(cfg),
        "--runs-dir",
        str(tmp_path / "runs"),
    ]


def test_run_reports_pass_and_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The command body dispatches to the runner, then prints the machine-only verdict: all results
    # ok -> PASS on stdout and exit 0.
    from bajutsu.orchestrator import RunResult

    manifest = _manifest_at(tmp_path)
    _stub_execution(monkeypatch, results=[RunResult("demo", True, [])], manifest=manifest)
    cfg, scn = _fake_run(tmp_path)
    r = runner.invoke(app, _run_argv(cfg, scn, tmp_path, "--no-dismiss-alerts"))
    assert r.exit_code == 0
    assert r.output.startswith(f"PASS  {manifest}")


def test_run_reports_fail_and_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A single failing result flips the verdict: FAIL on stdout and exit 1 (no LLM on this path).
    from bajutsu.orchestrator import RunResult

    manifest = _manifest_at(tmp_path)
    _stub_execution(
        monkeypatch, results=[RunResult("demo", False, [], failure="boom")], manifest=manifest
    )
    cfg, scn = _fake_run(tmp_path)
    r = runner.invoke(app, _run_argv(cfg, scn, tmp_path, "--erase", "--no-dismiss-alerts"))
    assert r.exit_code == 1
    assert r.output.startswith("FAIL")


def test_run_zip_writes_artifact_after_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --zip packages the finished run into runs/<id>.zip strictly after the verdict, so it can't
    # affect pass/fail (BE-0060). The archive is a plain walk of the run dir, so a populated dir is
    # all it needs.
    from bajutsu.orchestrator import RunResult

    manifest = _manifest_at(tmp_path)
    (manifest.parent / "report.html").write_text("<html></html>", encoding="utf-8")
    _stub_execution(monkeypatch, results=[RunResult("demo", True, [])], manifest=manifest)
    cfg, scn = _fake_run(tmp_path)
    r = runner.invoke(app, _run_argv(cfg, scn, tmp_path, "--zip", "--no-dismiss-alerts"))
    assert r.exit_code == 0  # the verdict stands, unaffected by the post-run zip
    assert (manifest.parent.parent / f"{manifest.parent.name}.zip").is_file()


def test_run_dismiss_alerts_notes_no_op_without_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The alert guard is on by default, but with no AI credential it degrades to a no-op and never
    # constructs a client (a Claude-free deterministic run). The note surfaces; the run still runs.
    from bajutsu.orchestrator import RunResult

    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "anthropic.Anthropic",
        lambda *a, **k: pytest.fail("client constructed despite missing credential"),
    )
    _stub_execution(
        monkeypatch, results=[RunResult("demo", True, [])], manifest=_manifest_at(tmp_path)
    )
    cfg, scn = _fake_run(tmp_path)
    r = runner.invoke(app, _run_argv(cfg, scn, tmp_path))
    assert r.exit_code == 0
    assert "the vision alert guard will no-op" in r.output


def test_run_dismiss_alerts_bedrock_note_without_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Bedrock provider authenticates the alert guard with AWS credentials but still needs a
    # model id; with none set the guard no-ops and says so — a distinct note from the Anthropic-key
    # gap, and still no client is constructed on this deterministic run.
    from bajutsu.orchestrator import RunResult

    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.delenv("BAJUTSU_BEDROCK_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _stub_execution(
        monkeypatch, results=[RunResult("demo", True, [])], manifest=_manifest_at(tmp_path)
    )
    cfg, scn = _fake_run(tmp_path)
    r = runner.invoke(app, _run_argv(cfg, scn, tmp_path))
    assert r.exit_code == 0
    assert "no Bedrock model id is set" in r.output


def test_run_tag_no_match_exits_2(tmp_path: Path) -> None:
    # --tag selects across the fully-expanded set; when nothing matches it's a usage error (exit 2),
    # surfaced before any device work.
    cfg, scn = _fake_run(tmp_path, tags="smoke")
    r = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            str(scn),
            "--target",
            "demo",
            "--backend",
            "fake",
            "--tag",
            "nightly",
            "--config",
            str(cfg),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert r.exit_code == 2
    assert "no scenarios match" in r.output


def test_run_browsers_matrix_is_web_only(tmp_path: Path) -> None:
    # --browsers is a web-only axis: a multi-engine matrix on a non-web backend is caught up front
    # (exit 2), not after building a pool that would ignore the engine list (BE-0076).
    cfg, scn = _fake_run(tmp_path)
    r = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            str(scn),
            "--target",
            "demo",
            "--backend",
            "fake",
            "--browsers",
            "chromium,firefox",
            "--config",
            str(cfg),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert r.exit_code == 2
    assert "web-only" in r.output


def _web_eff(browser: str) -> Effective:
    cfg = load_config(f"targets: {{ web: {{ baseUrl: 'http://x/', browser: {browser} }} }}")
    return resolve(cfg, "web")


def _browser_of(eff: Effective) -> str:
    assert isinstance(eff.platform_config, WebConfig)
    return eff.platform_config.browser


def test_resolve_browser_flag_overrides_config() -> None:
    # Precedence (BE-0076): an explicit --browser flag wins over the target's config.
    eff = _web_eff("firefox")  # config says firefox
    assert _browser_of(_resolve_browser(eff, "webkit")) == "webkit"  # flag wins


def test_resolve_browser_empty_flag_keeps_config() -> None:
    # No flag: the resolved config value (here firefox) stands.
    assert _browser_of(_resolve_browser(_web_eff("firefox"), "")) == "firefox"


def test_resolve_browser_default_is_chromium() -> None:
    # No flag and no config: chromium, today's behaviour.
    eff = resolve(load_config("targets: { web: { baseUrl: 'http://x/' } }"), "web")
    assert _browser_of(_resolve_browser(eff, "")) == "chromium"


def test_resolve_language_flag_overrides_config() -> None:
    # BE-0188: an explicit --language flag wins over the target's `ai.language` config.
    cfg = load_config("defaults:\n  ai: { language: en }\ntargets:\n  s:\n    bundleId: com.x\n")
    eff = resolve(cfg, "s")
    assert eff.ai is not None and eff.ai.language == "en"  # config says en
    overridden = _resolve_language(eff, "ja")
    assert overridden.ai is not None and overridden.ai.language == "ja"  # flag wins


def test_resolve_language_flag_is_normalized() -> None:
    # BE-0188: the flag is normalized (strip + lowercase) like the config/serve paths, so the three
    # input surfaces agree on `JA` / ` ja `.
    eff = resolve(load_config("targets:\n  s:\n    bundleId: com.x\n"), "s")
    overridden = _resolve_language(eff, " JA ")
    assert overridden.ai is not None and overridden.ai.language == "ja"


def test_resolve_language_empty_flag_keeps_config() -> None:
    cfg = load_config("defaults:\n  ai: { language: ja }\ntargets:\n  s:\n    bundleId: com.x\n")
    eff = resolve(cfg, "s")
    kept = _resolve_language(eff, "")  # no flag
    assert kept.ai is not None and kept.ai.language == "ja"


def test_resolve_language_flag_without_ai_config() -> None:
    # No `ai:` block at all: the flag still applies, constructing the config it overrides.
    eff = resolve(load_config("targets:\n  s:\n    bundleId: com.x\n"), "s")
    assert eff.ai is None
    overridden = _resolve_language(eff, "ja")
    assert overridden.ai is not None and overridden.ai.language == "ja"


@pytest.mark.parametrize("command", ["record", "crawl"])
def test_unknown_language_exits_cleanly(tmp_path: Path, command: str) -> None:
    # An unknown --language exits 2 before reaching an AI path (BE-0188), with a usable hint.
    cfg, scn = _write(tmp_path)
    argv = _argv(command, cfg=cfg, scn=scn, out=tmp_path / "rec.yaml", app="demo")
    r = runner.invoke(app, [*argv, "--language", "klingon"])
    assert r.exit_code == 2
    assert "unknown --language" in r.output


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
    from bajutsu.scenario import Redact

    eff = Effective(
        target="web",
        platform_config=WebConfig(base_url=None),  # web target missing its baseUrl
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


def test_doctor_non_web_target_on_playwright_exits_cleanly() -> None:
    # An iOS-shaped target forced onto the playwright screen query exits 2 (fixable config error),
    # not an uncaught TypeError from the web narrowing (BE-0126: the base_url gate runs first).
    import typer

    from bajutsu.cli.commands.doctor import _current_screen
    from bajutsu.scenario import Redact

    eff = Effective(
        target="app",
        platform_config=IosConfig(bundle_id="com.example.demo"),
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


def test_doctor_xcuitest_uses_a_short_lived_runner_for_screen_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bajutsu.cli.commands.doctor import _current_screen
    from bajutsu.drivers import base
    from bajutsu.scenario import Redact

    eff = Effective(
        target="app",
        platform_config=IosConfig(bundle_id="com.example.demo"),
        backend=["ios"],
        device="booted",
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
    built: list[str] = []
    el: base.Element = {
        "identifier": "ok",
        "label": "OK",
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }

    class _FakeDriver:
        name = "xcuitest"

        def query(self) -> list[base.Element]:
            return [el]

    class _FakeEnv:
        def __init__(self, udid: str) -> None:
            built.append(udid)

        def start(self, *_a: object, **_k: object) -> _FakeDriver:
            return _FakeDriver()

        def teardown(self, *_a: object, **_k: object) -> None:
            pass

    # Earlier iOS read the tree with no runner; now (BE-0290) the shared probe brings a
    # short-lived XCUITest runner up. `_current_screen` is a thin adapter over doctor.probe_screen
    # (BE-0199), which resolves the udid then builds that runner via the read-session seam.
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.read_session.environment_for",
        lambda actuator, udid, env_run=None, **_k: _FakeEnv(udid),
    )

    elements = _current_screen("xcuitest", "booted", eff)
    assert elements == [el]
    assert built == ["FAKE-UDID"]  # the runner was built for the resolved udid


def test_xcuitest_runner_summary_reports_the_resolved_source(tmp_path: Path) -> None:
    # BE-0292: doctor discloses which runner tier an xcuitest target would use, without acting on
    # it (no build run, no cache materialized) — pure config inspection.
    from bajutsu.cli.commands.doctor import xcuitest_runner_summary
    from bajutsu.config import XcuitestConfig
    from bajutsu.scenario import Redact

    runner = tmp_path / "Runner.xctestrun"
    runner.write_bytes(b"")
    eff = Effective(
        target="app",
        platform_config=IosConfig(
            bundle_id="com.example.demo",
            xcuitest=XcuitestConfig(test_runner=str(runner)),
        ),
        backend=["xcuitest"],
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
    assert xcuitest_runner_summary(eff, "xcuitest") == [f"xcuitest runner: testRunner: {runner}"]


def test_xcuitest_runner_summary_empty_for_other_actuators() -> None:
    # No other backend resolves a runner this way, so there is nothing to disclose.
    from bajutsu.cli.commands.doctor import xcuitest_runner_summary

    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    assert xcuitest_runner_summary(eff, "idb") == []


def test_tool_version_degrades_on_failure_and_reads_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0292: the host toolchain probe must degrade to None on any failure (so the mismatch note
    # doesn't fire on a non-macOS host) and must read stderr (xcodebuild -version prints there).
    import subprocess as sp

    from bajutsu.cli.commands import doctor

    def _raise(*args: object, **kwargs: object) -> object:
        raise OSError("no such tool")

    monkeypatch.setattr(doctor.subprocess, "run", _raise)
    assert doctor._tool_version(["missing-tool"]) is None

    def _nonzero(*args: object, **kwargs: object) -> object:
        # check=True turns this into CalledProcessError; a stray "1.2" must NOT be read as a version.
        raise sp.CalledProcessError(1, "cmd", output="error near 1.2", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", _nonzero)
    assert doctor._tool_version(["failing-tool"]) is None

    def _junk(*args: object, **kwargs: object) -> sp.CompletedProcess[str]:
        return sp.CompletedProcess([], 0, stdout="no version here", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", _junk)
    assert doctor._tool_version(["quiet-tool"]) is None

    def _stderr_only(*args: object, **kwargs: object) -> sp.CompletedProcess[str]:
        return sp.CompletedProcess([], 0, stdout="", stderr="Xcode 16.0")

    monkeypatch.setattr(doctor.subprocess, "run", _stderr_only)
    assert doctor._tool_version(["xcodebuild"]) == "16.0"


def test_xcuitest_runner_summary_warns_on_a_bundled_toolchain_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0292: a target with no testRunner resolves to the bundled runner, so doctor appends a
    # mismatch warning when the host Xcode major differs from the toolchain the bundle recorded.
    from bajutsu.cli.commands import doctor
    from bajutsu.config import XcuitestConfig
    from bajutsu.platform_lifecycle.environments import xcuitest as xc
    from bajutsu.scenario import Redact

    # A non-None products dir makes `runner_source` report the bundled tier for the first line; the
    # note's own tier check keys on the empty XcuitestConfig (no testRunner), not on this path.
    monkeypatch.setattr(xc, "bundled_products_dir", lambda: tmp_path)
    monkeypatch.setattr(xc, "bundled_runner_build_info", lambda: {"xcode": "16.0", "sdk": "18.0"})
    monkeypatch.setattr(doctor, "_host_toolchain", lambda: ("15.4", "18.0"))
    eff = Effective(
        target="app",
        platform_config=IosConfig(bundle_id="com.example.demo", xcuitest=XcuitestConfig()),
        backend=["xcuitest"],
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
    lines = doctor.xcuitest_runner_summary(eff, "xcuitest")
    assert lines[0] == "xcuitest runner: bundled (wheel-shipped Simulator runner)"
    assert len(lines) == 2
    assert "⚠" in lines[1]
    assert "Xcode 16.0 (bundled runner) vs 15.4 (host)" in lines[1]
    assert "xcuitest.testRunner" in lines[1]


def test_current_screen_fake_backend_queries_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    # For a non-xcuitest actuator, `doctor` scores whatever the driver's query() returns. The fake
    # backend needs no device, so resolving the udid is the only thing to stub away.
    from bajutsu.cli.commands.doctor import _current_screen

    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    assert _current_screen("fake", "booted", eff) == []  # the fake's screen starts empty


def test_check_scenarios_flags_an_unsupported_construct(tmp_path: Path) -> None:
    # A selectOption (native <select>) needs the web-only selectOption capability, which xcuitest
    # lacks — check_scenarios reports it, purely, with no device: the capability set is a static
    # class constant.
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    scn = tmp_path / "select.yaml"
    scn.write_text(
        "- name: pick\n  steps:\n    - selectOption: { sel: { id: theme }, option: dark }\n",
        encoding="utf-8",
    )
    reasons = check_scenarios(scn, capabilities_for("xcuitest"))
    assert len(reasons) == 1
    assert "[pick]" in reasons[0] and "selectOption" in reasons[0]


def test_check_scenarios_passes_a_supported_scenario(tmp_path: Path) -> None:
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    scn = tmp_path / "ok.yaml"
    scn.write_text("- name: t\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8")
    # the fake backend can perform every gated construct
    assert check_scenarios(scn, capabilities_for("fake")) == []


def test_check_scenarios_missing_file_raises(tmp_path: Path) -> None:
    from bajutsu.backends import capabilities_for
    from bajutsu.cli.commands.doctor import check_scenarios

    with pytest.raises(FileNotFoundError):
        check_scenarios(tmp_path / "nope.yaml", capabilities_for("fake"))


def test_check_scenarios_narrows_on_a_real_ios_device(tmp_path: Path) -> None:
    # BE-0238 Unit 3: doctor's capability check runs against the run-narrowed capabilities, so a real
    # iOS device (xcuitest.deviceType: device) reports a setLocation scenario as unsupported — the
    # same simctl-backed capability the run preflight drops — instead of the stale "supported" the
    # static set gives. Guards doctor against the drift this item's motivation calls out.
    from bajutsu.backends import capabilities_for_run
    from bajutsu.cli.commands.doctor import check_scenarios

    scn = tmp_path / "loc.yaml"
    scn.write_text(
        "- name: here\n  steps:\n    - setLocation: { lat: 1.0, lon: 2.0 }\n", encoding="utf-8"
    )
    dev = resolve(
        load_config(
            "targets: { demo: { bundleId: com.x,"
            " xcuitest: { deviceType: device, testRunner: Runner.xctestrun } } }"
        ),
        "demo",
    )
    sim = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    reasons = check_scenarios(scn, capabilities_for_run("xcuitest", dev))
    assert len(reasons) == 1 and "setLocation" in reasons[0]
    # The Simulator keeps the simctl-backed capability, so the same scenario is supported there.
    assert check_scenarios(scn, capabilities_for_run("xcuitest", sim)) == []


def test_claude_readiness_reachable_with_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # With a resolvable credential the optional Claude section reports "reachable" — a ✓, never the
    # ✗ an environment failure uses (BE-0101). No client is built; availability only reads env.
    from bajutsu.cli.commands.doctor import _claude_readiness

    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    line = _claude_readiness(eff)
    assert "reachable" in line and "✓" in line


def test_claude_readiness_not_configured_without_a_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No credential: a neutral "not configured (optional)" line with the en-dash marker, so a user
    # with no AI setup is never told the deterministic path is broken (BE-0101).
    from bajutsu.cli.commands.doctor import _claude_readiness

    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    eff = resolve(load_config("targets: { demo: { bundleId: com.x } }"), "demo")
    line = _claude_readiness(eff)
    assert "not configured (optional)" in line and "–" in line


def test_doctor_fake_backend_scores_the_current_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The full doctor path on the fake backend: environment gates pass with no tools, then it scores
    # the (empty) fake screen — Blocked, exit 1 — device-free.
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "FAKE-UDID")
    cfg, _ = _write(tmp_path)
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        app, ["doctor", "--target", "demo", "--backend", "fake", "--config", str(cfg)]
    )
    assert r.exit_code == 1
    assert "grade: Blocked" in r.output


def test_doctor_reports_unreachable_screen_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Environment gates pass (tools installed) but the live screen faults — e.g. a web target whose
    # app server is down, so navigating the baseUrl raises DeviceError. doctor must surface it as a
    # fixable error and exit 1, not propagate a stack trace.
    from bajutsu import simctl

    # `_current_screen` is replaced wholesale below, so the real udid resolution it would do never
    # runs — no `resolve_udid` stub needed.
    def boom(actuator: str, udid: str, eff: object) -> list[object]:
        raise simctl.DeviceError("web browser fault (recoverable wedge): ERR_CONNECTION_REFUSED")

    monkeypatch.setattr("bajutsu.cli.commands.doctor._current_screen", boom)
    cfg, _ = _write(tmp_path)
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        app, ["doctor", "--target", "demo", "--backend", "fake", "--config", str(cfg)]
    )
    assert r.exit_code == 1
    assert r.exception is None or isinstance(r.exception, SystemExit)  # no crash, a clean exit
    assert "could not read the screen to score" in r.output
    assert "ERR_CONNECTION_REFUSED" in r.output


def test_doctor_scenario_not_found_exits_2(tmp_path: Path) -> None:
    # A --scenario path that doesn't exist is a usage error surfaced before any capability work.
    cfg, _ = _write(tmp_path)
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "fake",
            "--scenario",
            str(tmp_path / "missing.yaml"),
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "scenario not found" in r.output


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


def test_serve_local_config_binds_the_config_directory(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A local `--config` in a subdirectory anchors serve's cwd at the config's own directory, not the
    # launch dir, so its relative paths resolve the same wherever serve was started (BE-0242) — the
    # local-config counterpart of the Git bind above. The config lives under a subdir so the config
    # dir differs from any plausible launch dir.
    import bajutsu.serve as srv

    cfg_dir = tmp_path / "proj"
    cfg_dir.mkdir()
    cfg = cfg_dir / "bajutsu.config.yaml"
    cfg.write_text("targets: { demo: { bundleId: com.example.demo } }\n", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: captured.update(kw))  # don't start a server
    r = runner.invoke(app, ["serve", "--config", str(cfg)])
    assert r.exit_code == 0
    assert captured["config"] == cfg
    assert captured["cwd"] == cfg_dir  # anchored at the config's directory, not the launch dir


def test_serve_local_relative_config_is_resolved_to_absolute(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A relative `--config` must reach srv.serve as an absolute path: a run job passes it as `--config`
    # to a subprocess launched with cwd=<config's dir>, so a path left relative to serve's launch dir
    # would no longer resolve once cwd moves. Launch from a directory *other* than the config's so a
    # relative path only resolves after `.resolve()` runs — this is what the absolute-path fixture in
    # test_serve_local_config_binds_the_config_directory can't catch (its input is already absolute).
    import bajutsu.serve as srv

    cfg_dir = tmp_path / "proj"
    cfg_dir.mkdir()
    cfg = cfg_dir / "bajutsu.config.yaml"
    cfg.write_text("targets: { demo: { bundleId: com.example.demo } }\n", encoding="utf-8")
    launch_dir = tmp_path / "elsewhere"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    captured: dict[str, object] = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: captured.update(kw))  # don't start a server
    # Path relative to launch_dir, pointing back up into the config's subdir.
    r = runner.invoke(app, ["serve", "--config", "../proj/bajutsu.config.yaml"])
    assert r.exit_code == 0
    config = captured["config"]
    assert isinstance(config, Path) and config.is_absolute()  # survives the cwd rebind
    assert config == cfg
    assert captured["cwd"] == cfg_dir  # still anchored at the config's directory


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


def test_serve_themes_flag_and_default_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `--themes <dir>` must arrive at srv.serve as Path(themes), and read_default_theme must be
    # called against the config that was resolved (including a materialized Git checkout) so that
    # ui.default_theme is not silently dropped on Git-bound configs (BE-0191).
    import bajutsu.serve as srv

    themes = tmp_path / "themes"
    themes.mkdir()
    cfg = tmp_path / "c.yaml"
    cfg.write_text("ui:\n  default_theme: daylight\n", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: captured.update(kw))
    r = runner.invoke(app, ["serve", "--themes", str(themes), "--config", str(cfg)])
    assert r.exit_code == 0
    assert captured["themes_dir"] == themes
    assert captured["default_theme"] == "daylight"


def test_serve_themes_flag_absent_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # When --themes is omitted, themes_dir must be None (only the built-in pair is offered).
    import bajutsu.serve as srv

    captured: dict[str, object] = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: captured.update(kw))
    r = runner.invoke(app, ["serve", "--config", "bajutsu.config.yaml"])
    assert r.exit_code == 0
    assert captured.get("themes_dir") is None


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


# --- crawl AI-provider credential gate (BE-0053 / BE-0097) ------------------------------------
# The crawl-specific `_ai_credential_gap` was removed by BE-0097: crawl now uses the shared,
# provider-aware `_require_ai_credential(eff)` from `_shared.py`, and `credential_gap(eff.ai)` is
# tested exhaustively in `test_anthropic_client.py`.


def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the credential-gate CLI tests hermetic: stub the @app.callback .env load so a
    developer's local .env can't re-inject ANTHROPIC_API_KEY / a provider, and clear those vars."""
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    for var in (
        "ANTHROPIC_API_KEY",
        "BAJUTSU_AI_PROVIDER",
        "BAJUTSU_BEDROCK_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_crawl_api_agent_needs_anthropic_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On the default Anthropic provider the crawl guide requires ANTHROPIC_API_KEY. The gate fires
    # after backend selection (the `fake` actuator is always available) and before any device work,
    # so the run dir is not created.
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
    import bajutsu.simctl as _benv

    _no_dotenv(monkeypatch)
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("BAJUTSU_BEDROCK_MODEL", "global.anthropic.claude-opus-4-6-v1")
    monkeypatch.setattr("bajutsu.simctl.resolve_udid", lambda u, run=None: "booted")

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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # clear the credential gate (guide is lazy)
    monkeypatch.setattr("bajutsu.cli._shared.ensure_web_runtime", lambda *a, **k: None)
    monkeypatch.setattr("bajutsu.cli._shared.select_actuator", lambda *a, **k: "playwright")
    monkeypatch.setattr(
        "bajutsu.cli._shared.start_launch_server", lambda *a, **k: ((lambda: None), None)
    )

    launched = {"n": 0}

    def fake_launch(*_a: object, **_k: object) -> object:
        launched["n"] += 1
        return object(), None  # the engine is mocked, so no driver method is ever called

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
            "--out",
            str(out),
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 0, r.output
    # Like record, crawl announces the resolved AI provider and model up front (BE-0176 follow-up:
    # both commands share `announce_ai`, so neither starts a provider silently). The api-key default
    # is the Anthropic SDK, whose per-provider line names only provider and model — no effort.
    assert "AI: api-key" in r.output
    assert "model claude-opus-4-8" in r.output
    assert "effort" not in r.output
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


def test_approve_promotes_element_scoped_crop(tmp_path: Path) -> None:
    """An element-scoped baseline is just the cropped actual — `approve` needs no special-casing."""
    import json

    run_dir = tmp_path / "runs" / "20260610-1"
    (run_dir / "00-home").mkdir(parents=True)
    # The evidence points at the element crop, not the full screenshot.
    (run_dir / "00-home" / "actual-card.png").write_bytes(b"CROPDATA")
    manifest = {
        "runId": "20260610-1",
        "ok": False,
        "scenarios": [
            {
                "scenario": "home",
                "ok": False,
                "expect_results": [
                    {
                        "ok": False,
                        "kind": "visual",
                        "detail": "visual ≈ card.png",
                        "reason": "baseline not found: card.png",
                        "visual": {
                            "baseline_name": "card.png",
                            "actual": "00-home/actual-card.png",
                            "missing": True,
                            "element_scoped": True,
                        },
                    }
                ],
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    baselines = tmp_path / "baselines"
    r = runner.invoke(app, ["approve", str(run_dir), "--baselines", str(baselines)])
    assert r.exit_code == 0
    assert (baselines / "card.png").read_bytes() == b"CROPDATA"  # the crop became the baseline


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
    # `serve --backend=server` needs the optional extras; with one missing, it must print an
    # install hint and exit 2 — never a raw ImportError traceback.
    import sys

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setitem(sys.modules, "boto3", None)
    r = runner.invoke(app, ["serve", "--backend", "server"])
    assert r.exit_code == 2
    assert "extra" in r.output.lower()


def test_crawl_continue_and_resume_are_mutually_exclusive(tmp_path: Path) -> None:
    # --continue (the whole remaining frontier) and --resume-src/--resume-key (one pruned branch)
    # contradict, so naming both is rejected up front — before any backend/credential/device setup
    # (BE-0181), so the error is a clean usage message, not a late failure.
    cfg, _ = _write(tmp_path)
    r = runner.invoke(
        app,
        [
            "crawl",
            "--target",
            "demo",
            "--continue",
            "--resume-src",
            "fp",
            "--resume-key",
            "k",
            "--config",
            str(cfg),
        ],
    )
    assert r.exit_code == 2
    assert "mutually exclusive" in r.output


def test_worker_help_exits_cleanly() -> None:
    # The worker CLI uses stdlib only (no Redis/RQ since BE-0106), so --help must work without
    # any optional extras installed.
    r = runner.invoke(app, ["worker", "--help"])
    assert r.exit_code == 0
    assert "lease" in r.output.lower() or "poll" in r.output.lower()
