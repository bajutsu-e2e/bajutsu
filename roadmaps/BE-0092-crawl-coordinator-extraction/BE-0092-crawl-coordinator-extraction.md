**English** · [日本語](BE-0092-crawl-coordinator-extraction-ja.md)

# BE-0092 — Extract the crawl coordinator into a class

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0092](BE-0092-crawl-coordinator-extraction.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0092") |
| Implementing PR | [#321](https://github.com/bajutsu-e2e/bajutsu/pull/321) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Refactor `crawl()` in [`bajutsu/crawl.py`](../../bajutsu/crawl.py) so that its shared concurrent
state (the screen map, the frontier, the budgets, and the lock that guards them) lives in one
small `_Coordinator` class. `crawl()` itself remains as the device-walk that calls into the class.

The refactor is **behavior-preserving** and internal. The crawl stays a discovery tool (Tier 1, never
a CI gate), and screen identity, transitions, and crashes are decided as they are today. The
existing `test_crawl*` suite is the regression net. Nothing in the public API or the on-disk screen
map changes.

The proposal is the maintainability counterpart to
[BE-0064](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) and
[BE-0077](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md), which added the concurrency
that this proposal reorganizes. It refactors production code without changing behavior or
performance, so it is filed under *Codebase quality & technical debt*. That topic is distinct from
*Contributor workflow*
([BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)), which
covers the tooling contributors use to work on this repo, not the internal structure of `bajutsu/`.
[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) is a
comparable internal-structure refactor.

## Motivation

`crawl()` is the single most complex function in the codebase. In its current form, the function is
one ~370-line body holding **eleven nested closures** (`_bootstrap`, `_observe`, `_emit`, `_claim`,
`_finish`, `_discover`, `_publish`, `_select_next_work`, `_worker`, `_give_back`, `_run`,
`_run_extra`) over **one `threading.Condition` and eight mutable shared variables** captured by
`nonlocal`: `path_to`, `pending`, `claimed`, `discovering`, `steps`, `active`, `stopped`, and
`failure`.

Three problems follow from that shape. None is a bug today, but each raises the cost of every future
change to the crawl engine:

1. **The lock contract is spread across the function.** The correctness of a multi-threaded frontier
   depends on a strict discipline: which mutation happens under `cond`, when `active`/`steps` are
   bumped, and when `cond.notify_all()` / `cond.wait()` fire. The discipline also includes the subtle
   fact that the per-worker `errors` counter is *not* shared and must stay in `_worker`. Today the
   discipline is documented in `# holding the lock` / `# off-lock` comments scattered through ~250
   lines. A reviewer has to reconstruct the invariant from the comments rather than read it in one
   place.
2. **The shared state is implicit.** Eight `nonlocal` variables and a `Condition` are the crawl's de
   facto coordinator object, but they have no name and no boundary. Any closure can touch any of
   them, so the questions "what is the concurrent state, and who may mutate it" are answerable only
   by reading the whole function.
3. **Each step is hard to test or change in isolation.** The frontier logic (`_select_next_work`,
   `_claim`, `_publish`) is welded to the device-walk (`_worker`, `_observe`, `_discover`), so the
   scheduling decisions cannot be unit-tested without driving a fake device through the whole walk.

The codebase already separates this code cleanly along one seam, in the comments if not in the
structure: every closure is marked as either **lock-holding (touches shared state)** or **off-lock
(does device I/O)**. This proposal makes that existing seam the class boundary.

## Detailed design

Move the lock-holding half into a `_Coordinator` class that owns the `Condition` and the eight
shared variables. The off-lock half (`_observe`, `_discover`, `_bootstrap`, the device steps of
`_worker`, and `_replay`) stays as functions that take a driver and call coordinator methods for
every shared-state transition.

```python
class _Coordinator:
    """The crawl's shared concurrent state behind one lock: the screen map, the frontier
    (path_to / pending), the global-control claims, the in-flight discovery set, and the
    step/active/stopped budgets. Every mutation of shared state goes through a method here, so
    the lock discipline lives in one reviewable place."""

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
        self.path_to: dict[str, list[Action]] = {}
        self.pending: dict[str, list[Action]] = {}
        self._claimed: dict[str, str] = {}
        self._discovering: set[str] = set()
        self._steps = 0
        self._active = 0
        self._stopped = False
        self.failure: list[Exception] = []
        ...

    # the while/wait loop that reserves the next frontier entry (bumps steps+active atomically)
    def select_next_work(self, current_fp: str | None) -> _Work | None: ...
    # register node + claim its operations under the lock; returns the screen's frontier
    def publish(self, fp: Fingerprint, node: Node, actions: list[Action]) -> list[Action]: ...
    # record an edge; reserve a newly seen destination; return whether THIS worker discovers it
    def record_edge(self, src_fp, action, dst_fp, dismissed, path) -> bool: ...
    def record_crash(self, path) -> None: ...
    def record_alert(self, path, dismissed) -> None: ...
    def give_back(self, src_fp: str, action: Action) -> None: ...   # pool failure isolation
    def drop_screen(self, src_fp: str) -> None: ...                 # an unresolvable replay path
    def finish_discovery(self, dst_fp: str, node, actions) -> None: ...
    def finish(self, reason: str) -> None: ...
    def emit(self) -> None: ...
    def note_failure(self, exc: Exception) -> None: ...
```

`crawl()` becomes the following sequence. It builds a `_Coordinator` and runs `_bootstrap` once
(single-threaded, as today). It then spawns the extra-worker threads and runs the primary worker.
Each worker is a straight-line device walk whose every shared-state touch is a coordinator call. The
concurrency invariants are then reviewable as one class, and the device-walk reads top to bottom
without `with cond:` blocks interleaved through it.

**Invariants that must be preserved exactly** (the review checklist, and what the existing tests
pin):

- The per-worker `errors` counter stays a `_worker` local — it is *not* shared state and must not
  move into `_Coordinator`.
- `select_next_work` keeps its `while True` + `cond.wait()` loop: continue from the worker's current
  screen if it still has frontier, else backtrack to the cheapest entry (shortest known path, then
  fingerprint), else finish when `active == 0`, else wait.
- `give_back` re-inserts the popped action at the **front** of its frontier and decrements `active`.
- `steps` and `active` are bumped together under the lock when an action is reserved, so two workers
  never pop the same action.
- The single authoritative final `emit()` runs after join, capturing late records.
- The `on_event` / `on_node` callbacks fire exactly when they do today.

**Verification.** The existing deterministic suite (`test_crawl*`, the `fake` driver) exercises the
whole change with no Simulator, so `make check` on Linux validates the change fully. The seam newly
exposes the scheduler, so the refactor *enables* (but does not require) adding direct unit tests of
`select_next_work` / `_claim` against a `_Coordinator` instance. That addition is a follow-up
benefit, not part of the behavior-preserving slice.

The change is a single-surface, cross-cutting edit to one Tier-1 hot file. Per the working agreement,
it lands as **one focused PR** announced up front, not folded into unrelated work.

## Alternatives considered

- **Leave it as is.** The function works and is heavily commented. But the function is the codebase's
  highest concentration of concurrency-correctness reasoning, and every future crawl change pays the
  cost of re-deriving the lock contract from scattered comments. The refactor is cheap insurance on
  the part of the code where a subtle regression is hardest to catch.
- **Split into free functions threading the state explicitly.** Passing `(cond, path_to, pending,
  …)` into module-level functions removes the closures but not the implicitness. The eight-tuple of
  state is still unnamed and unbounded, and the call sites grow noisy. A class names the boundary and
  hides the lock, which is the whole point.
- **A full actor / queue redesign of the scheduler.** Replacing the `Condition`-based frontier with a
  work queue would be a behavior *change*, not a refactor, and would risk the determinism the crawl
  relies on (the cheapest-entry backtrack order, the exact stop reasons). Out of scope: this proposal
  preserves the existing algorithm exactly and relocates its state alone.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [`bajutsu/crawl.py`](../../bajutsu/crawl.py): `crawl()` and its nested closures.
- [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md): added the multi-worker frontier that this item reorganizes.
- [BE-0077 — Parallel web crawl across multiple browsers](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md): added the in-thread worker factories and `recover`.
- [BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md): a comparable behavior-preserving internal-structure refactor.
- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md): the contributor-workflow counterpart, from which this item is distinct.
