"""Direct unit tests for the crawl coordinator (bajutsu/crawl._Coordinator, BE-0092).

Extracting the crawl's shared concurrent state into one class exposes the scheduler so it can be
tested in isolation, without driving a (fake) device through the whole `crawl()` walk. These pin the
invariants the parallel engine relies on: reservation bumps the budget, the cheapest-path backtrack,
front-insert give-back, and global-control pruning on publish.
"""

from __future__ import annotations

from bajutsu.crawl import Action, Fingerprint, Node, ScreenMap, _Coordinator


def _coord(
    *, max_screens: int = 50, max_steps: int = 200, prune_global: bool = False
) -> _Coordinator:
    return _Coordinator(
        ScreenMap(),
        max_screens=max_screens,
        max_steps=max_steps,
        prune_global=prune_global,
        on_event=None,
    )


def _node(fp: str, actions: tuple[str, ...] = ()) -> Node:
    return Node(fingerprint=fp, kind="id", ids=(), actions=actions)


def test_publish_registers_node_and_returns_its_frontier() -> None:
    coord = _coord()
    frontier = coord.publish(_node("a"), [Action(kind="tap", target="x")])
    assert "a" in coord.screen_map.nodes
    assert [act.target for act in frontier] == ["x"]


def test_publish_prunes_a_global_control_already_claimed_elsewhere() -> None:
    coord = _coord(prune_global=True)
    coord.path_to["a"] = []
    coord.path_to["b"] = []
    tab = Action(kind="tap", target="tabBar")
    coord.publish(_node("a"), [tab])  # screen a claims the shared tab control first
    frontier_b = coord.publish(_node("b"), [tab])  # b offers the same key -> pruned
    assert frontier_b == []
    assert [p.owner for p in coord.screen_map.pruned] == ["a"]


def test_select_next_work_reserves_an_action_from_the_current_screen() -> None:
    coord = _coord()
    coord.path_to["a"] = []
    coord.pending["a"] = [Action(kind="tap", target="x"), Action(kind="tap", target="y")]
    work = coord.select_next_work("a")
    assert work is not None
    assert work.src_fp == "a"
    assert work.action.target == "x"  # deterministic front-of-frontier order
    assert work.replay_needed is False  # already on the current screen
    assert coord.pending["a"] == [Action(kind="tap", target="y")]  # the popped action is reserved


def test_select_next_work_backtracks_to_the_shortest_path_entry() -> None:
    coord = _coord()
    coord.path_to["near"] = [Action(kind="tap", target="p")]
    coord.path_to["far"] = [Action(kind="tap", target="p"), Action(kind="tap", target="q")]
    coord.pending["near"] = [Action(kind="tap", target="x")]
    coord.pending["far"] = [Action(kind="tap", target="z")]
    work = coord.select_next_work(None)  # not standing on any screen -> backtrack to the cheapest
    assert work is not None
    assert work.src_fp == "near"  # shorter known path wins
    assert work.replay_needed is True


def test_select_next_work_finishes_when_max_screens_reached() -> None:
    coord = _coord(max_screens=1)
    coord.publish(_node("a"), [])
    assert coord.select_next_work(None) is None
    assert coord.screen_map.stop_reason == "max_screens"


def test_give_back_reinserts_the_action_at_the_front() -> None:
    coord = _coord()
    coord.path_to["a"] = []
    coord.pending["a"] = [Action(kind="tap", target="y")]
    returned = Action(kind="tap", target="x")
    coord.give_back("a", returned)
    assert coord.pending["a"][0].target == "x"  # a healthy worker retries it next


def _reserve(coord: _Coordinator, src: str) -> Action:
    # Pop a reservation the way a worker does (select_next_work bumps `active`), so record_edge runs
    # against the same paired-with-a-reservation state it sees in the real crawl flow.
    coord.path_to.setdefault(src, [])
    coord.pending[src] = [Action(kind="tap", target="x")]
    work = coord.select_next_work(src)
    assert work is not None
    return work.action


def test_record_edge_reserves_a_new_destination_for_discovery() -> None:
    coord = _coord()
    action = _reserve(coord, "a")  # active -> 1, as a worker would before acting
    path = [action]
    is_new = coord.record_edge("a", action, Fingerprint("b", "id"), [], path)
    assert is_new is True
    assert coord.path_to["b"] == path  # the replayable path to the new screen is recorded
    assert [e.dst for e in coord.screen_map.edges] == ["b"]
    assert coord._active == 1  # the reservation is held for finish_discovery
    coord.finish_discovery(_node("b"), [])
    assert coord._active == 0  # discovery releases it


def test_record_edge_to_a_known_screen_is_not_new() -> None:
    coord = _coord()
    coord.publish(_node("b"), [])
    action = _reserve(coord, "a")  # active -> 1, paired with the edge that releases it
    is_new = coord.record_edge("a", action, Fingerprint("b", "id"), [], [])
    assert is_new is False
    assert coord._active == 0  # a known screen: the step is done, the reservation released
