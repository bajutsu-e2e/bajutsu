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
    assert set(data) == {"nodes", "edges", "crashes", "alerts", "plan", "stop_reason"}
    assert isinstance(data["nodes"], list) and data["nodes"]
    assert all({"fingerprint", "kind", "ids", "actions"} <= set(n) for n in data["nodes"])
    assert all({"src", "action", "dst", "alert"} == set(e) for e in data["edges"])
    # A fully explored app has no pending operations left, so the plan is empty.
    assert data["plan"] == {}
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


def test_crawl_exposes_the_live_plan_via_on_event() -> None:
    """The plan (still-untried operations per screen) is refreshed on every event, so a watcher
    can visualize the frontier as it shrinks. The start screen has pending operations early on,
    and once the app is fully explored the plan is empty."""
    react, home = _three_screen_app()
    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    plans: list[dict[str, list[str]]] = []

    def on_event(sm: crawl.ScreenMap) -> None:
        plans.append({fp: list(ops) for fp, ops in sm.plan.items()})

    screen_map = crawl.crawl(driver, reset, on_event=on_event)
    assert any(plans)  # at some point the frontier held untried operations
    assert all(ops for plan in plans for ops in plan.values())  # only non-empty entries are kept
    assert screen_map.plan == {}  # fully explored -> nothing left to try


def test_crawl_plans_and_explores_a_vision_located_tab() -> None:
    """A SwiftUI tab bar idb can't address is reached via a coordinate tab tap from the guide (the
    vision fallback). That planned tab must surface in the live plan (the frontier a watcher sees)
    and drive a real transition edge — exactly like an id-based action, since a coordinate tap is
    deterministic to replay."""
    home = [
        el(label="Tab Bar", traits=["group"], frame=(0, 800, 400, 80)),
        el(identifier="home.title", traits=["staticText"]),
        el(identifier="home.body", traits=["staticText"]),
    ]
    search = [
        el(identifier="search.title", traits=["staticText"]),
        el(identifier="search.body", traits=["staticText"]),
    ]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap_point":
            d.screen = list(search)

    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    def guide(
        _drv: FakeDriver, elements: list[dict], _ctx: crawl.GuideContext
    ) -> list[crawl.Action]:
        # Mimic the vision fallback: a coordinate tab tap only while the un-addressable bar is shown.
        if any((e.get("label") or "") == "Tab Bar" for e in elements):
            return [crawl.Action("tap_point", label="Search", point=(0.5, 0.95))]
        return []

    plans: list[list[str]] = []

    def on_event(sm: crawl.ScreenMap) -> None:
        plans.append([op for ops in sm.plan.values() for op in ops])

    screen_map = crawl.crawl(driver, reset, guide=guide, on_event=on_event)
    # The vision tab was queued as an exploration target (the frontier), then explored into an edge.
    assert any("tap tab 'Search'" in op for ops in plans for op in ops)
    assert any(e.action == "tap tab 'Search'" for e in screen_map.edges)
    assert any(n for n in screen_map.nodes.values() if "search.body" in n.ids)


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


def test_candidate_actions_tab_bar_items_are_tap_candidates_first() -> None:
    elements = [
        el(identifier="content.a", traits=["button"]),
        el(identifier="tab.home", traits=["tab", "selected"]),  # the active tab
        el(identifier="tab.settings", traits=["tab"]),
    ]
    actions = crawl.candidate_actions(elements)
    targets = [a.target for a in actions]
    # Tabs come first (switch the whole view before drilling into content), then other taps.
    assert targets == ["tab.home", "tab.settings", "content.a"]


def test_crawl_switches_tabs_and_explores_each_tab() -> None:
    """Tab bar items are tap candidates, so the crawl switches tabs and explores each tab's view."""

    def view(active: str) -> list[dict]:
        return [
            el(identifier="tab.a", traits=["tab", *(["selected"] if active == "a" else [])]),
            el(identifier="tab.b", traits=["tab", *(["selected"] if active == "b" else [])]),
            el(identifier=f"{active}.content", traits=["button"]),
        ]

    s = {"tab": "a"}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and str(arg.get("id")) in ("tab.a", "tab.b"):
            s["tab"] = str(arg.get("id")).split(".")[1]
            d.screen = list(view(s["tab"]))

    driver = FakeDriver(screen=list(view("a")), react=react)

    def reset(d: FakeDriver) -> None:
        s["tab"] = "a"
        d.screen = list(view("a"))

    screen_map = crawl.crawl(driver, reset, max_steps=100)
    assert any(e.action == "tap tab.b" for e in screen_map.edges)  # switched to the second tab
    assert crawl.fingerprint(view("b")).value in screen_map.nodes  # explored the second tab's view


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


def test_action_tap_point_scales_a_normalized_coordinate_to_the_screen() -> None:
    """A vision-located tab is stored normalized [0,1] and replayed against the live screen size
    (the largest element frame), so the same Action taps the same point regardless of pixel scale."""
    driver = FakeDriver(screen=[el(identifier="root", frame=(0, 0, 400, 800))])
    crawl.Action("tap_point", label="Search", point=(0.5, 0.95)).perform(driver)
    assert ("tap_point", (200.0, 760.0)) in driver.actions


def test_action_tap_point_describe_and_key() -> None:
    a = crawl.Action("tap_point", label="Home", point=(0.1, 0.9))
    b = crawl.Action("tap_point", point=(0.1, 0.9))  # same coordinate, no label
    assert a.describe() == "tap tab 'Home'"
    assert b.describe() == "tap point (0.10, 0.90)"
    # The coordinate keys de-dup, so the labelled and unlabelled taps at the same point collapse.
    assert a.key == b.key


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


def test_crawl_taps_through_an_alert_marks_the_edge_and_replays_through_it() -> None:
    """Tapping `home.go` pops an OS alert that collapses the app UI (looks like a crash). The
    guard dismisses it, revealing `mid` (two buttons); the home→mid edge is marked and no crash
    is recorded. The forward walk explores `mid.a` → `screen_a` (which returns home), exhausting
    that branch; to try `mid.b` it must *backtrack* — reset and replay `tap home.go`, popping the
    alert again — so `screen_b` is reached only if replay dismisses the alert too."""
    home = [
        el(identifier="home.go", traits=["button"]),
        el(identifier="home.t", traits=["staticText"]),
    ]
    alert = [el(traits=["application"])]  # collapsed tree — indistinguishable from a crash
    mid = [el(identifier="mid.a", traits=["button"]), el(identifier="mid.b", traits=["button"])]
    screen_a = [
        el(identifier="a.back", traits=["button"]),
        el(identifier="a.t", traits=["staticText"]),
    ]
    screen_b = [el(identifier="b.x", traits=["button"]), el(identifier="b.y", traits=["button"])]
    s = {"screen": "home"}
    nav = {"home.go": "alert", "mid.a": "a", "mid.b": "b", "a.back": "home"}
    screens = {"home": home, "alert": alert, "mid": mid, "a": screen_a, "b": screen_b}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and (dst := nav.get(str(arg.get("id")))):
            s["screen"] = dst
            d.screen = list(screens[dst])

    driver = FakeDriver(screen=list(home), react=react)

    def reset(d: FakeDriver) -> None:
        s["screen"] = "home"
        d.screen = list(home)

    def clear_blocking(d: FakeDriver) -> list[str]:
        if s["screen"] == "alert":  # the guard dismisses the prompt, revealing `mid`
            s["screen"] = "mid"
            d.screen = list(mid)
            return ["Allow"]
        return []

    screen_map = crawl.crawl(driver, reset, clear_blocking=clear_blocking, max_steps=100)
    assert not screen_map.crashes  # the alert was dismissed, not mistaken for a crash
    assert any(e.alert == ("Allow",) for e in screen_map.edges)  # the home→mid edge is marked
    assert screen_map.alerts and screen_map.alerts[0].path[-1] == "tap home.go"  # recorded once
    assert crawl.fingerprint(screen_a).value in screen_map.nodes  # explored forward
    # `screen_b` is reached only by backtracking — replaying `tap home.go` through the alert again.
    assert crawl.fingerprint(screen_b).value in screen_map.nodes


def test_crawl_walks_forward_without_resetting_on_a_linear_chain() -> None:
    """The forward walk reaches the end of a linear chain with a single reset (the initial one)
    instead of resetting + replaying for each screen — the efficiency the strategy buys."""
    chain = ["home", "a", "b", "c"]
    screens = {
        name: [
            el(identifier=f"{name}.go", traits=["button"]),
            el(identifier=f"{name}.t", traits=["staticText"]),
        ]
        for name in chain
    }
    nav = {"home.go": "a", "a.go": "b", "b.go": "c"}  # c.go leads nowhere (a leaf self-loop)
    s = {"screen": "home"}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and (dst := nav.get(str(arg.get("id")))):
            s["screen"] = dst
            d.screen = list(screens[dst])

    driver = FakeDriver(screen=list(screens["home"]), react=react)
    resets = 0

    def reset(d: FakeDriver) -> None:
        nonlocal resets
        resets += 1
        s["screen"] = "home"
        d.screen = list(screens["home"])

    screen_map = crawl.crawl(driver, reset, max_steps=100)
    assert len(screen_map.nodes) == 4  # home, a, b, c all discovered
    assert resets == 1  # only the initial reset — the walk continued forward, never backtracked


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

    def guide(
        _driver: FakeDriver, elements: list[dict], _ctx: crawl.GuideContext
    ) -> list[crawl.Action]:
        return [
            crawl.Action("tap", label=e["label"])
            for e in elements
            if "button" in (e.get("traits") or []) and not e.get("identifier")
        ]

    screen_map = crawl.crawl(driver, reset, guide=guide)
    assert any(e.action == "tap Start" for e in screen_map.edges)
    assert crawl.fingerprint(second).value in screen_map.nodes
