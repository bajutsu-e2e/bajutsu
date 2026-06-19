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
    assert set(data) == {"nodes", "edges", "crashes", "stop_reason"}
    assert isinstance(data["nodes"], list) and data["nodes"]
    assert all({"fingerprint", "kind", "ids", "actions"} <= set(n) for n in data["nodes"])
    assert all({"src", "action", "dst"} == set(e) for e in data["edges"])
    assert data["stop_reason"] == "completed"  # the small app is fully explored


def test_crawl_reports_why_it_stopped() -> None:
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    # Frontier exhausted within budget -> completed; a tight budget -> the limit that was hit.
    assert crawl.crawl(driver, reset, max_screens=50, max_steps=100).stop_reason == "completed"
    assert crawl.crawl(driver, reset, max_screens=50, max_steps=1).stop_reason == "max_steps"
    assert crawl.crawl(driver, reset, max_screens=1, max_steps=100).stop_reason == "max_screens"


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


def test_crawl_fires_on_node_once_per_screen_while_on_it() -> None:
    """`on_node` fires once per discovered screen — the hook the CLI uses to screenshot each one
    while the driver is still positioned on it. Here we capture a screenshot per node and assert
    one shot per distinct screen."""
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    seen: list[str] = []

    def on_node(node: crawl.Node) -> None:
        seen.append(node.fingerprint)
        driver.screenshot(f"{node.fingerprint}.png")

    screen_map = crawl.crawl(driver, reset, on_node=on_node)
    assert sorted(seen) == sorted(screen_map.nodes)  # one call per distinct screen, no repeats
    shots = [a for a in driver.actions if a[0] == "screenshot"]
    assert len(shots) == len(screen_map.nodes)  # a screenshot was taken for every node


# --- disabled controls + input filling (enabling a gated button) ---------------------------


def test_candidate_actions_offer_each_empty_field_and_a_compound_fill() -> None:
    elements = [
        el(identifier="f.user", traits=["textField"]),  # empty input
        el(identifier="f.pass", traits=["secureTextField"]),  # empty input
        el(identifier="f.submit", traits=["button", "notEnabled"]),  # disabled -> skipped
        el(identifier="f.cancel", traits=["button"]),  # enabled -> tapped
    ]
    actions = crawl.candidate_actions(elements)
    pairs = {(a.kind, a.target) for a in actions if a.kind != "fill"}
    assert ("tap", "f.cancel") in pairs
    assert ("tap", "f.submit") not in pairs  # disabled control is not a candidate
    # A type action for EACH empty field (the cross-product of fills), not just the first.
    assert ("type", "f.user") in pairs and ("type", "f.pass") in pairs
    # Plus one compound fill of both, to cross a gate that needs several fields at once.
    fills = [a for a in actions if a.kind == "fill"]
    assert len(fills) == 1 and {i for i, _ in fills[0].fields} == {"f.user", "f.pass"}


def test_action_fill_types_into_every_field() -> None:
    driver = FakeDriver(
        screen=[el(identifier="a", traits=["textField"]), el(identifier="b", traits=["textField"])]
    )
    crawl.Action("fill", fields=(("a", "x"), ("b", "y"))).perform(driver)
    assert ("tap", {"id": "a"}) in driver.actions and ("type", "x") in driver.actions
    assert ("tap", {"id": "b"}) in driver.actions and ("type", "y") in driver.actions


def test_fingerprint_distinguishes_selected_toggle() -> None:
    off = [el(identifier="s", traits=["switch"]), el(identifier="t", traits=["staticText"])]
    on = [
        el(identifier="s", traits=["switch", "selected"]),
        el(identifier="t", traits=["staticText"]),
    ]
    assert crawl.fingerprint(off).value != crawl.fingerprint(on).value


def test_crawl_crosses_a_two_field_gate_via_compound_fill() -> None:
    """Two masked (secure) fields gate a submit button; neither exposes its value, so filling one
    at a time is invisible and the BFS can't reach the all-filled state. The compound fill crosses
    the gate in one observable step (submit flips enabled) and the crawl presses it."""
    s: dict[str, object] = {"a": False, "b": False, "focus": None}

    def form() -> list[dict]:
        on = bool(s["a"]) and bool(s["b"])
        return [
            el(identifier="f.a", traits=["secureTextField"]),  # value never exposed (masked)
            el(identifier="f.b", traits=["secureTextField"]),
            el(identifier="f.go", traits=["button"] if on else ["button", "notEnabled"]),
        ]

    home = [
        el(identifier="home.t", traits=["staticText"]),
        el(identifier="home.b", traits=["button"]),
    ]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict):
            s["focus"] = arg.get("id")
            if arg.get("id") == "f.go" and s["a"] and s["b"]:
                d.screen = list(home)
        elif kind == "type":
            if s["focus"] == "f.a":
                s["a"] = True
            elif s["focus"] == "f.b":
                s["b"] = True
            d.screen = form()

    driver = FakeDriver(screen=form(), react=react)

    def reset(d: FakeDriver) -> None:
        s.update(a=False, b=False, focus=None)
        d.screen = form()

    screen_map = crawl.crawl(driver, reset, max_screens=50, max_steps=50)
    assert any(
        e.action.startswith("fill") for e in screen_map.edges
    )  # the compound fill crossed it
    assert any(e.action == "tap f.go" for e in screen_map.edges)  # pressed once enabled
    assert crawl.fingerprint(home).value in screen_map.nodes  # reached the screen behind it


def test_blocked_controls_lists_only_disabled_actionable_ids() -> None:
    elements = [
        el(identifier="a", traits=["button", "notEnabled"]),  # disabled button -> blocked
        el(identifier="b", traits=["button"]),  # enabled -> not blocked
        el(identifier="c", traits=["staticText", "notEnabled"]),  # not actionable -> ignored
    ]
    assert crawl.blocked_controls(elements) == ["a"]


def test_fingerprint_distinguishes_enabled_from_disabled() -> None:
    """The same ids hash differently when a control's interactive state differs, so a screen whose
    submit just became enabled is explored rather than mistaken for the empty form."""
    empty = [
        el(identifier="x.field", traits=["textField"]),
        el(identifier="x.go", traits=["button", "notEnabled"]),
    ]
    filled = [
        el(identifier="x.field", traits=["textField"], value="hi"),
        el(identifier="x.go", traits=["button"]),
    ]
    assert crawl.fingerprint(empty).value != crawl.fingerprint(filled).value


def _login_form(filled: bool) -> list[dict]:
    """A field + a submit button that is disabled until the field is filled."""
    return [
        el(identifier="login.user", traits=["textField"], value="abc" if filled else None),
        el(identifier="login.submit", traits=["button"] if filled else ["button", "notEnabled"]),
    ]


def test_crawl_fills_a_form_to_enable_and_press_a_disabled_button() -> None:
    """End to end: the crawl types into the field, the submit enables (a distinct screen via the
    enabling-aware fingerprint), and the crawl then presses the once-disabled button to reach the
    screen behind it."""
    home = [
        el(identifier="home.title", traits=["staticText"]),
        el(identifier="home.ok", traits=["button"]),
    ]
    focus: dict[str, object] = {"id": None}

    def submit_enabled(screen: list[dict]) -> bool:
        return any(
            e.get("identifier") == "login.submit" and "notEnabled" not in (e.get("traits") or [])
            for e in screen
        )

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict):
            focus["id"] = arg.get("id")
            if arg.get("id") == "login.submit" and submit_enabled(d.screen):
                d.screen = list(home)
        elif kind == "type" and focus["id"] == "login.user":
            d.screen = list(_login_form(filled=True))

    driver = FakeDriver(screen=list(_login_form(False)), react=react)

    def reset(d: FakeDriver) -> None:
        focus["id"] = None
        d.screen = list(_login_form(filled=False))

    screen_map = crawl.crawl(driver, reset, max_screens=50, max_steps=50)

    start_fp = crawl.fingerprint(_login_form(False)).value
    assert screen_map.nodes[start_fp].blocked == ("login.submit",)  # reported as gated
    assert any(e.action == "tap login.submit" for e in screen_map.edges)  # pressed once enabled
    assert crawl.fingerprint(home).value in screen_map.nodes  # reached the screen behind it


def test_crawl_clears_a_blocking_alert_instead_of_recording_a_crash() -> None:
    """Tapping a control pops an OS alert that collapses the app UI (looks like a crash). The
    `clear_blocking` hook dismisses it before the alive check, so the screen behind it is explored
    and no crash is recorded."""
    home = [el(identifier="home.go", traits=["button"])]
    alert = [el(traits=["application"])]  # collapsed tree — indistinguishable from a crash
    behind = [el(identifier="b.one", traits=["button"]), el(identifier="b.two", traits=["button"])]
    s = {"alerting": False}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.go":
            s["alerting"] = True
            d.screen = list(alert)

    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        s["alerting"] = False
        d.screen = list(home)

    def clear_blocking(d: FakeDriver) -> None:
        if s["alerting"]:  # the guard dismisses the prompt, revealing the screen behind it
            s["alerting"] = False
            d.screen = list(behind)

    screen_map = crawl.crawl(driver, reset, clear_blocking=clear_blocking, max_steps=50)
    assert not screen_map.crashes  # the alert was dismissed, not mistaken for a crash
    assert crawl.fingerprint(behind).value in screen_map.nodes  # the screen behind it was explored


def test_crawl_uses_a_custom_guide_for_label_based_actions() -> None:
    """The guide chooses what to try: an AI-style guide proposing a label-based tap drives a
    transition off an id-less control, which the deterministic guide would skip."""
    start = [
        el(label="Start", traits=["button"], frame=(0, 0, 80, 40)),
        el(label="hint", traits=["staticText"]),
    ]
    second = [el(identifier="s.a", traits=["button"]), el(identifier="s.b", traits=["button"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("label") == "Start":
            d.screen = list(second)

    driver = FakeDriver(screen=list(start), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(start)

    def guide(_driver: FakeDriver, elements: list[dict]) -> list[crawl.Action]:
        return [
            crawl.Action("tap", label=e["label"])
            for e in elements
            if "button" in (e.get("traits") or []) and not e.get("identifier")
        ]

    screen_map = crawl.crawl(driver, reset, guide=guide)
    assert any(e.action == "tap Start" for e in screen_map.edges)
    assert crawl.fingerprint(second).value in screen_map.nodes
