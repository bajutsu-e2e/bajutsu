"""The crawl's shared concurrent state, behind one lock."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ._work import _Work
from .alert import Alert
from .crash import Crash
from .edge import Edge
from .fingerprint import Fingerprint
from .node import Node
from .pruned import Pruned

if TYPE_CHECKING:
    from ._shared import OnEvent
    from .action import Action
    from .screen_map import ScreenMap


class _Coordinator:
    """The crawl's shared concurrent state behind one lock.

    Owns the screen map, the frontier (`path_to` shortest known paths + `pending` untried actions),
    the global-control `claimed` table, the in-flight `discovering` set, and the step/active/stopped
    budgets. Every mutation of that state goes through a method here, so the whole lock discipline
    lives in one reviewable place and the device-walk (`crawl`'s `_worker`) reads top to bottom with
    no `with cond:` blocks interleaved through it. Workers call these methods off their own driver
    threads; `path_to` / `pending` / `failure` / `screen_map` are read single-threaded by bootstrap
    and after join (BE-0092).
    """

    def __init__(
        self,
        screen_map: ScreenMap,
        *,
        max_screens: int,
        max_steps: int,
        prune_global: bool,
        on_event: OnEvent | None,
    ) -> None:
        self._cond = threading.Condition()
        self._sm = screen_map
        self._max_screens = max_screens
        self._max_steps = max_steps
        self._prune_global = prune_global
        self._on_event = on_event
        # A known replayable path to each discovered screen (set once at discovery, never mutated),
        # and the still-untried actions per screen. The strategy is a *forward walk*: a worker keeps
        # acting on the screen its driver is on until it has no untried action left, resetting +
        # replaying only to reach another screen. Read single-threaded by bootstrap / after join.
        self.path_to: dict[str, list[Action]] = {}
        self.pending: dict[str, list[Action]] = {}
        # When pruning global controls, the first screen to offer an operation (by replay key) claims
        # and explores it; later screens offering the same key skip it (a tab bar / nav button reused
        # across screens collides and is pruned to one exploration).
        self._claimed: dict[str, str] = {}
        # Fingerprints a worker is currently discovering (guide in flight), so two workers don't
        # double-discover the same new screen.
        self._discovering: set[str] = set()
        self._steps = 0  # shared action budget counter
        self._active = 0  # workers holding a popped action (mid step) — done at 0 with no frontier
        self._stopped = False  # a budget was hit; no worker takes more work
        self.failure: list[
            Exception
        ] = []  # the first unexpected worker error, re-raised after join

    @property
    def screen_map(self) -> ScreenMap:
        return self._sm

    def _emit(self) -> None:  # holding the lock (it reads `pending`)
        # Refresh the plan (the live frontier: still-untried operations per screen) before each
        # notification, so a watcher sees what the crawl will try next as it advances.
        self._sm.plan = {
            fp: [a.describe() for a in acts] for fp, acts in self.pending.items() if acts
        }
        if self._on_event is not None:
            self._on_event(self._sm)

    def emit(self) -> None:
        """The authoritative final/bootstrap notification (acquires the lock)."""
        with self._cond:
            self._emit()

    def _finish(self, reason: str) -> None:  # holding the lock
        # Signal the stop; the single authoritative final `emit()` runs after join (so it captures
        # any late records a worker added between this signal and its own exit).
        if not self._sm.stop_reason:
            self._sm.stop_reason = reason
        self._stopped = True
        self._cond.notify_all()

    def _claim(self, fp_value: str, actions: list[Action]) -> list[Action]:
        # Holding the lock. Without pruning, every action is the screen's own to explore. With it, an
        # op already claimed by another screen is recorded as Pruned (with a replay path) instead.
        if not self._prune_global:
            return list(actions)
        kept: list[Action] = []
        for a in actions:
            owner = self._claimed.get(a.key)
            if owner is not None and owner != fp_value:
                path = (*self.path_to.get(fp_value, []), a)  # replay to src, then the pruned op
                self._sm.pruned.append(Pruned(fp_value, a.describe(), a.key, owner, path))
            else:
                self._claimed.setdefault(a.key, fp_value)
                kept.append(a)
        return kept

    def _publish(self, node: Node, actions: list[Action]) -> list[Action]:
        # Holding the lock: register the node (keyed by its fingerprint) and claim its operations.
        # Returns the screen's frontier (its actions not already claimed elsewhere).
        self._sm.nodes[node.fingerprint] = node
        # The path to reach this screen is already known (set before publish: [] for the entry, the
        # discovering edge's path otherwise) — persist it so a discovered screen carries a
        # committable candidate flow (BE-0038).
        self._sm.paths[node.fingerprint] = tuple(self.path_to.get(node.fingerprint, ()))
        return self._claim(node.fingerprint, actions)

    def publish(self, node: Node, actions: list[Action]) -> list[Action]:
        """Register a node and claim its operations; return its frontier (used by bootstrap)."""
        with self._cond:
            return self._publish(node, actions)

    def select_next_work(self, current_fp: str | None) -> _Work | None:
        # Pick (and reserve) the next frontier entry to explore, or return None when the worker
        # should retire — a stop was signalled, a budget is spent, or the frontier is fully drained
        # with no worker in flight. Continue from the screen the worker is on; else backtrack to the
        # cheapest entry (shortest known path, then fingerprint) and replay to it. Reserving bumps
        # steps/active under the lock, so two workers never pop the same action.
        with self._cond:
            while True:
                if self._stopped:
                    return None
                if len(self._sm.nodes) >= self._max_screens:
                    self._finish("max_screens")
                    return None
                if self._steps >= self._max_steps:
                    self._finish("max_steps")
                    return None
                if current_fp is not None and self.pending.get(current_fp):
                    src_fp, replay_needed = current_fp, False
                elif candidates := [fp for fp, acts in self.pending.items() if acts]:
                    src_fp = min(candidates, key=lambda fp: (len(self.path_to[fp]), fp))
                    replay_needed = True
                elif self._active == 0:
                    self._finish("completed")  # no frontier and no worker in flight → all explored
                    return None
                else:
                    self._cond.wait()  # another worker is mid-step; it may add frontier
                    continue
                action = self.pending[src_fp].pop(0)  # deterministic order
                src_path = list(self.path_to[src_fp])
                self._steps += 1
                self._active += 1
                return _Work(src_fp, action, src_path, replay_needed)

    def record_alert(self, path: list[Action], dismissed: list[str]) -> None:
        """Record an OS prompt the guard dismissed mid-step (no budget change)."""
        with self._cond:
            self._sm.alerts.append(Alert(tuple(a.describe() for a in path), tuple(dismissed)))

    def record_crash(
        self, path: list[Action], artifacts: tuple[tuple[str, bytes], ...] = ()
    ) -> None:
        """Record a crash (with its replayable action path), release the reservation, notify.

        `artifacts` arrives already resolved, swept off-lock by the caller (BE-0424). This body holds
        `self._cond` throughout, and that same lock serializes `on_event` and every other worker's
        `record_crash` / `record_edge` — so a multi-second `.ips` poll run in here would stall every
        other crawl lane for its duration. Handed in resolved, it costs the lock a list append.
        """
        with self._cond:
            self._sm.crashes.append(
                Crash(tuple(a.describe() for a in path), tuple(path), artifacts)
            )
            self._active -= 1
            self._emit()
            self._cond.notify_all()

    def record_edge(
        self,
        src_fp: str,
        action: Action,
        dst_fp: Fingerprint,
        dismissed: list[str],
        path: list[Action],
    ) -> bool:
        """Record a transition; reserve a newly seen destination for THIS worker to discover.

        Returns True when the destination is new (this worker holds the reservation and must call
        `finish_discovery`); False for a known/in-flight screen (the step is done, reservation
        released).
        """
        with self._cond:
            self._sm.edges.append(Edge(src_fp, action.describe(), dst_fp.value, tuple(dismissed)))
            if dst_fp.value not in self._sm.nodes and dst_fp.value not in self._discovering:
                self._discovering.add(dst_fp.value)  # reserve so two workers don't double-discover
                self.path_to[dst_fp.value] = path
                return True
            self._active -= 1  # a known/in-flight screen: just the edge, this step is done
            self._emit()
            self._cond.notify_all()
            return False

    def finish_discovery(self, node: Node, actions: list[Action]) -> None:
        """Publish a freshly discovered screen's node + frontier, release the reservation, notify."""
        with self._cond:
            self.pending[node.fingerprint] = self._publish(node, actions)
            self._discovering.discard(node.fingerprint)
            self._active -= 1
            self._emit()
            self._cond.notify_all()

    def give_back(self, src_fp: str, action: Action) -> None:
        # Pool failure isolation: a device misbehaved, so hand the popped action back to the front of
        # its frontier (a healthy worker retries it) and release the reservation.
        with self._cond:
            self.pending[src_fp].insert(0, action)
            self._active -= 1
            self._cond.notify_all()

    def drop_screen(self, src_fp: str) -> None:
        # A replay path no longer resolves (lone worker): drop this screen's frontier and release.
        with self._cond:
            self.pending[src_fp] = []
            self._active -= 1
            self._cond.notify_all()

    def cancel_action(self) -> None:
        # A selector no longer resolves: drop this action and release the reservation.
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    def note_failure(self, exc: Exception) -> None:
        """Record an unexpected worker error (surfaced after join) and stop the crawl."""
        with self._cond:
            self.failure.append(exc)
            self._finish(self._sm.stop_reason or "completed")
