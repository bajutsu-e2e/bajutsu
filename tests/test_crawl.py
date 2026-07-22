"""Tests for the autonomous crawl engine core (bajutsu/crawl/core.py, BE-0038).

The engine explores an app breadth-first over the Driver abstraction, building a screen map.
It is exercised here entirely on the FakeDriver's multi-screen `react` model — no Simulator and
no AI — which is exactly the determinism boundary BE-0038 relies on: exploration and state
identity are deterministic functions of the element tree.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from conftest import el

from bajutsu.crawl import core as crawl
from bajutsu.crawl import serialize
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


def test_fingerprint_token_appends_state_markers_in_fixed_order() -> None:
    # An enabled, empty, unselected element contributes just its id; each interactive state adds a
    # marker, and a combination appends them in a fixed order (!, =, +) so the hash stays stable.
    assert crawl._fingerprint_token(el(identifier="btn.go")) == "btn.go"
    assert crawl._fingerprint_token(el(identifier="btn.go", traits=["notEnabled"])) == "btn.go!"
    assert (
        crawl._fingerprint_token(el(identifier="in.email", traits=["textField"], value="x"))
        == "in.email="
    )
    assert (
        crawl._fingerprint_token(el(identifier="in.email", traits=["textField"], value=""))
        == "in.email"  # empty input contributes no marker
    )
    assert crawl._fingerprint_token(el(identifier="tab.home", traits=["selected"])) == "tab.home+"
    assert (
        crawl._fingerprint_token(
            el(identifier="x", traits=["notEnabled", "textField", "selected"], value="v")
        )
        == "x!=+"
    )


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


def _tabbed_app() -> tuple[Callable[[FakeDriver, str, object], None], list[dict]]:
    """Two tab buttons (tab.home / tab.settings) sit on every screen — a global control — plus a
    screen-specific button. tab.settings -> settings, tab.home -> home, settings.detail -> detail."""
    tabs = [
        el(identifier="tab.home", traits=["button"]),
        el(identifier="tab.settings", traits=["button"]),
    ]
    home = [*tabs]
    settings = [*tabs, el(identifier="settings.detail", traits=["button"])]
    detail = [*tabs, el(identifier="detail.go", traits=["button"])]
    screens = {"home": home, "settings": settings, "detail": detail}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap" or not isinstance(arg, dict):
            return
        dest = {"tab.settings": "settings", "tab.home": "home", "settings.detail": "detail"}.get(
            str(arg.get("id"))
        )
        if dest is not None:
            d.screen = list(screens[dest])

    return react, home


def test_prune_global_explores_a_shared_control_once_and_records_the_rest() -> None:
    """With pruning on, the first screen to offer a global op (the tab buttons, reused across
    screens) explores it; later screens skip it and record a Pruned entry pointing at the owner —
    so the same op isn't re-explored from every screen, and the WebUI can show / resume it."""
    react, home = _tabbed_app()
    fp_home = crawl.fingerprint(home).value

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    pruned_on = crawl.crawl(
        FakeDriver(screen=list(home), react=react), reset, prune_global=True, max_steps=100
    )
    # The tab buttons are claimed by home; from settings they're pruned, not re-explored.
    assert any(p.key == "tab.home" and p.owner == fp_home for p in pruned_on.pruned), (
        pruned_on.pruned
    )
    assert not any(e.action == "tap tab.home" and e.src != fp_home for e in pruned_on.edges), (
        "a pruned global op must not be explored from another screen"
    )

    # Without pruning, every screen re-explores the tabs, so the tab op fires from more than home.
    pruned_off = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=100)
    assert not pruned_off.pruned
    assert any(e.action == "tap tab.home" and e.src != fp_home for e in pruned_off.edges)
    # Serialized maps carry the pruned list (empty when pruning is off).
    assert serialize.screenmap_dict(pruned_on)["pruned"]
    assert serialize.screenmap_dict(pruned_off)["pruned"] == []


def test_resume_a_pruned_branch_explores_it_and_appends_to_the_map() -> None:
    """A pruned op carries a replayable `path`; feeding it back as a resume seed (base_map +
    seed_path/ops) re-walks to the screen, performs the op, and continues exploring — merging new
    findings into the existing map. Here, resuming a pruned tab op from `detail` reaches a screen
    only reachable that way, proving the pruned branch can be explored on demand."""
    tabs = [
        el(identifier="tab.home", traits=["button"]),
        el(identifier="tab.extra", traits=["button"]),
    ]
    home = [*tabs, el(identifier="home.detail", traits=["button"])]
    detail = [*tabs, el(identifier="detail.mark", traits=["button"])]
    # From detail, the (globally-pruned) tab.extra leads somewhere new — a screen no other path hits.
    extra = [
        el(identifier="extra.a", traits=["button"]),
        el(identifier="extra.b", traits=["button"]),
    ]
    screens = {"home": home, "detail": detail, "extra": extra}
    here = {"v": "home"}

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap" or not isinstance(arg, dict):
            return
        tgt = str(arg.get("id"))
        dest = {"tab.home": "home", "home.detail": "detail"}.get(tgt)
        if tgt == "tab.extra":  # tab.extra only opens `extra` once you're on `detail`
            dest = "extra" if here["v"] == "detail" else "home"
        if dest is not None:
            here["v"] = dest
            d.screen = list(screens[dest])

    def reset(d: FakeDriver) -> None:
        here["v"] = "home"
        d.screen = list(home)

    base = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, prune_global=True)
    # tab.extra got pruned somewhere (claimed by home), and `extra` was never reached.
    pruned = next(p for p in base.pruned if p.key == "tab.extra")
    assert crawl.fingerprint(extra).value not in base.nodes

    resumed = crawl.crawl(
        FakeDriver(screen=list(home), react=react),
        reset,
        base_map=base,
        seed_path=list(pruned.path[:-1]),  # replay to the screen the op was pruned on
        seed_ops=[pruned.path[-1]],  # the pruned op itself, now explored
    )
    # Resuming reached the previously-unreachable screen and added it to the same map.
    assert crawl.fingerprint(extra).value in resumed.nodes
    assert any(e.dst == crawl.fingerprint(extra).value for e in resumed.edges)


def test_continue_reconstructs_the_full_frontier_and_finishes_the_crawl() -> None:
    """A crawl stopped on a budget leaves a frontier in `plan`; feeding that map back as `base_map`
    without a seed path (BE-0181 full-frontier continuation) reconstructs every screen's untried ops
    from `paths` + `plan`, keeps exploring, and completes the map a bigger budget would have."""
    react, home = _three_screen_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    # A tight budget stops after one action, so `about` is never reached and a frontier remains.
    partial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=1)
    assert partial.stop_reason == "max_steps"
    assert len(partial.nodes) < 3
    assert any(partial.plan.values()), "the budget stop must leave untried operations to continue"

    # Continue: base_map, no seed_path/seed_ops — the whole remaining frontier, not one branch.
    continued = crawl.crawl(
        FakeDriver(screen=list(home), react=react),
        reset,
        base_map=partial,
        max_screens=50,
        max_steps=100,
    )
    assert len(continued.nodes) == 3  # home, settings, about — the full map
    assert crawl.fingerprint(_three_screen_app()[1]).value in continued.nodes
    assert continued.stop_reason == "completed"  # re-decided by the continuation, not inherited


def test_continue_through_a_dict_round_trip_matches_a_full_crawl() -> None:
    """The real path: the partial map is persisted to `screenmap.json` and reloaded (as the CLI's
    `--continue` does), then continued. The continued map reaches the same screens a single
    uninterrupted crawl of the deterministic app would."""
    react, home = _three_screen_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    full = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=100)
    partial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=1)
    reloaded = serialize.screenmap_from_dict(serialize.screenmap_dict(partial))
    continued = crawl.crawl(
        FakeDriver(screen=list(home), react=react), reset, base_map=reloaded, max_steps=100
    )
    assert set(continued.nodes) == set(full.nodes)


def test_continue_with_no_frontier_is_a_noop_and_reports_completed() -> None:
    """Continuing a map that already explored everything finds nothing new: the reconstructed
    frontier is empty, so the crawl returns the same nodes and reports `completed`."""
    react, home = _three_screen_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    done = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=100)
    assert done.stop_reason == "completed" and not any(done.plan.values())
    again = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, base_map=done)
    assert set(again.nodes) == set(done.nodes)
    assert again.stop_reason == "completed"


def test_continue_runs_the_worker_pool_in_parallel() -> None:
    """Unlike a single-branch resume (one walk), a full-frontier continuation keeps `extra_workers`
    (BE-0181): a partial crawl of a wide hub is continued across a worker pool, reaches the same
    screens the serial crawl of the whole app does, and genuinely shares the work across devices."""
    react, home = _wide_app(8)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    serial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=100)
    partial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=2)
    assert partial.stop_reason == "max_steps"

    # A small settle (device-latency stand-in) lets the extra workers actually overlap the primary
    # instead of it draining the synchronous fake alone — the latency the parallel design overlaps.
    def settle(_d: FakeDriver) -> None:
        time.sleep(0.002)

    drivers, factories = _pool(react, home, 4)  # 4 workers: primary + 3 extra-worker factories
    continued = crawl.crawl(
        drivers[0], reset, base_map=partial, max_steps=100, settle=settle, extra_workers=factories
    )
    assert set(continued.nodes) == set(serial.nodes)
    # The continuation genuinely used the pool (not a silently-serial walk): more than one device
    # explored the reconstructed frontier. The extras only tap during exploration, so an extra having
    # tapped proves the pool drove the continuation — this fails if `extra_workers` were dropped.
    extras_acted = sum(1 for d in drivers[1:] if any(a[0] == "tap" for a in d.actions))
    assert extras_acted >= 1


def test_continue_skips_a_screen_whose_recorded_path_no_longer_resolves() -> None:
    """App drift between the original crawl and the continuation: one frontier screen's recorded path
    no longer replays (a selector is gone), the other still does. The stale screen is skipped, the
    live one is still explored, and the crawl neither raises nor aborts the whole continuation."""
    react, home = _three_screen_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    partial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=1)
    # Hand-inject a frontier screen whose recorded path taps an id the reset screen doesn't have, so
    # its replay fails (SelectorError); `home`'s own remaining op (home.about) still resolves.
    ghost = "ghost-fingerprint"
    partial.paths[ghost] = (crawl.Action("tap", target="does.not.exist"),)
    partial.plan[ghost] = ["tap does.not.exist"]

    continued = crawl.crawl(
        FakeDriver(screen=list(home), react=react), reset, base_map=partial, max_steps=100
    )
    # The stale ghost screen contributed nothing, but the reachable frontier (home → about) was still
    # explored: all three real screens are present and the run didn't crash on the broken path.
    assert crawl.fingerprint(_three_screen_app()[1]).value in continued.nodes
    assert len(continued.nodes) == 3


def test_continue_skips_a_screen_whose_replay_lands_on_a_different_fingerprint() -> None:
    """App drift where a recorded path still replays cleanly but now reaches a *different* screen: the
    landed fingerprint no longer matches the planned one, so that screen must be skipped rather than
    seeded under the stale fingerprint (which would misattribute the continuation's edges)."""
    react, home = _three_screen_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    partial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=1)
    # Inject a frontier entry with an empty path (so replay trivially succeeds and lands on `home`)
    # but a fingerprint that isn't home's — modeling a screen whose identity changed under us.
    bogus = "bogus-fingerprint"
    partial.paths[bogus] = ()
    partial.plan[bogus] = ["tap home.settings"]

    continued = crawl.crawl(
        FakeDriver(screen=list(home), react=react), reset, base_map=partial, max_steps=100
    )
    # The bogus screen was neither seeded nor attributed any edge, and the real frontier still
    # finished: no edge claims to originate from the stale fingerprint.
    assert bogus not in continued.nodes
    assert not any(e.src == bogus for e in continued.edges)
    assert len(continued.nodes) == 3


def test_continue_preserves_a_frontier_it_cannot_reconstruct_rather_than_completing() -> None:
    """The recorded frontier is real but not deterministically reconstructable — its only untried op
    is an AI-only one (`tap_point`, which `candidate_actions` never re-derives). Reconstruction
    re-seeds nothing, but it must NOT wipe the saved plan or mislabel the run `completed`: the work
    still exists in the map, so the plan and the prior stop reason survive for a retry (BE-0181)."""
    react, home = _three_screen_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    home_fp = crawl.fingerprint(home).value
    # A map whose sole frontier op is a vision-located tap — `candidate_actions(home)` yields the
    # deterministic taps (home.settings / home.about), none of which describe as this op, so nothing
    # is re-seeded even though the path replays cleanly and the fingerprint matches.
    ai_op = crawl.Action("tap_point", point=(0.5, 0.9)).describe()
    base = crawl.ScreenMap(
        nodes={home_fp: crawl.Node(home_fp, "id", ("home.about", "home.settings"), ())},
        plan={home_fp: [ai_op]},
        paths={home_fp: ()},
        stop_reason="max_steps",
    )
    continued = crawl.crawl(
        FakeDriver(screen=list(home), react=react), reset, base_map=base, max_steps=100
    )
    # Nothing reconstructed → the crawl added no screens, kept the recorded frontier verbatim, and
    # did not falsely report completion.
    assert continued.stop_reason == "max_steps"
    assert continued.plan == {home_fp: [ai_op]}  # preserved, not overwritten with {}


def test_continue_that_hits_a_budget_again_reports_the_new_reason_and_keeps_a_frontier() -> None:
    """A continuation re-decides its own stop reason (it doesn't inherit the prior run's): continuing
    a map that stopped on `max_steps` with a still-too-small budget stops on `max_steps` again and
    leaves a frontier, so it stays continuable — proving the stop_reason reset, not an inherited value."""
    react, home = _wide_app(8)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    partial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, max_steps=1)
    assert partial.stop_reason == "max_steps" and len(partial.nodes) < 9

    continued = crawl.crawl(
        FakeDriver(screen=list(home), react=react),
        reset,
        base_map=partial,
        max_screens=50,
        max_steps=3,
    )
    assert continued.stop_reason == "max_steps"  # re-decided by this run, not inherited
    assert len(continued.nodes) < 9  # still incomplete
    assert any(continued.plan.values())  # a frontier remains, so it's still continuable


def test_screenmap_round_trips_through_dict_for_resume() -> None:
    """A saved map reloads with its nodes, edges and pruned replay paths intact, so a resume can use
    it as the base."""
    react, home = _tabbed_app()

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    original = crawl.crawl(FakeDriver(screen=list(home), react=react), reset, prune_global=True)
    back = serialize.screenmap_from_dict(serialize.screenmap_dict(original))
    assert set(back.nodes) == set(original.nodes)
    assert len(back.edges) == len(original.edges)
    assert [(p.src, p.key, p.path) for p in back.pruned] == [
        (p.src, p.key, p.path) for p in original.pruned
    ]


def test_action_dict_round_trips_every_kind() -> None:
    for a in [
        crawl.Action("tap", target="a"),
        crawl.Action("type", target="f", value="v"),
        crawl.Action("tap", label="L", index=2),
        crawl.Action("fill", fields=(("a", "1"), ("b", "2"))),
        crawl.Action("tap_point", label="Home", point=(0.5, 0.9)),
    ]:
        assert serialize.action_from_dict(serialize.action_to_dict(a)) == a


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


# --- parallel crawl across simulators (BE-0064) --------------------------------------------


def _wide_app(n: int) -> tuple[Callable[[FakeDriver, str, object], None], list[dict]]:
    """A hub (`home`) with `n` distinct leaf screens, each reached by its own button and returning
    home — `n` independent frontier branches for workers to share. The react is a pure function of
    the tap target, so every worker's own FakeDriver explores it independently."""
    home = [el(identifier=f"home.leaf{i}", traits=["button"]) for i in range(n)]
    leaves = {
        f"leaf{i}": [
            el(identifier=f"leaf{i}.marker", traits=["staticText"]),
            el(identifier=f"leaf{i}.back", traits=["button"]),
        ]
        for i in range(n)
    }
    screens = {"home": home, **leaves}
    dest = {f"home.leaf{i}": f"leaf{i}" for i in range(n)}
    dest.update({f"leaf{i}.back": "home" for i in range(n)})

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap" or not isinstance(arg, dict):
            return
        nxt = dest.get(str(arg.get("id")))
        if nxt is not None:
            d.screen = list(screens[nxt])

    return react, home


def _edge_set(sm: crawl.ScreenMap) -> set[tuple[str, str, str]]:
    return {(e.src, e.action, e.dst) for e in sm.edges}


def _pool(
    react: Callable[[FakeDriver, str, object], None], home: list[dict], n: int
) -> tuple[list[FakeDriver], list[crawl.WorkerFactory]]:
    """The `n` driver objects (index 0 = primary) plus the `n-1` extra-worker factories that hand
    them out. Each factory returns a pre-made FakeDriver (thread-agnostic) when the engine calls it
    on that worker's own thread (BE-0077); the driver list lets a test inspect what each worker did."""

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    drivers = [FakeDriver(screen=list(home), react=react) for _ in range(n)]
    return drivers, [(lambda d=d: (d, reset)) for d in drivers[1:]]


def test_parallel_crawl_discovers_the_same_map_as_serial() -> None:
    """The crux of BE-0064: across N simulators the *set* of screens and transitions discovered is
    identical to the serial crawl of the same deterministic app — only ordering/path metadata may
    differ. Here a 4-worker crawl of a wide app matches the single-worker map exactly."""
    react, home = _wide_app(8)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    serial = crawl.crawl(FakeDriver(screen=list(home), react=react), reset)

    # A small settle (a stand-in for real device latency) lets the workers actually overlap instead
    # of the primary draining the synchronous fake before the others wake — the very latency the
    # parallel design overlaps across simulators.
    def settle(_d: FakeDriver) -> None:
        time.sleep(0.002)

    drivers, extra = _pool(react, home, 4)
    parallel = crawl.crawl(drivers[0], reset, settle=settle, extra_workers=extra)

    assert set(parallel.nodes) == set(serial.nodes)  # same 9 screens (home + 8 leaves)
    assert _edge_set(parallel) == _edge_set(serial)  # same transitions, regardless of scheduling
    assert parallel.stop_reason == "completed"
    # The work was genuinely shared: more than one simulator performed taps.
    acted = sum(1 for d in drivers if any(a[0] == "tap" for a in d.actions))
    assert acted >= 2


def test_parallel_crawl_honors_the_screen_budget() -> None:
    """`--max-screens` is a shared counter under the lock. With N workers it can overshoot only by
    the in-flight discoveries (at most one per worker), never unbounded."""
    react, home = _wide_app(12)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    drivers, extra = _pool(react, home, 4)
    sm = crawl.crawl(drivers[0], reset, max_screens=5, extra_workers=extra)
    assert 5 <= len(sm.nodes) <= 5 + 4  # the cap, plus at most one in-flight discovery per worker
    assert sm.stop_reason == "max_screens"


def test_parallel_crawl_isolates_a_wedged_device() -> None:
    """One bad simulator can't sink the crawl: a worker whose device errors on every action hands
    its frontier entries back and retires, while the healthy worker still maps the whole app."""
    react, home = _wide_app(6)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    def wedged(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "tap":
            raise crawl.device_errors.DeviceError("simulator wedged")

    healthy = FakeDriver(screen=list(home), react=react)
    bad = FakeDriver(screen=list(home), react=wedged)
    sm = crawl.crawl(healthy, reset, extra_workers=[lambda: (bad, reset)])

    assert len(sm.nodes) == 7  # home + 6 leaves, all found by the healthy device
    assert sm.stop_reason == "completed"


def test_lone_worker_surfaces_a_device_error() -> None:
    """Without a pool there's nothing to fall back to, so a device error propagates as it always
    did (the serial engine's behavior) rather than being silently swallowed."""
    home = [el(identifier="home.a", traits=["button"]), el(identifier="home.b", traits=["button"])]

    def boom(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "tap":
            raise crawl.device_errors.DeviceError("device gone")

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    try:
        crawl.crawl(FakeDriver(screen=list(home), react=boom), reset)
    except crawl.device_errors.DeviceError:
        return
    raise AssertionError("a lone worker's device error must propagate")


def test_parallel_crawl_recovers_a_wedged_lane_instead_of_retiring() -> None:
    """With a `recover` hook (web's browser relaunch, BE-0077), a worker whose device wedges hands
    its frontier entry back, heals its lane, and keeps crawling — rather than retiring the lane as
    the iOS default does. The fault is keyed to an *action* (the first open of leaf0), not a worker,
    so it fires deterministically whichever worker reaches it; the whole app is still mapped."""
    react, home = _wide_app(6)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    wedged = {"leaf0": False}  # leaf0's first open wedges once; only one worker holds it at a time

    def flaky(d: FakeDriver, kind: str, arg: object) -> None:
        opening_leaf0 = kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.leaf0"
        if opening_leaf0 and not wedged["leaf0"]:
            wedged["leaf0"] = True
            raise crawl.device_errors.DeviceError("browser wedged")
        react(d, kind, arg)

    recovered = {"n": 0}

    def recover(_d: FakeDriver) -> None:
        recovered["n"] += 1

    drivers = [FakeDriver(screen=list(home), react=flaky) for _ in range(2)]
    sm = crawl.crawl(
        drivers[0], reset, recover=recover, extra_workers=[lambda: (drivers[1], reset)]
    )

    assert len(sm.nodes) == 7  # home + 6 leaves — nothing lost, the lane was not retired
    assert sm.stop_reason == "completed"
    assert recovered["n"] == 1  # exactly one wedge, healed in place rather than retired


def test_lone_worker_ignores_recover_and_surfaces_the_error() -> None:
    """`recover` only heals a pool lane; a lone worker has no healthy peer to fall back to, so its
    device error still propagates and recover never fires — a missing pool can't silently swallow a
    real failure (prime directive #2)."""
    home = [el(identifier="home.a", traits=["button"])]

    def boom(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "tap":
            raise crawl.device_errors.DeviceError("device gone")

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    calls = {"n": 0}

    def recover(_d: FakeDriver) -> None:
        calls["n"] += 1

    try:
        crawl.crawl(FakeDriver(screen=list(home), react=boom), reset, recover=recover)
    except crawl.device_errors.DeviceError:
        assert calls["n"] == 0  # recover was never called for a lone worker
        return
    raise AssertionError("a lone worker's device error must propagate even with recover set")


def test_parallel_crawl_retires_a_lane_that_never_heals_instead_of_looping() -> None:
    """The recover safety valve: a browser that keeps wedging even after relaunch must not busy-loop
    recover() forever. The wedged worker is the primary, so it deterministically faults on its first
    action; recover (a no-op stand-in here — the lane stays broken) fires at most MAX-1 times, then
    the lane retires after MAX faults in a row, and the healthy worker still maps the whole app."""
    react, home = _wide_app(6)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    def always_wedged(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "tap":
            raise crawl.device_errors.DeviceError("browser wedged")

    recovered = {"n": 0}

    def recover(_d: FakeDriver) -> None:  # the lane stays broken; recovery never actually heals it
        recovered["n"] += 1

    # A small settle throttles the healthy worker (it settles after each observe) so the wedged
    # primary — whose taps fault *before* any observe — reliably runs through its fault budget rather
    # than the synchronous fake being fully mapped before the primary acts.
    def settle(_d: FakeDriver) -> None:
        time.sleep(0.002)

    primary = FakeDriver(screen=list(home), react=always_wedged)
    healthy = FakeDriver(screen=list(home), react=react)
    sm = crawl.crawl(
        primary, reset, recover=recover, settle=settle, extra_workers=[lambda: (healthy, reset)]
    )

    assert len(sm.nodes) == 7  # the healthy worker maps everything despite the unhealable lane
    assert sm.stop_reason == "completed"
    # recover was attempted but bounded — the lane retired rather than looping forever.
    assert 1 <= recovered["n"] <= crawl._MAX_WORKER_DEVICE_ERRORS - 1


def test_extra_worker_driver_is_built_on_its_own_thread() -> None:
    """BE-0077: each extra worker's `(driver, reset)` lane is built by its factory *inside that
    worker's thread*, never on the main thread — required because Playwright's sync API is bound to
    the thread that creates it (a main-thread driver driven from a worker thread raises
    `greenlet.error: cannot switch to a different thread`). A factory that records the thread it ran
    on proves the lane was built off the main thread."""
    import threading

    react, home = _wide_app(4)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    main_ident = threading.get_ident()
    built_on: list[int] = []

    def factory() -> tuple[FakeDriver, crawl.Reset]:
        built_on.append(threading.get_ident())
        return FakeDriver(screen=list(home), react=react), reset

    primary = FakeDriver(screen=list(home), react=react)
    crawl.crawl(primary, reset, extra_workers=[factory])

    assert built_on, "the extra worker's factory was never called"
    assert all(ident != main_ident for ident in built_on)  # built on a worker thread, not main


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

    data = serialize.screenmap_dict(crawl.crawl(driver, reset))
    assert set(data) == {
        "nodes",
        "edges",
        "crashes",
        "alerts",
        "plan",
        "paths",
        "pruned",
        "stop_reason",
    }
    assert isinstance(data["nodes"], list) and data["nodes"]
    assert all({"fingerprint", "kind", "ids", "actions"} <= set(n) for n in data["nodes"])
    assert all({"src", "action", "dst", "alert"} == set(e) for e in data["edges"])
    # Every discovered screen carries the replayable path that reached it (empty for the entry).
    assert isinstance(data["paths"], dict)
    assert set(data["paths"]) == {n["fingerprint"] for n in data["nodes"]}
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
    """A SwiftUI tab bar the accessibility tree can't address is reached via a coordinate tab tap from the guide (the
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

    def on_node(d: FakeDriver, node: crawl.Node) -> None:
        seen.append(node.fingerprint)
        d.screenshot(f"{node.fingerprint}.png")

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


def test_node_targets_normalize_tap_rectangles() -> None:
    """Each candidate action carries the on-screen rectangle it taps, normalized to [0,1] of the
    screen and keyed by the action's description — what the web UI overlays on the screenshot to
    show where a transition's tap lands. A coordinate (vision) tab yields a small box at its point."""
    elements = [
        el(traits=["application"], frame=(0, 0, 200, 400)),  # the window spans the screen
        el(identifier="a", traits=["button"], frame=(40, 100, 120, 50)),
        el(identifier="b", traits=["button"], frame=(40, 300, 120, 50)),
    ]
    actions = [*crawl.candidate_actions(elements), crawl.Action("tap_point", point=(0.5, 0.9))]
    node = crawl._node_of(crawl.fingerprint(elements), elements, actions)
    t = dict(node.targets)
    # Screen size = the window frame (200 x 400); a frame normalizes to that.
    assert t["tap a"] == (40 / 200, 100 / 400, 120 / 200, 50 / 400)
    # The coordinate tab maps to a small box centred on its normalized point.
    x, y, w, h = t["tap point (0.50, 0.90)"]
    assert x < 0.5 < x + w and y < 0.9 < y + h
    # Serialized as a plain {description: [x, y, w, h]} object.
    data = serialize.screenmap_dict(crawl.ScreenMap(nodes={node.fingerprint: node}))
    assert data["nodes"][0]["targets"]["tap b"] == [40 / 200, 300 / 400, 120 / 200, 50 / 400]


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


# --- platform-dispatched health check (the web crash-detection seam, BE-0066) ---------------


def test_is_alive_dispatch_records_a_crash_via_injected_health_check() -> None:
    """The engine's liveness check is injectable so a non-iOS backend can supply its own crash
    signal (web: pageerror / HTTP status / blank DOM). Here a health check flags the landed
    screen as dead, so the engine records a Crash on that path without any accessibility-tree
    logic — and stops treating it as a normal screen."""
    home = [el(identifier="home.boom", traits=["button"], frame=(0, 0, 100, 40))]
    broken = [el(identifier="error.page", traits=["button"], frame=(0, 200, 100, 40))]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.boom":
            d.screen = list(broken)

    def reset(d: FakeDriver) -> None:
        d.screen = list(home)

    def is_alive(_d: FakeDriver, elements: list[dict]) -> bool:
        return not any(e.get("identifier") == "error.page" for e in elements)

    screen_map = crawl.crawl(
        FakeDriver(screen=list(home), react=react), reset, is_alive=is_alive, max_steps=20
    )
    assert any(c.path == ("tap home.boom",) for c in screen_map.crashes)
    # the dead screen is recorded as a crash, not as a normal edge/node
    assert not any(e.action == "tap home.boom" for e in screen_map.edges)
    assert crawl.fingerprint(broken).value not in screen_map.nodes


# --- _action_targets (per-action tap rectangles, normalized to the screen) -----------------


def test_action_targets_normalizes_to_screen_max_dimensions() -> None:
    """The screen size is the max frame width/height over all elements, in a single pass;
    each tappable element's rectangle is normalized against it."""
    elements = [
        el(identifier="a", traits=["button"], frame=(0.0, 0.0, 50.0, 20.0)),
        el(identifier="b", traits=["button"], frame=(100.0, 200.0, 200.0, 400.0)),
    ]
    actions = [crawl.Action(kind="tap", target="a")]
    targets = crawl._action_targets(elements, actions)
    # screen is (max width 200, max height 400); element "a" at (0,0,50,20) normalizes to those.
    assert targets == (("tap a", (0.0, 0.0, 50.0 / 200.0, 20.0 / 400.0)),)


def test_action_targets_empty_without_dimensions() -> None:
    """A zero-sized frame gives no derivable screen size, so there are no targets."""
    elements = [el(identifier="a", traits=["button"], frame=(0.0, 0.0, 0.0, 0.0))]
    assert crawl._action_targets(elements, [crawl.Action(kind="tap", target="a")]) == ()


# --- `bajutsu crawl` CLI option validation (BE-0117) ---
#
# These drive the option-validation and dispatch branches that fail before any device work — they
# need no live actuator. iOS backend selection fails cleanly in the sandbox (no XCUITest tooling on PATH), which
# is how the "unavailable backend" branch is reached.

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from bajutsu.cli import app  # noqa: E402

_cli = CliRunner()


def _crawl_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    return cfg


def test_cli_crawl_fails_closed_without_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)  # no .env key leak-in
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "anthropic.Anthropic",
        lambda *a, **k: pytest.fail("client constructed despite missing credential"),
    )
    cfg = _crawl_config(tmp_path)
    # fake backend selects fine, so the guide's missing credential (default anthropic provider) fails.
    r = _cli.invoke(app, ["crawl", "--target", "demo", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no AI credential" in r.output and "ANTHROPIC_API_KEY" in r.output


# --- screen_identity: transition signature that ignores per-element state (BE-0178) ---


def test_screen_identity_structural_ignores_state_traits() -> None:
    # On an id-poor screen (structural fallback), a control merely enabling mid-batch must NOT read
    # as a transition — else a form-fill batch that enables Submit would wrongly abort.
    disabled = [el(label="Submit", traits=["button", "notEnabled"])]
    enabled = [el(label="Submit", traits=["button"])]
    assert crawl.screen_identity(disabled) == crawl.screen_identity(enabled)
    # fingerprint stays state-sensitive (the crawl explores distinct control-state combinations).
    assert crawl.fingerprint(disabled) != crawl.fingerprint(enabled)


def test_screen_identity_structural_changes_when_an_element_appears() -> None:
    one = [el(label="A", traits=["button"])]
    two = [
        el(label="A", traits=["button"]),
        el(label="B", traits=["button"], frame=(0, 20, 10, 10)),
    ]
    assert crawl.screen_identity(one) != crawl.screen_identity(two)  # a real transition


def test_screen_identity_is_kind_prefixed() -> None:
    # Crossing the id-count threshold changes kind, so the identity differs (a transition worth
    # aborting on) — the prefix rules out an accidental id/structural hash collision.
    ids = [
        el(identifier="x0", traits=["button"]),
        el(identifier="x1", traits=["button"], frame=(0, 20, 10, 10)),
    ]
    struct = [el(label="A", traits=["button"])]
    assert crawl.screen_identity(ids).startswith("id:")
    assert crawl.screen_identity(struct).startswith("structural:")
