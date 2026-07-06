"""Tests for `bajutsu serve`'s pure helpers: listing, command builders, path guards, coercion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from _shared import project, write_run

from bajutsu import serve as srv


def test_list_scenarios_parses_names(tmp_path: Path) -> None:
    scn_dir, _, _ = project(tmp_path)
    got = srv.list_scenarios(scn_dir)
    assert len(got) == 1
    assert got[0]["file"] == "smoke.yaml"
    assert got[0]["names"] == ["alpha", "beta"]
    assert got[0]["path"].endswith("smoke.yaml")


def test_list_targets(tmp_path: Path) -> None:
    _, cfg, _ = project(tmp_path)
    assert srv.list_targets(cfg) == ["demo", "other"]


def test_load_config_cached_reuses_an_unchanged_file(tmp_path: Path) -> None:
    from bajutsu.serve import helpers

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets:\n  demo: { bundleId: com.example.demo }\n", encoding="utf-8")
    first = helpers._load_config_cached(cfg)
    # An unchanged file isn't re-parsed: the same object comes back.
    assert helpers._load_config_cached(cfg) is first


def test_load_config_cached_reparses_when_the_file_changes(tmp_path: Path) -> None:
    import os

    from bajutsu.serve import helpers

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets:\n  demo: { bundleId: com.example.demo }\n", encoding="utf-8")
    assert list(helpers._load_config_cached(cfg).targets) == ["demo"]
    # Rewrite with new content; bump mtime so the freshness key changes deterministically.
    cfg.write_text(
        "targets:\n  demo: { bundleId: com.example.demo }\n  other: { bundleId: com.example.other }\n",
        encoding="utf-8",
    )
    future = cfg.stat().st_mtime_ns + 1_000_000_000
    os.utime(cfg, ns=(future, future))
    assert sorted(helpers._load_config_cached(cfg).targets) == ["demo", "other"]


def test_load_serve_config_file_returns_none_on_malformed_yaml(tmp_path: Path) -> None:
    # A YAML syntax error (yaml.YAMLError, not a ValueError) is normalized so the helpers' broad
    # except still turns it into None rather than escaping and crashing request handling.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets: [unbalanced\n", encoding="utf-8")
    assert srv.load_serve_config_file(cfg) is None
    assert srv.list_targets(cfg) == []


def test_load_config_cached_keys_on_the_resolved_path(tmp_path: Path) -> None:
    import os

    from bajutsu.serve import helpers

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets:\n  demo: { bundleId: com.example.demo }\n", encoding="utf-8")
    # A relative path and the absolute path to the same file share one cache entry (same object).
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        first = helpers._load_config_cached(Path("bajutsu.config.yaml"))
        assert helpers._load_config_cached(cfg) is first
    finally:
        os.chdir(cwd)


def test_list_targets_reflects_an_edited_config(tmp_path: Path) -> None:
    import os

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets:\n  demo: { bundleId: com.example.demo }\n", encoding="utf-8")
    assert srv.list_targets(cfg) == ["demo"]
    cfg.write_text(
        "targets:\n  demo: { bundleId: com.example.demo }\n  other: { bundleId: com.example.other }\n",
        encoding="utf-8",
    )
    future = cfg.stat().st_mtime_ns + 1_000_000_000
    os.utime(cfg, ns=(future, future))
    assert srv.list_targets(cfg) == ["demo", "other"]  # cache invalidated by the mtime/size change


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("sk-ant-api03-abcdefXYZ", "sk-a…fXYZ"),  # head 4 + … + tail 4 (the middle never leaks)
        ("short", "•••••"),  # <= 8 chars: fully masked
        ("", ""),
    ],
)
def test_mask_secret(value: str, masked: str) -> None:
    assert srv.mask_secret(value) == masked


def test_list_scenarios_includes_descriptions(tmp_path: Path) -> None:
    d = tmp_path / "scn"
    d.mkdir()
    (d / "described.yaml").write_text(
        "description: file note\nscenarios:\n  - name: a\n    description: scn note\n"
        "    steps:\n      - tap: { id: x }\n",
        encoding="utf-8",
    )
    got = srv.list_scenarios(d)
    assert got[0]["description"] == "file note"
    assert got[0]["scenarios"] == [{"name": "a", "description": "scn note"}]
    assert got[0]["names"] == ["a"]


def test_list_scenarios_degrades_a_non_utf8_file_to_a_bare_entry(tmp_path: Path) -> None:
    # A non-UTF-8 *.yaml must still list (as a bare entry), not crash the whole listing.
    d = tmp_path / "scn"
    d.mkdir()
    (d / "ok.yaml").write_text("- name: a\n  steps: []\n", encoding="utf-8")
    (d / "bad.yaml").write_bytes(b"\xff\xfe not utf-8")
    got = {s["file"]: s for s in srv.list_scenarios(d)}
    assert set(got) == {"ok.yaml", "bad.yaml"}
    assert got["bad.yaml"]["names"] == []  # unparseable -> bare entry


def test_list_runs_newest_first_with_summary(tmp_path: Path) -> None:
    _, _, runs = project(tmp_path)
    write_run(runs, "20260610-1", ok=True, scenarios=[("alpha", True)])
    write_run(runs, "20260610-2", ok=False, scenarios=[("alpha", True), ("beta", False)])
    (runs / "not-a-run").mkdir()  # no manifest → skipped
    got = srv.list_runs(runs)
    assert [r["id"] for r in got] == ["20260610-2", "20260610-1"]  # newest first
    assert got[0]["ok"] is False and got[0]["passed"] == 1 and got[0]["total"] == 2
    assert got[0]["scenarios"] == ["alpha", "beta"] and got[0]["report"] is True
    assert got[1]["ok"] is True


def test_list_runs_empty_dir(tmp_path: Path) -> None:
    assert srv.list_runs(tmp_path / "nope") == []


def test_run_command_builder() -> None:
    cmd = srv.run_command("s.yaml", "demo", backend="idb", udid="U", config="c.yaml")
    assert cmd[:6] == [sys.executable, "-m", "bajutsu", "run", "--scenario", "s.yaml"]
    # erase defaults to None: no flag, so each scenario's preconditions.erase decides.
    # --progress is always passed so the run streams scenario/step lines into the run log.
    assert cmd[6:] == [
        "--target",
        "demo",
        "--config",
        "c.yaml",
        "--progress",
        "--backend",
        "idb",
        "--udid",
        "U",
    ]
    assert "--erase" not in cmd and "--no-erase" not in cmd
    erased = srv.run_command("s.yaml", "demo", erase=True, dismiss_alerts=True)
    assert "--erase" in erased and "--no-erase" not in erased and "--dismiss-alerts" in erased
    assert "--no-erase" in srv.run_command("s.yaml", "demo", erase=False)  # explicit override
    # dismiss_alerts defaults to None: no flag, so each scenario's dismissAlerts (on) decides.
    assert "--dismiss-alerts" not in cmd and "--no-dismiss-alerts" not in cmd
    # False forces the guard off for the run (mirrors --no-erase).
    assert "--no-dismiss-alerts" in srv.run_command("s.yaml", "demo", dismiss_alerts=False)
    # headed defaults to None: no flag, so the app's `headless` config decides. True/False force
    # the web browser visible/headless for the run (the Web UI's "show browser" toggle).
    assert "--headed" not in cmd and "--no-headed" not in cmd
    assert "--headed" in srv.run_command("s.yaml", "demo", headed=True)
    assert "--no-headed" in srv.run_command("s.yaml", "demo", headed=False)


def test_run_command_parallel_pool() -> None:
    cmd = srv.run_command("s.yaml", "demo", udid="A,B", workers=2)
    assert cmd[cmd.index("--udid") + 1] == "A,B"  # comma list passes through as a device pool
    assert cmd[cmd.index("--workers") + 1] == "2"
    assert "--workers" not in srv.run_command("s.yaml", "demo", workers=1)  # single-device omits it


def test_command_builders_pass_upload_exec_only_when_set() -> None:
    # The flag is set by serve only for an upload-sourced config (BE-0090); empty omits it.
    for build in (
        lambda **k: srv.run_command("s.yaml", "demo", **k),
        lambda **k: srv.record_command("o.yaml", "demo", "goal", **k),
        lambda **k: srv.crawl_command("demo", out="o", **k),
    ):
        assert "--upload-exec" not in build()  # default empty: ungoverned
        sandboxed = build(upload_exec="sandbox")
        assert sandboxed[sandboxed.index("--upload-exec") + 1] == "sandbox"


def test_run_command_includes_baselines() -> None:
    cmd = srv.run_command("s.yaml", "demo", baselines="/b/dir")
    assert cmd[cmd.index("--baselines") + 1] == "/b/dir"
    assert "--baselines" not in srv.run_command("s.yaml", "demo")  # omitted when empty


def test_run_command_includes_runs_dir() -> None:
    # An uploaded bundle (BE-0073) runs from its extracted dir but writes its run into serve's
    # runs store via --runs-dir; omitted (current-dir runs/) for a normal run.
    cmd = srv.run_command("s.yaml", "demo", runs_dir="/serve/runs")
    assert cmd[cmd.index("--runs-dir") + 1] == "/serve/runs"
    assert "--runs-dir" not in srv.run_command("s.yaml", "demo")


def test_run_command_emits_the_backfilled_flags() -> None:
    # BE-0134 backfill: run_command now passes through the flags it previously couldn't. This drives
    # the real run_command path (not flag_args directly), so a param dropped from its dict is caught.
    cmd = srv.run_command(
        "s.yaml",
        "demo",
        browser="firefox",
        browsers="chromium,firefox",
        tag="smoke",
        exclude="slow",
        schemas="/s",
        goldens="/g",
        network=False,
        log_predicate="subsystem == 'x'",
        log_subsystem="com.example",
        alert_instruction="tap Allow",
        zip_run=True,
        config_offline=True,
        require_pinned_config=True,
    )
    assert cmd[cmd.index("--browser") + 1] == "firefox"
    assert cmd[cmd.index("--browsers") + 1] == "chromium,firefox"
    assert cmd[cmd.index("--tag") + 1] == "smoke"
    assert cmd[cmd.index("--exclude") + 1] == "slow"
    assert cmd[cmd.index("--schemas") + 1] == "/s"
    assert cmd[cmd.index("--goldens") + 1] == "/g"
    assert cmd[cmd.index("--log-predicate") + 1] == "subsystem == 'x'"
    assert cmd[cmd.index("--log-subsystem") + 1] == "com.example"
    assert cmd[cmd.index("--alert-instruction") + 1] == "tap Allow"
    assert "--no-network" in cmd and "--zip" in cmd
    assert "--config-offline" in cmd and "--require-pinned-config" in cmd
    # All omitted when unset (defaults), so a normal run's argv is unchanged.
    bare = srv.run_command("s.yaml", "demo")
    for flag in (
        "--browser",
        "--tag",
        "--schemas",
        "--zip",
        "--network",
        "--no-network",
        "--config-offline",
    ):
        assert flag not in bare


def test_triage_command_builder() -> None:
    cmd = srv.triage_command("runs/r", config="c.yaml")
    assert cmd[:5] == [sys.executable, "-m", "bajutsu", "triage", "runs/r"]
    assert cmd[cmd.index("--config") + 1] == "c.yaml"
    # Heuristic by default: no --ai, and no apply/json/scenario/target flags when unset.
    assert "--ai" not in cmd and "--apply" not in cmd and "--json" not in cmd
    assert "--scenario" not in cmd and "--target" not in cmd
    # The full serve triage job: opt into AI, preview a fix against the source, emit machine JSON.
    full = srv.triage_command(
        "runs/r",
        target="demo",
        ai=True,
        apply_path="/scn/login.yaml",
        json_out="runs/r/triage.json",
        config="c.yaml",
    )
    assert "--ai" in full
    assert full[full.index("--target") + 1] == "demo"
    assert full[full.index("--apply") + 1] == "/scn/login.yaml"
    assert full[full.index("--json") + 1] == "runs/r/triage.json"
    # serve drives apply through POST /api/scenario, never the CLI's --write/--rerun.
    assert "--write" not in full and "--rerun" not in full


def test_record_command_builder() -> None:
    cmd = srv.record_command(
        "out.yaml",
        "demo",
        "tap Increment",
        backend="idb",
        udid="U",
        config="c.yaml",
    )
    assert cmd[:6] == [sys.executable, "-m", "bajutsu", "record", "--out", "out.yaml"]
    assert cmd[6:12] == ["--target", "demo", "--goal", "tap Increment", "--config", "c.yaml"]
    # The AI provider is inherited from the serve env (BE-0163), never passed as a flag.
    assert "--agent" not in cmd
    assert cmd[cmd.index("--backend") + 1] == "idb" and cmd[cmd.index("--udid") + 1] == "U"
    # erase / dismiss default to None (the CLI defaults — record erases and dismisses): no flag.
    assert "--erase" not in cmd and "--no-erase" not in cmd and "--no-dismiss-alerts" not in cmd
    # Explicit overrides mirror run_command.
    assert "--no-erase" in srv.record_command("o.yaml", "demo", "g", erase=False)
    assert "--no-dismiss-alerts" in srv.record_command("o.yaml", "demo", "g", dismiss_alerts=False)
    # headed mirrors run_command: None = no flag, True/False force the web browser visible/headless.
    assert "--headed" not in cmd and "--no-headed" not in cmd
    assert "--headed" in srv.record_command("o.yaml", "demo", "g", headed=True)
    assert "--no-headed" in srv.record_command("o.yaml", "demo", "g", headed=False)
    bare = srv.record_command("o.yaml", "demo", "g")
    assert "--agent" not in bare and "--backend" not in bare


def test_crawl_command_builder() -> None:
    cmd = srv.crawl_command(
        "demo",
        out="runs/20260619-1",
        backend="idb",
        udid="U",
        max_screens=10,
        max_steps=30,
        config="c.yaml",
    )
    assert cmd[:6] == [sys.executable, "-m", "bajutsu", "crawl", "--target", "demo"]
    assert cmd[cmd.index("--out") + 1] == "runs/20260619-1"
    assert cmd[cmd.index("--config") + 1] == "c.yaml"
    assert cmd[cmd.index("--max-screens") + 1] == "10"
    assert cmd[cmd.index("--max-steps") + 1] == "30"
    # The AI provider is inherited from the serve env (BE-0163), never passed as a flag.
    assert "--agent" not in cmd
    assert cmd[cmd.index("--backend") + 1] == "idb" and cmd[cmd.index("--udid") + 1] == "U"
    # erase defaults to None (the CLI default — crawl erases): no flag forced either way.
    assert "--erase" not in cmd and "--no-erase" not in cmd
    # headed mirrors run_command: None = no flag, True/False force the web browser visible/headless.
    assert "--headed" not in cmd and "--no-headed" not in cmd
    assert "--headed" in srv.crawl_command("demo", out="o", headed=True)
    assert "--no-headed" in srv.crawl_command("demo", out="o", headed=False)
    assert "--no-erase" in srv.crawl_command("demo", out="o", erase=False)  # explicit override
    assert "--no-dismiss-alerts" in srv.crawl_command("demo", out="o", dismiss_alerts=False)
    bare = srv.crawl_command("demo", out="o")  # no backend/udid → those flags omitted
    assert "--agent" not in bare  # the AI provider is inherited from the serve env (BE-0163)
    assert "--backend" not in bare and "--udid" not in bare
    assert "--guide" not in bare  # crawl is AI-driven; there is no guide toggle
    assert "--workers" not in bare  # single-device crawl omits it (CLI default 1)
    # A parallel pool (BE-0064): the comma udid list + worker count reach the crawl command.
    pool = srv.crawl_command("demo", out="o", udid="A,B,C", workers=3)
    assert pool[pool.index("--udid") + 1] == "A,B,C"
    assert pool[pool.index("--workers") + 1] == "3"
    # Resume passes the pruned branch's coordinates and never erases (it continues the same run).
    res = srv.crawl_command("demo", out="o", resume_src="abc123", resume_key="tab.x")
    assert res[res.index("--resume-src") + 1] == "abc123"
    assert res[res.index("--resume-key") + 1] == "tab.x"
    assert "--no-erase" in res
    assert "--resume-src" not in bare  # omitted for a normal crawl


def test_scenario_out_path_sanitizes(tmp_path: Path) -> None:
    d = tmp_path / "scn"
    assert srv.scenario_out_path(d, "login") == d / "login.yaml"
    assert srv.scenario_out_path(d, "login.yaml") == d / "login.yaml"  # suffix normalized
    assert srv.scenario_out_path(d, "a/b/../c") == d / "a-b-..-c.yaml"  # no escape via separators
    assert srv.scenario_out_path(d, "") == d / "authored.yaml"  # blank → fallback
    assert srv.scenario_out_path(d, "   ") == d / "authored.yaml"
    assert srv.scenario_out_path(d, "..") == d / "authored.yaml"  # never names the parent dir


def test_unique_scenario_path_stamps_existing(tmp_path: Path) -> None:
    p = tmp_path / "generated.yaml"
    assert srv.unique_scenario_path(p) == p  # free → unchanged
    p.write_text("a", encoding="utf-8")
    # taken → the run's date-time is appended so nothing is overwritten
    assert (
        srv.unique_scenario_path(p, stamp="20260613-153045")
        == tmp_path / "generated-20260613-153045.yaml"
    )


def test_scenario_path_guard_keeps_inside_dir(tmp_path: Path) -> None:
    d = tmp_path / "scn"
    d.mkdir()
    assert srv._scenario_path(d, None) is None
    assert srv._scenario_path(d, "smoke.yaml") == (d / "smoke.yaml").resolve()
    assert srv._scenario_path(d, str(d / "x.yaml")) == (d / "x.yaml").resolve()
    assert srv._scenario_path(d, "x.txt") is None  # only *.yaml
    assert srv._scenario_path(d, "../escape.yaml") is None  # traversal blocked
    assert srv._scenario_path(d, str(tmp_path / "outside.yaml")) is None


def test_int_coercion() -> None:
    assert srv._int(3, 1) == 3 and srv._int("4", 1) == 4
    assert srv._int(None, 1) == 1 and srv._int("x", 1) == 1  # bad values fall back


def _boom(_args: list[str], _e: object = None) -> str:
    raise OSError("simctl not found")


def test_list_simulators_parses_and_orders() -> None:
    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"udid": "B1", "name": "iPhone 17", "state": "Shutdown", "isAvailable": True},
                    {"udid": "A1", "name": "iPhone 17 Pro", "state": "Booted", "isAvailable": True},
                    {
                        "udid": "X",
                        "name": "old",
                        "state": "Shutdown",
                        "isAvailable": False,
                    },  # filtered out
                ],
            }
        }
    )
    sims = srv.list_simulators(simctl=lambda args, e=None: payload)
    assert [s["udid"] for s in sims] == ["A1", "B1"]  # booted first, then by name
    assert sims[0] == {"udid": "A1", "name": "iPhone 17 Pro", "runtime": "iOS 26.5", "booted": True}
    assert srv.list_simulators(simctl=_boom) == []  # failure -> empty, never raises


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("idb", True),
        ("ios", True),
        ("ios,fake", True),  # comma list of known tokens
        ("idb,bogus", False),  # one unknown token -> reject
        ("rm -rf /", False),  # free text -> reject
    ],
)
def test_valid_backend(value: str, ok: bool) -> None:
    assert srv.valid_backend(value) is ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("booted", True),
        ("ABCDEF01-2345-6789-ABCD-EF0123456789", True),
        ("A,B", True),  # comma pool
        ("A B", False),  # space -> reject
        ("A;rm -rf /", False),  # metacharacters -> reject
        ("-rf", False),  # leading hyphen -> reject (could look like a flag)
        ("--help", False),  # leading hyphen -> reject
        ("--config", False),  # leading hyphen -> reject
        ("booted,-rf", False),  # one leading-hyphen token in the pool -> reject
    ],
)
def test_valid_udid(value: str, ok: bool) -> None:
    assert srv.valid_udid(value) is ok
