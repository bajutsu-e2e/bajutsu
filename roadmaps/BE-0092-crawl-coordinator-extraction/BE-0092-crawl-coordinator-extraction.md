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

Refactor `crawl()` in [`bajutsu/crawl.py`](../../bajutsu/crawl.py) so its shared concurrent
state — the screen map, the frontier, the budgets, and the lock that guards them — lives in one
small `_Coordinator` class, leaving `crawl()` itself as the device-walk that calls into it. This is a
**behavior-preserving** internal refactor: the crawl stays a discovery tool (Tier 1, never a CI
gate), screen identity / transitions / crashes are decided exactly as today, and the existing
`test_crawl*` suite is the regression net. Nothing in the public API or the on-disk screen map
changes. It is the maintainability counterpart to [BE-0064](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md)
and [BE-0077](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md), which added
the concurrency this proposal reorganizes. Because it is a behavior-preserving internal refactor of
production code rather than a behavior or performance change, it is filed under *Codebase quality &
technical debt* — distinct from *Contributor workflow*
([BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)),
which covers the tooling contributors use to work on this repo, not `bajutsu/`'s own internal
structure.
[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)
is a comparable internal-structure refactor.

## Motivation

`crawl()` is the single most complex function in the codebase. In its current form it is one
~370-line function holding **eleven nested closures** (`_bootstrap`, `_observe`, `_emit`, `_claim`,
`_finish`, `_discover`, `_publish`, `_select_next_work`, `_worker`, `_give_back`, `_run`,
`_run_extra`) over **one `threading.Condition` and eight mutable shared variables** captured by
`nonlocal`: `path_to`, `pending`, `claimed`, `discovering`, `steps`, `active`, `stopped`, and
`failure`.

Three problems follow from that shape, none of which is a bug today but all of which raise the cost
of every future change to the crawl engine:

1. **The lock contract is spread across the function.** The correctness of a multi-threaded frontier
   depends on a strict discipline: which mutation happens under `cond`, when `active`/`steps` are
   bumped, when `cond.notify_all()` / `cond.wait()` fire, and the subtle fact that the per-worker
   `errors` counter is *not* shared and must stay in `_worker`. Today that discipline is documented
   in `# holding the lock` / `# off-lock` comments scattered through ~250 lines. A reviewer has to
   reconstruct the invariant from the comments rather than read it in one place.
2. **The shared state is implicit.** Eight `nonlocal` variables and a `Condition` are the crawl's de
   facto coordinator object, but they have no name and no boundary — any closure can touch any of
   them, so "what is the concurrent state, and who may mutate it" is answerable only by reading the
   whole function.
3. **Each step is hard to test or change in isolation.** Because the frontier logic
   (`_select_next_work`, `_claim`, `_publish`) is welded to the device-walk (`_worker`, `_observe`,
   `_discover`), the scheduling decisions can't be unit-tested without driving a fake device through
   the whole walk.

The codebase already separates this code cleanly along one seam, in the comments if not in the
structure: every closure is marked as either **lock-holding (touches shared state)** or **off-lock
(does device I/O)**. That existing seam is exactly the class boundary this proposal makes explicit.

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

`crawl()` becomes: build a `_Coordinator`, run `_bootstrap` once (single-threaded, as today), then
spawn the extra-worker threads and run the primary worker — each worker a straight-line device walk
whose every shared-state touch is a coordinator call. The result is that the concurrency invariants
are reviewable as one class, and the device-walk reads top to bottom without `with cond:` blocks
interleaved through it.

**Invariants that must be preserved exactly** (these are the review checklist, and what the existing
tests pin):

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

**Verification.** The whole change is exercised by the existing deterministic suite (`test_crawl*`,
the `fake` driver) with no Simulator, so it is fully validated by `make check` on Linux. Because the
seam newly exposes the scheduler, the refactor *enables* (but does not require) adding direct unit
tests of `select_next_work` / `_claim` against a `_Coordinator` instance — a follow-up benefit, not
part of the behavior-preserving slice.

This is a single-surface, cross-cutting change to one Tier-1 hot file, so per the working agreement
it lands as **one focused PR** announced up front, not folded into unrelated work.

## Alternatives considered

- **Leave it as is.** The function works and is heavily commented. But it is the codebase's highest
  concentration of concurrency-correctness reasoning, and every future crawl change pays the cost of
  re-deriving the lock contract from scattered comments. The refactor is cheap insurance on the part
  of the code where a subtle regression is hardest to catch.
- **Split into free functions threading the state explicitly.** Passing `(cond, path_to, pending,
  …)` into module-level functions removes the closures but not the implicitness — the eight-tuple of
  state is still unnamed and unbounded, and the call sites grow noisy. A class names the boundary and
  hides the lock, which is the whole point.
- **A full actor / queue redesign of the scheduler.** Replacing the `Condition`-based frontier with a
  work queue would be a behavior *change*, not a refactor, and would risk the determinism the crawl
  relies on (the cheapest-entry backtrack order, the exact stop reasons). Out of scope: this proposal
  preserves the existing algorithm exactly and only relocates its state.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [`bajutsu/crawl.py`](../../bajutsu/crawl.py) — `crawl()` and its nested closures.
- [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) — added the multi-worker frontier this reorganizes.
- [BE-0077 — Parallel web crawl across multiple browsers](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md) — added the in-thread worker factories and `recover`.
- [BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) — a comparable behavior-preserving internal-structure refactor.
- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) — the contributor-workflow counterpart this item is distinct from.
