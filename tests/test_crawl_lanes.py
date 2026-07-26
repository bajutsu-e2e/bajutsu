"""The `bajutsu crawl` command's resolution helpers (BE-0205): warm-start resolution, lane
planning, callback wiring, and health-seam wiring — the pieces BE-0205 lifted out of the ~250-line
body so each is a named unit taking plain data, unit-testable without a Simulator."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu import crawl as crawl_engine
from bajutsu.cli.commands.crawl import (
    _make_callbacks,
    _plan_lanes,
    _resolve_warm_start,
    _wire_health,
    _write_screenmap,
)
from bajutsu.drivers import base


def _sink() -> tuple[list[str], object]:
    # A report sink that records what the crawl streamed, so a helper's messages are assertable.
    msgs: list[str] = []
    return msgs, msgs.append


# --- _resolve_warm_start: fresh crawl, single-branch resume, and full-frontier continuation


def test_warm_start_fresh_crawl_returns_all_none(tmp_path: Path) -> None:
    # No resume/continue flags → a fresh crawl; the caller writes the empty starter map.
    _msgs, report = _sink()
    base_map, seed_path, seed_ops = _resolve_warm_start(
        tmp_path / "screenmap.json",
        resume_src="",
        resume_key="",
        continue_crawl=False,
        report=report,
    )
    assert (base_map, seed_path, seed_ops) == (None, None, None)


def test_warm_start_resume_seeds_path_and_op(tmp_path: Path) -> None:
    # A pruned branch with a two-action path: resume replays to its screen (seed_path) then explores
    # the pruned op (seed_ops), and drops it from `pruned` since it's being explored now.
    walk = crawl_engine.Action(kind="tap", target="home.tab")
    op = crawl_engine.Action(kind="tap", target="home.button")
    screen_map = crawl_engine.ScreenMap(
        pruned=[
            crawl_engine.Pruned(
                src="abc123", action="tap button", key="k1", owner="def456", path=(walk, op)
            )
        ]
    )
    path = tmp_path / "screenmap.json"
    _write_screenmap(path, screen_map)

    _msgs, report = _sink()
    base_map, seed_path, seed_ops = _resolve_warm_start(
        path, resume_src="abc123", resume_key="k1", continue_crawl=False, report=report
    )
    assert base_map is not None
    assert seed_path == [walk]
    assert seed_ops == [op]
    assert base_map.pruned == []  # the resumed branch is no longer pending


def test_warm_start_resume_unknown_branch_exits_2(tmp_path: Path) -> None:
    _write_screenmap(tmp_path / "screenmap.json", crawl_engine.ScreenMap())
    _msgs, report = _sink()
    with pytest.raises(typer.Exit) as exc:
        _resolve_warm_start(
            tmp_path / "screenmap.json",
            resume_src="nope",
            resume_key="missing",
            continue_crawl=False,
            report=report,
        )
    assert exc.value.exit_code == 2


def test_warm_start_continue_returns_base_map_only(tmp_path: Path) -> None:
    # A prior run that stopped with a frontier (a screen with untried ops) continues: base map, no seed.
    screen_map = crawl_engine.ScreenMap(plan={"abc123": ["home.button"]})
    path = tmp_path / "screenmap.json"
    _write_screenmap(path, screen_map)

    msgs, report = _sink()
    base_map, seed_path, seed_ops = _resolve_warm_start(
        path, resume_src="", resume_key="", continue_crawl=True, report=report
    )
    assert base_map is not None
    assert (seed_path, seed_ops) == (None, None)
    assert any("continuing crawl" in m for m in msgs)


def test_warm_start_continue_no_frontier_exits_2(tmp_path: Path) -> None:
    # A prior run that explored everything (empty plan) has nothing to continue — reject up front.
    _write_screenmap(tmp_path / "screenmap.json", crawl_engine.ScreenMap(plan={"abc123": []}))
    _msgs, report = _sink()
    with pytest.raises(typer.Exit) as exc:
        _resolve_warm_start(
            tmp_path / "screenmap.json",
            resume_src="",
            resume_key="",
            continue_crawl=True,
            report=report,
        )
    assert exc.value.exit_code == 2


def test_warm_start_unreadable_map_exits_2(tmp_path: Path) -> None:
    # --out points at a run with no readable screenmap.json — a usage error, not a traceback.
    _msgs, report = _sink()
    with pytest.raises(typer.Exit) as exc:
        _resolve_warm_start(
            tmp_path / "missing.json",
            resume_src="abc",
            resume_key="k",
            continue_crawl=False,
            report=report,
        )
    assert exc.value.exit_code == 2


# --- _plan_lanes: the lane pool + worker count the platform Environment sizes


class _StubEnv:
    # A minimal CrawlEnvironment stub: plan_lanes returns a fixed pool, the health seams are unused
    # here (filled per-test in the _wire_health section).
    def __init__(self, lanes: list[str]) -> None:
        self._lanes = lanes

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        return self._lanes


def test_plan_lanes_returns_the_capped_pool() -> None:
    # The pool is one lane per resolved udid (already capped to the worker count), so its length is
    # the final worker count — no separate count is carried.
    udids = _plan_lanes(_StubEnv(["a", "b", "c"]), udid="a,b,c", workers=3, seed_path=None)
    assert udids == ["a", "b", "c"]


def test_plan_lanes_empty_pool_exits_2() -> None:
    with pytest.raises(typer.Exit) as exc:
        _plan_lanes(_StubEnv([]), udid="", workers=1, seed_path=None)
    assert exc.value.exit_code == 2


def test_plan_lanes_single_branch_resume_collapses_to_one_lane() -> None:
    # A single-branch resume (seed_path set) is one walk, so the pool collapses to a single lane.
    seed = [crawl_engine.Action(kind="tap", target="x")]
    udids = _plan_lanes(_StubEnv(["a", "b", "c"]), udid="a,b,c", workers=3, seed_path=seed)
    assert udids == ["a"]


# --- _make_callbacks: the engine's (on_event, on_node) — persist the map, screenshot each node


class _FakeDriver:
    def __init__(self, fail: bool = False) -> None:
        self.shots: list[str] = []
        self._fail = fail

    def screenshot(self, path: str) -> None:
        if self._fail:
            raise OSError("no screen")
        self.shots.append(path)


def test_on_event_persists_map_and_reports(tmp_path: Path) -> None:
    path = tmp_path / "screenmap.json"
    msgs, report = _sink()
    on_event, _on_node = _make_callbacks(path, tmp_path / "screens", report)
    on_event(crawl_engine.ScreenMap())
    assert path.exists()  # the growing map is persisted for the web UI's poll
    assert any(m.startswith("🔭") for m in msgs)


def test_on_node_screenshots_each_new_screen(tmp_path: Path) -> None:
    screens = tmp_path / "screens"
    screens.mkdir()
    _on_event, on_node = _make_callbacks(tmp_path / "screenmap.json", screens, lambda _m: None)
    driver = _FakeDriver()
    node = crawl_engine.Node(fingerprint="abc1234", kind="screen", ids=(), actions=())
    on_node(driver, node)  # type: ignore[arg-type]
    assert driver.shots == [str(screens / "abc1234.png")]


def test_on_node_swallows_screenshot_failure(tmp_path: Path) -> None:
    # A screenshot hiccup (here an OSError) must not abort the crawl — it warns and moves on.
    msgs, report = _sink()
    _on_event, on_node = _make_callbacks(tmp_path / "screenmap.json", tmp_path / "screens", report)
    node = crawl_engine.Node(fingerprint="abc1234", kind="screen", ids=(), actions=())
    on_node(_FakeDriver(fail=True), node)  # type: ignore[arg-type]
    assert any("screenshot failed" in m for m in msgs)


# --- _wire_health: crash detection, blocking-overlay clearing, and lane recovery seams


class _HealthEnv:
    # A CrawlEnvironment stub exposing configurable health seams (the web shape supplies all three;
    # the device shape returns None from each and lets the alert guard supply clear_blocking).
    def __init__(
        self,
        *,
        aliveness: crawl_engine.AliveCheck | None = None,
        clearer: crawl_engine.ClearBlocking | None = None,
        recover: crawl_engine.Recover | None = None,
    ) -> None:
        self._aliveness = aliveness
        self._clearer = clearer
        self._recover = recover

    def crawl_aliveness(self) -> crawl_engine.AliveCheck | None:
        return self._aliveness

    def crawl_dialog_clearer(self) -> crawl_engine.ClearBlocking | None:
        return self._clearer

    def crawl_recover(self) -> crawl_engine.Recover | None:
        return self._recover


def _eff() -> object:
    from bajutsu.config import load_config, resolve

    return resolve(load_config("targets:\n  x:\n    bundleId: com.x\n"), "x")


def _redactor() -> object:
    from bajutsu.cli._shared import _ai_redactor

    return _ai_redactor(_eff())  # type: ignore[arg-type]


def test_wire_health_device_no_alerts_is_all_none() -> None:
    # A device platform declines every health seam and, with --no-alert-handling, no guard is built.
    is_alive, clear_blocking, recover = _wire_health(
        _HealthEnv(),  # type: ignore[arg-type]
        _eff(),  # type: ignore[arg-type]
        _redactor(),  # type: ignore[arg-type]
        alert_handling=False,
        alert_instruction="",
        report=lambda _m: None,
    )
    assert (is_alive, clear_blocking, recover) == (None, None, None)


def test_wire_health_web_passes_through_and_wraps_recover() -> None:
    # The web shape supplies all three seams. `clear_blocking` present means no alert guard is built
    # even with alert_handling on; `recover` is wrapped to report the wedge before healing the lane.
    healed: list[base.Driver] = []
    env = _HealthEnv(
        aliveness=lambda _d, _els: True,
        clearer=lambda _d: ["dialog"],
        recover=lambda d: healed.append(d),
    )
    msgs, report = _sink()
    is_alive, clear_blocking, recover = _wire_health(
        env,  # type: ignore[arg-type]
        _eff(),  # type: ignore[arg-type]
        _redactor(),  # type: ignore[arg-type]
        alert_handling=True,
        alert_instruction="",
        report=report,
    )
    assert is_alive is not None
    assert clear_blocking is not None and clear_blocking(_FakeDriver())  # type: ignore[arg-type]
    assert recover is not None
    recover(_FakeDriver())  # type: ignore[arg-type]
    assert len(healed) == 1  # the platform heal still runs
    assert any("wedged" in m for m in msgs)  # after reporting the wedge


def test_wire_health_ios_builds_alert_guard_clear_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The iOS shape: the platform declines the dialog clearer (returns None) and, with
    # --alert-handling on, `_wire_health` builds the alert guard to supply `clear_blocking`.
    # The shared `_build_alert_guard` (BE-0260) gates the guard on the AI credential — the real
    # crawl flow has already required it via `_require_ai_credential`, so set it here to exercise
    # the wiring branch without a Simulator or network.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    is_alive, clear_blocking, recover = _wire_health(
        _HealthEnv(),  # type: ignore[arg-type]  # all seams None, like iOS
        _eff(),  # type: ignore[arg-type]
        _redactor(),  # type: ignore[arg-type]
        alert_handling=True,
        alert_instruction="",
        report=lambda _m: None,
    )
    assert is_alive is None and recover is None  # engine default / no platform recovery
    assert clear_blocking is not None  # the alert guard now supplies it


def test_wire_health_no_credential_leaves_clear_blocking_unwired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0260 alignment: with --alert-handling on but no AI credential, the shared guard no-ops, so
    # `_wire_health` leaves `clear_blocking` unwired rather than constructing a hosted-fallback
    # client. (The real crawl flow never reaches here credential-less — `_require_ai_credential`
    # fails closed first — but the seam degrades gracefully.)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _is_alive, clear_blocking, _recover = _wire_health(
        _HealthEnv(),  # type: ignore[arg-type]  # all seams None, like iOS
        _eff(),  # type: ignore[arg-type]
        _redactor(),  # type: ignore[arg-type]
        alert_handling=True,
        alert_instruction="",
        report=lambda _m: None,
    )
    assert clear_blocking is None
