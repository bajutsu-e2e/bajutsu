"""Tests for `bajutsu serve`'s pure helpers: listing, command builders, path guards, coercion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _shared import project, write_run

from bajutsu import serve as srv


def test_list_scenarios_parses_names(tmp_path: Path) -> None:
    scn_dir, _, _ = project(tmp_path)
    got = srv.list_scenarios(scn_dir)
    assert len(got) == 1
    assert got[0]["file"] == "smoke.yaml"
    assert got[0]["names"] == ["alpha", "beta"]
    assert got[0]["path"].endswith("smoke.yaml")


def test_list_apps(tmp_path: Path) -> None:
    _, cfg, _ = project(tmp_path)
    assert srv.list_apps(cfg) == ["demo", "other"]


def test_mask_secret_keeps_head_and_tail() -> None:
    masked = srv.mask_secret("sk-ant-api03-abcdefXYZ")
    assert masked == "sk-a…fXYZ"  # head 4 + … + tail 4
    assert "abcdef" not in masked


def test_mask_secret_fully_hides_short_values() -> None:
    assert srv.mask_secret("short") == "•••••"
    assert srv.mask_secret("") == ""


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
        "--app",
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


def test_run_command_parallel_pool() -> None:
    cmd = srv.run_command("s.yaml", "demo", udid="A,B", workers=2)
    assert cmd[cmd.index("--udid") + 1] == "A,B"  # comma list passes through as a device pool
    assert cmd[cmd.index("--workers") + 1] == "2"
    assert "--workers" not in srv.run_command("s.yaml", "demo", workers=1)  # single-device omits it


def test_run_command_includes_baselines() -> None:
    cmd = srv.run_command("s.yaml", "demo", baselines="/b/dir")
    assert cmd[cmd.index("--baselines") + 1] == "/b/dir"
    assert "--baselines" not in srv.run_command("s.yaml", "demo")  # omitted when empty


def test_record_command_builder() -> None:
    cmd = srv.record_command(
        "out.yaml",
        "demo",
        "tap Increment",
        agent="claude-code",
        backend="idb",
        udid="U",
        config="c.yaml",
    )
    assert cmd[:6] == [sys.executable, "-m", "bajutsu", "record", "--out", "out.yaml"]
    assert cmd[6:12] == ["--app", "demo", "--goal", "tap Increment", "--config", "c.yaml"]
    assert cmd[cmd.index("--agent") + 1] == "claude-code"
    assert cmd[cmd.index("--backend") + 1] == "idb" and cmd[cmd.index("--udid") + 1] == "U"
    # erase / dismiss default to None (the CLI defaults — record erases and dismisses): no flag.
    assert "--erase" not in cmd and "--no-erase" not in cmd and "--no-dismiss-alerts" not in cmd
    # Explicit overrides mirror run_command.
    assert "--no-erase" in srv.record_command("o.yaml", "demo", "g", erase=False)
    assert "--no-dismiss-alerts" in srv.record_command("o.yaml", "demo", "g", dismiss_alerts=False)
    bare = srv.record_command("o.yaml", "demo", "g")  # no agent → no --agent (CLI default applies)
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
    assert cmd[:6] == [sys.executable, "-m", "bajutsu", "crawl", "--app", "demo"]
    assert cmd[cmd.index("--out") + 1] == "runs/20260619-1"
    assert cmd[cmd.index("--config") + 1] == "c.yaml"
    assert cmd[cmd.index("--max-screens") + 1] == "10"
    assert cmd[cmd.index("--max-steps") + 1] == "30"
    assert cmd[cmd.index("--backend") + 1] == "idb" and cmd[cmd.index("--udid") + 1] == "U"
    # erase defaults to None (the CLI default — crawl erases): no flag forced either way.
    assert "--erase" not in cmd and "--no-erase" not in cmd
    assert "--no-erase" in srv.crawl_command("demo", out="o", erase=False)  # explicit override
    bare = srv.crawl_command("demo", out="o")  # no backend/udid → those flags omitted
    assert "--backend" not in bare and "--udid" not in bare


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
