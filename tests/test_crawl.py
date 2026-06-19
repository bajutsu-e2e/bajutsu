"""Tests for the autonomous crawl engine core (bajutsu/crawl.py, BE-0038).

The engine explores an app breadth-first over the Driver abstraction, building a screen map.
It is exercised here entirely on the FakeDriver's multi-screen `react` model — no Simulator and
no AI — which is exactly the determinism boundary BE-0038 relies on: exploration and state
identity are deterministic functions of the element tree.
"""

from __future__ import annotations

from collections.abc import Callable

from conftest import el

from bajutsu import crawl
from bajutsu.drivers.fake import FakeDriver

# --- state fingerprint ---------------------------------------------------------------------


def test_fingerprint_is_stable_across_element_order() -> None:
    a = [el(identifier="home.start"), el(identifier="home.settings")]
    b = [el(identifier="home.settings"), el(identifier="home.start")]
    fa, fb = crawl.fingerprint(a), crawl.fingerprint(b)
    assert fa.kind == "id"
    assert fa.value == fb.value


def test_fingerprint_differs_on_different_id_sets() -> None:
    a = [el(identifier="home.start"), el(identifier="home.settings")]
    b = [el(identifier="home.start"), el(identifier="home.help")]
    assert crawl.fingerprint(a).value != crawl.fingerprint(b).value


def test_fingerprint_falls_back_to_structural_when_too_few_ids() -> None:
    # Coordinate/label-driven screen: no identifiers -> structural fingerprint, flagged.
    elements = [
        el(label="Start", traits=["button"], frame=(0, 0, 100, 40)),
        el(label="More", traits=["button"], frame=(0, 50, 100, 40)),
    ]
    fp = crawl.fingerprint(elements)
    assert fp.kind == "structural"
    # same structure -> same fingerprint
    assert fp.value == crawl.fingerprint(elements).value


# --- candidate actions ---------------------------------------------------------------------


def test_candidate_actions_are_actionable_id_bearing_and_id_sorted() -> None:
    elements = [
        el(identifier="z.button", traits=["button"]),
        el(identifier="a.link", traits=["link"]),
        el(identifier="label.only", traits=["staticText"]),  # not actionable
        el(traits=["button"]),  # actionable but no id -> skipped (replay needs a selector)
    ]
    actions = crawl.candidate_actions(elements)
    assert [a.target for a in actions] == ["a.link", "z.button"]
    assert all(a.kind == "tap" for a in actions)


# --- crawl traversal over a multi-screen fake app -------------------------------------------


def _three_screen_app() -> tuple[Callable[[FakeDriver, str, object], None], list[dict]]:
    """home -> {settings, about}; each has a back button to home; settings.toggle self-loops."""
    home = [
        el(identifier="home.settings", traits=["button"]),
        el(identifier="home.about", traits=["button"]),
    ]
    settings = [
        el(identifier="settings.back", traits=["button"]),
        el(identifier="settings.toggle", traits=["button"]),
    ]
    about = [el(identifier="about.back", traits=["button"])]
    screens = {"home": home, "settings": settings, "about": about}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap" or not isinstance(arg, dict):
            return
        target = arg.get("id")
        dest = {
            "home.settings": "settings",
            "home.about": "about",
            "settings.back": "home",
            "about.back": "home",
            "settings.toggle": "settings",  # self-loop, no new state
        }.get(str(target))
        if dest is not None:
            d.screen = list(screens[dest])

    return react, home


def test_crawl_discovers_every_reachable_screen() -> None:
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    screen_map = crawl.crawl(driver, reset, max_screens=50, max_steps=100)

    # Three distinct screens reached: home, settings, about.
    assert len(screen_map.nodes) == 3
    assert crawl.fingerprint(home).value in screen_map.nodes
    # Both forward transitions out of home were discovered by replay.
    assert any(e.action == "tap home.settings" for e in screen_map.edges)
    assert any(e.action == "tap home.about" for e in screen_map.edges)


def test_crawl_respects_max_steps_budget() -> None:
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    screen_map = crawl.crawl(driver, reset, max_screens=50, max_steps=1)
    # Only one action exercised, so we cannot have discovered all three screens.
    assert len(screen_map.nodes) < 3


def test_crawl_records_a_crash_when_app_ui_collapses() -> None:
    home = [el(identifier="home.boom", traits=["button"])]
    crashed = [el(traits=["application"])]  # collapsed tree: no actionable app content

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.boom":
            d.screen = list(crashed)

    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    screen_map = crawl.crawl(driver, reset, max_screens=50, max_steps=100)
    assert len(screen_map.crashes) == 1
    assert screen_map.crashes[0].path[-1] == "tap home.boom"


# --- serialization -------------------------------------------------------------------------


def test_screenmap_dict_round_trips_nodes_edges_crashes() -> None:
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    data = crawl.screenmap_dict(crawl.crawl(driver, reset))
    assert set(data) == {"nodes", "edges", "crashes"}
    assert isinstance(data["nodes"], list) and data["nodes"]
    assert all({"fingerprint", "kind", "ids", "actions"} <= set(n) for n in data["nodes"])
    assert all({"src", "action", "dst"} == set(e) for e in data["edges"])


# --- settle hook ---------------------------------------------------------------------------


def test_crawl_settles_after_every_observation() -> None:
    """A condition wait (never a fixed sleep) runs after each action; here we just count it."""
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)
    calls = 0

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    def settle(d: FakeDriver) -> None:
        nonlocal calls
        calls += 1

    crawl.crawl(driver, reset, settle=settle)
    assert calls > 0


# --- live event stream ---------------------------------------------------------------------


def test_crawl_streams_the_growing_map_via_on_event() -> None:
    """`on_event` fires as the map grows (the web UI's live graph), each call seeing more of it
    than the last — and the final snapshot matches the returned map."""
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    sizes: list[tuple[int, int]] = []

    def on_event(sm: crawl.ScreenMap) -> None:
        sizes.append((len(sm.nodes), len(sm.edges)))

    screen_map = crawl.crawl(driver, reset, on_event=on_event)
    assert sizes  # fired at least once (the start node), then again as edges/nodes were found
    assert sizes[0] == (1, 0)  # first event is the start screen, before any transition
    assert sizes[-1] == (len(screen_map.nodes), len(screen_map.edges))  # ends on the full map
    assert max(n for n, _ in sizes) == len(screen_map.nodes)  # never exceeds the final node count
