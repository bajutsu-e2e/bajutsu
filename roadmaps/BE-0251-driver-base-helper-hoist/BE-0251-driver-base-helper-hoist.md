**English** · [日本語](BE-0251-driver-base-helper-hoist-ja.md)

# BE-0251 — Hoist duplicated driver helpers into drivers.base and unify small constants

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0251](BE-0251-driver-base-helper-hoist.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0251") |
| Implementing PR | [#1069](https://github.com/bajutsu-e2e/bajutsu/pull/1069) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Four small pieces of logic are duplicated across the codebase, byte for byte or nearly so:

- the single-shot `wait_for` body across the real drivers
- a frame-center computation repeated in three drivers, plus its gesture-anchor variant in two of them
- a no-op network source defined once for the runner and once for the orchestrator
- the `bajutsu.config.yaml` default-path constant, defined once in `config_source.py` and once in `cli/_shared.py`

This item hoists each duplicate into a single shared definition. The driver-side helpers go into
`bajutsu/drivers/base.py`. The other two each get a single owning module. A future change to any of
them then lands once instead of drifting.

## Motivation

`wait_for` is identical across the real drivers: `coordinate_tree.py`, `bajutsu/drivers/xcuitest.py`,
and `bajutsu/drivers/playwright.py`. The first is the shared base (BE-0254) that idb and adb
inherit. Each body is exactly `return len(base.find_all(self.query(), sel)) >= 1`. This method is the
single-shot condition check underneath the shared `base.wait_until` deadline poll (BE-0118), so its
correctness is determinism-sensitive.

A `Driver` conformance test (BE-0114) already asserts that every backend behaves identically. Nothing
stops a future edit from touching some copies and leaving another to diverge silently. Per-backend
drift of that kind is exactly what the app-agnostic, backend-agnostic driver design (prime directive
3) is meant to rule out.

The frame-center computation `(x + w / 2, y + h / 2)` from an element's `(x, y, w, h)` frame repeats
in three places:

- `bajutsu/drivers/idb.py:317-321` (`_center`)
- `bajutsu/drivers/playwright.py:516-518` (`_center`)
- `bajutsu/drivers/adb.py:338-350` (`_center` / `_center_with_screen`)

The gesture-anchor variant is the same center plus a finger half-distance of `min(w, h) / 4` for a
two-finger gesture. It repeats in two places:

- `bajutsu/drivers/playwright.py:601-608` (`_gesture_anchor`)
- `bajutsu/drivers/adb.py:492-494` (inline in the multi-touch gesture path, BE-0232)

Both are small pieces of geometry, easy to copy and slightly misstate. Every new backend currently
reinvents them instead of reusing them. Android was the third backend, and a fourth is plausible. A
wrong divisor or a swapped `x` / `y` in one copy would silently mistap or mis-anchor a gesture on
that one backend. The driver conformance suite (BE-0114) would catch the error only if it happens to
exercise that exact path.

Separately, `bajutsu/runner/types.py:66` defines `_no_net`, and `bajutsu/orchestrator/types.py:63`
defines `_no_network`. Both are the same zero-argument function returning an empty
`list[NetworkExchange]`. Each serves as the default `NetworkSource` when no network collector is
attached. `bajutsu/runner/pipeline.py:39` imports `_no_net`, and `bajutsu/orchestrator/loop.py:39`
imports `_no_network`. The runner and the orchestrator each own a private copy of a value with no
runner-specific or orchestrator-specific meaning. The value simply means "no network was collected."

Finally, `DEFAULT_CONFIG = "bajutsu.config.yaml"` is defined twice. One definition sits in
`bajutsu/config_source.py:29`, where it resolves a config spec's path (`config_source.py:391`). The
other sits independently in `bajutsu/cli/_shared.py:36`, presumably for the CLI's own default-path
fallback. A filename constant that is otherwise a magic string now has two independently maintained
copies. Renaming the default config file, or changing how it is resolved, therefore means
remembering two places. No test currently pins the two copies together.

None of these duplicates is a behavior bug today. The `wait_for` bodies, the three frame-center
copies, the two no-op network sources, and the two `DEFAULT_CONFIG` constants all agree. Each is
still a small island of duplicated truth. Two of them sit in determinism-sensitive code (`wait_for`
and the gesture geometry). The other two collapse trivially (the no-op network source and the
default-config constant). The effort is size XS/S per item, and all four fit in one small,
behavior-preserving PR.

## Detailed design

The work breaks down into four independent units. Each collapses one duplicate into one shared
definition with no behavior change.

- **Hoist `wait_for` into `drivers.base`.** Add a `base.default_wait_for(driver: Driver, sel:
  Selector) -> bool` helper. Its body is the current single-shot check
  (`len(base.find_all(driver.query(), sel)) >= 1`). Each backend delegates its `wait_for` to the
  helper (`return base.default_wait_for(self, sel)`).

  BE-0254 landed the shared `CoordinateTreeDriver` base. Idb and adb therefore inherit one
  `wait_for` there, and the delegating sites are `coordinate_tree.py` (covering both coordinate
  backends), `xcuitest.py`, and `playwright.py`.

  Each keeps a `wait_for` method, because the `Driver` protocol requires it. A future backend that
  can wait natively, rather than through the shared single-shot-plus-poll contract, still overrides
  the method. Only the default body moves. The protocol shape stays.
- **Hoist frame-center and gesture-anchor math into `drivers.base`.** Add
  `base.frame_center(frame: Frame) -> Point`, the `(x + w / 2, y + h / 2)` computation. Add
  `base.gesture_anchor(frame: Frame) -> tuple[float, float, float]`, the center plus `min(w, h) / 4`.
  Each takes the already-resolved `(x, y, w, h)` frame tuple, so both stay pure geometry with no
  driver-specific resolution logic attached.

  The call sites that route through these two helpers are `_center` in `idb.py`, `_center` and
  `_gesture_anchor` in `playwright.py`, and `_center_with_screen` plus the inline gesture-anchor
  computation in `adb.py`. Each driver keeps its own element-resolution step (`_resolve`,
  `resolve_unique`, `_resolve_frame_and_screen`) unchanged. Only the arithmetic on the resolved
  frame is replaced.
- **Consolidate the no-op network source.** Keep `_no_network` in `bajutsu/orchestrator/types.py`
  as the single definition. The runner already depends on the orchestrator: `runner/types.py`,
  `runner/pipeline.py`, `runner/pool.py`, and `runner/mailbox.py` all import from it. Nothing in
  the orchestrator imports from the runner. Keeping the definition on the orchestrator side
  preserves that one-way dependency instead of reversing it. `bajutsu/runner/pipeline.py` imports
  the single definition instead of the copy in `runner/types.py`, and the duplicate is deleted.
- **Consolidate `DEFAULT_CONFIG`.** Keep the definition at `bajutsu/config_source.py:29` as the
  single source, because that module already resolves a config spec's path against it.
  `bajutsu/cli/_shared.py` re-exports it (`from bajutsu.config_source import DEFAULT_CONFIG`)
  instead of redefining the same string literal.

Each of the four units is independently shippable and independently testable, and none depends on
another. They can land together in one PR or be picked up separately, with no ordering constraint.

## Alternatives considered

- **Leave `wait_for` fully per-driver**, so each backend's method body is free to diverge without
  touching a shared helper. The design above addresses this option instead of rejecting it
  outright. Hoisting the current identical body into `base.default_wait_for` does not remove
  per-driver overriding. A backend that gains a native wait primitive still defines its own
  `wait_for` and does not call the default. The shared helper replaces four copies of the *same*
  behavior today. It leaves the protocol's per-driver seam alone.
- **Do nothing and rely on the driver conformance suite (BE-0114) to catch divergence.** Rejected.
  The conformance suite verifies behavior after the fact, once a divergence has been introduced and,
  in the best case, caught in review or CI. A single shared definition removes the possibility of
  divergence instead of detecting it afterward. For determinism-sensitive code, that fix is cheaper
  and more durable.
- **Generalize `frame_center` and `gesture_anchor` to accept a `Selector` and do their own
  resolution**, mirroring each driver's current `_center` signature, instead of taking an
  already-resolved `Frame`. Rejected. Each driver resolves a selector to a frame differently:
  - `idb.py` settles and resolves against a snapshot tree.
  - `adb.py` also returns the screen extent for touch-device scaling.
  - `playwright.py` resolves directly against a fresh query.

  A shared function that also resolves would have to accept a driver-specific resolution callback,
  which is more indirection than the geometry is worth. Otherwise it would lose those per-driver
  differences. Taking a plain `Frame` keeps the hoisted helper pure geometry and leaves resolution
  where it already lives.
- **Leave the no-op network source and `DEFAULT_CONFIG` duplicated, since each is a one-line
  definition.** Rejected. A one-line duplicate is still a duplicate value with no independent reason
  to differ. The other two units of this item already touch import boundaries between modules.
  Folding these two low-risk, low-effort consolidations into the same PR costs less than opening a
  separate item for a one-line fix.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `base.default_wait_for` and have every backend delegate its `wait_for` to it —
      `coordinate_tree.py` (the BE-0254 shared base idb and adb inherit), `xcuitest.py`, and
      `playwright.py`.
- [x] Add `base.frame_center` and `base.gesture_anchor` and route the call sites through them —
      `idb.py`, `adb.py`, `playwright.py`, and also `orchestrator/actions/handlers/gestures.py`,
      which held a fifth copy of the frame-center math the proposal's inventory missed.
- [x] Consolidate `_no_net` / `_no_network` into one definition imported by both
      `runner/pipeline.py` and `orchestrator/loop.py`.
- [x] Have `cli/_shared.py` re-export `config_source.DEFAULT_CONFIG` instead of redefining it.

Log:

- [#1069](https://github.com/bajutsu-e2e/bajutsu/pull/1069) — all four units landed in one
  behavior-preserving PR: `base.default_wait_for` / `base.frame_center` / `base.gesture_anchor`
  added and the backends plus `gestures.py` routed through them (rebased onto BE-0254's
  `CoordinateTreeDriver`, so idb and adb delegate `wait_for` via that shared base rather than
  each carrying its own); `_no_network` and `DEFAULT_CONFIG` consolidated to single owners.

## References

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the shared selector-resolution
  core (`find_all`, `resolve_unique`, `wait_until`) this item extends with the hoisted helpers.
- [`bajutsu/drivers/coordinate_tree.py`](../../bajutsu/drivers/coordinate_tree.py) (the BE-0254
  shared base idb and adb inherit),
  [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py),
  [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — the identical
  `wait_for` bodies this item hoists.
- [`bajutsu/drivers/idb.py:317-321`](../../bajutsu/drivers/idb.py),
  [`bajutsu/drivers/playwright.py:516-518`](../../bajutsu/drivers/playwright.py),
  [`bajutsu/drivers/adb.py:338-350`](../../bajutsu/drivers/adb.py) — the frame-center
  computations this item hoists.
- [`bajutsu/drivers/playwright.py:601-608`](../../bajutsu/drivers/playwright.py),
  [`bajutsu/drivers/adb.py:492-494`](../../bajutsu/drivers/adb.py) — the gesture-anchor variant
  this item hoists.
- [`bajutsu/runner/types.py:66`](../../bajutsu/runner/types.py),
  [`bajutsu/orchestrator/types.py:63`](../../bajutsu/orchestrator/types.py) — the duplicated
  no-op `NetworkSource` this item consolidates.
- [`bajutsu/config_source.py:29`](../../bajutsu/config_source.py),
  [`bajutsu/cli/_shared.py:36`](../../bajutsu/cli/_shared.py) — the duplicated `DEFAULT_CONFIG`
  constant this item consolidates.
- [BE-0118 — Unify the wait_for polling contract across drivers](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md)
  — the single-shot `wait_for` contract this item's `wait_for` hoist preserves unchanged.
- [BE-0114 — Driver conformance suite for backend-agnostic behavior](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)
  — the test suite that already pins the four drivers' behavior to be identical, which a single
  shared implementation now guarantees by construction rather than by test alone.
- [BE-0232 — Multi-touch gestures on the adb driver (pinch / rotate)](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures.md)
  — introduced the `adb.py` gesture-anchor computation this item hoists alongside `playwright.py`'s.
