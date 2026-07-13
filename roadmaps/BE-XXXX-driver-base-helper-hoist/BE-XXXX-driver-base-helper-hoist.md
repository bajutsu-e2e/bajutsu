**English** · [日本語](BE-XXXX-driver-base-helper-hoist-ja.md)

# BE-XXXX — Hoist duplicated driver helpers into drivers.base and unify small constants

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-driver-base-helper-hoist.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Four small pieces of logic are duplicated, byte-for-byte or near enough, across the codebase:
the single-shot `wait_for` body in all four real drivers, a frame-center computation repeated in
three drivers plus its gesture-anchor variant in two of them, a no-op network source defined
once for the runner and once for the orchestrator, and the `bajutsu.config.yaml` default-path
constant defined once in `config_source.py` and once in `cli/_shared.py`. This item hoists each
duplicate into a single shared definition — `bajutsu/drivers/base.py` for the driver-side
helpers, and a single owning module for each of the other two — so a future change to any of them
lands once instead of drifting.

## Motivation

`wait_for` is identical in all four real drivers — `bajutsu/drivers/idb.py:385`,
`bajutsu/drivers/adb.py:526`, `bajutsu/drivers/xcuitest.py:367`, and
`bajutsu/drivers/playwright.py:683` — each body is exactly
`return len(base.find_all(self.query(), sel)) >= 1`. This method is the single-shot condition
check underneath the shared `base.wait_until` deadline poll (BE-0118), so its correctness is
determinism-sensitive: a `Driver` conformance test (BE-0114) already asserts all four behave
identically, but nothing stops a future edit from touching three of the four copies and leaving
the fourth to silently diverge, which is exactly the kind of per-backend behavior drift the
app-agnostic, backend-agnostic driver design (prime directive 3) is meant to rule out.

The frame-center computation `(x + w / 2, y + h / 2)` from an element's `(x, y, w, h)` frame
repeats in `bajutsu/drivers/idb.py:317-321` (`_center`), `bajutsu/drivers/playwright.py:516-518`
(`_center`), and `bajutsu/drivers/adb.py:338-350` (`_center` / `_center_with_screen`). The
related gesture-anchor variant — the same center plus a finger half-distance of `min(w, h) / 4`
for a two-finger gesture — repeats in `bajutsu/drivers/playwright.py:601-608`
(`_gesture_anchor`) and `bajutsu/drivers/adb.py:492-494` (inline in the multi-touch gesture path,
BE-0232). Both are small, easy to copy-paste-and-slightly-misstate pieces of geometry that every
new backend (Android was the third, a fourth is plausible) currently has to reinvent instead of
reusing; a wrong divisor or a swapped `x`/`y` in one copy would silently mistap or mis-anchor a
gesture only on that one backend, which the driver conformance suite (BE-0114) would catch only
if it happens to exercise that exact path.

Separately, `bajutsu/runner/types.py:66` defines `_no_net` and
`bajutsu/orchestrator/types.py:63` defines `_no_network` — both are the same zero-argument
function returning an empty `list[NetworkExchange]`, used as the default `NetworkSource` when no
network collector is attached. `bajutsu/runner/pipeline.py:39` imports `_no_net` and
`bajutsu/orchestrator/loop.py:39` imports `_no_network`; the runner and the orchestrator each own
a private copy of a value that carries no runner-specific or orchestrator-specific meaning — it
is simply "no network was collected."

Finally, `DEFAULT_CONFIG = "bajutsu.config.yaml"` is defined twice: once in
`bajutsu/config_source.py:29`, where it is used to resolve a config spec's path
(`config_source.py:391`), and independently in `bajutsu/cli/_shared.py:36`, presumably for the
CLI's own default-path fallback. Two independently maintained copies of a filename constant that
is otherwise a magic string mean a rename of the default config filename (or a change to how it's
resolved) has two places to remember, with no test currently pinning them together.

None of these are behavior bugs today — the four `wait_for` bodies, the three frame-center
copies, the two no-op network sources, and the two `DEFAULT_CONFIG` constants all currently agree
— but each is a small island of duplicated truth in code that is either determinism-sensitive
(`wait_for`, the gesture geometry) or trivially collapsible (the no-op network source, the
default-config constant). This is a size XS/S effort per item, all four addressable in one small,
behavior-preserving PR.

## Detailed design

The work breaks down into four independent units, each collapsing one duplicate into one shared
definition with no behavior change:

- **Hoist `wait_for` into `drivers.base`.** Add a `base.default_wait_for(driver: Driver, sel:
  Selector) -> bool` helper whose body is the current single-shot check
  (`len(base.find_all(driver.query(), sel)) >= 1`), and have `idb.py`, `adb.py`, `xcuitest.py`,
  and `playwright.py` each delegate their `wait_for` to it (`return base.default_wait_for(self,
  sel)`). Each driver keeps its own `wait_for` method — required by the `Driver` protocol — so a
  future backend that can wait natively (rather than through the shared single-shot-plus-poll
  contract) still overrides it; only the default body moves, not the protocol shape.
- **Hoist frame-center and gesture-anchor math into `drivers.base`.** Add
  `base.frame_center(frame: Frame) -> Point` (the `(x + w / 2, y + h / 2)` computation) and
  `base.gesture_anchor(frame: Frame) -> tuple[float, float, float]` (the center plus
  `min(w, h) / 4`), each taking the already-resolved `(x, y, w, h)` frame tuple so they stay pure
  geometry with no driver-specific resolution logic attached. Route `idb.py`'s `_center`,
  `playwright.py`'s `_center` and `_gesture_anchor`, and `adb.py`'s `_center_with_screen` and its
  inline gesture-anchor computation through these two helpers, keeping each driver's own
  element-resolution step (`_resolve`, `resolve_unique`, `_resolve_frame_and_screen`) unchanged
  and only replacing the arithmetic on the resolved frame.
- **Consolidate the no-op network source.** Keep `bajutsu/orchestrator/types.py`'s `_no_network`
  as the single definition. `runner` already depends on `orchestrator` (`runner/types.py`,
  `runner/pipeline.py`, `runner/pool.py`, and `runner/mailbox.py` all import from it), while
  nothing in `orchestrator` imports from `runner` — keeping the definition on the `orchestrator`
  side preserves that one-way dependency instead of reversing it. Have `bajutsu/runner/pipeline.py`
  import the single definition instead of `runner/types.py`'s own copy, and delete the duplicate.
- **Consolidate `DEFAULT_CONFIG`.** Keep `bajutsu/config_source.py:29`'s definition as the single
  source (it's the module that already resolves a config spec's path against it) and have
  `bajutsu/cli/_shared.py` re-export it (`from bajutsu.config_source import DEFAULT_CONFIG`)
  instead of redefining the same string literal.

Each of the four units is independently shippable and independently testable — none depends on
another — so they can land together in one PR or be picked up separately without reordering
constraints.

## Alternatives considered

- **Leave `wait_for` fully per-driver** so each backend's method body is free to diverge without
  touching a shared helper. Addressed by the design above rather than rejected outright: hoisting
  the current identical body into `base.default_wait_for` doesn't remove per-driver overriding —
  a backend that gains a native wait primitive still defines its own `wait_for` and simply doesn't
  call the default. The shared helper only replaces what today is four copies of the *same*
  behavior, not the protocol's per-driver seam.
- **Do nothing and rely on the driver conformance suite (BE-0114) to catch divergence.** Rejected:
  the conformance suite verifies behavior after the fact, once a divergence has already been
  introduced and (best case) caught in review or CI; a single shared definition removes the
  divergence as a possibility rather than detecting it after it happens, which is the cheaper and
  more durable fix for determinism-sensitive code.
- **Generalize `frame_center` / `gesture_anchor` to accept a `Selector` and do their own
  resolution** (mirroring each driver's current `_center` signature) rather than taking an
  already-resolved `Frame`. Rejected: each driver resolves a selector to a frame differently
  (`idb.py` settles and resolves against a snapshot tree, `adb.py` additionally returns the screen
  extent for touch-device scaling, `playwright.py` resolves directly against a fresh query) — a
  shared function that also resolves would either have to accept a driver-specific resolution
  callback (more indirection than the geometry itself is worth) or lose those per-driver
  differences. Taking a plain `Frame` keeps the hoisted helper pure geometry and leaves resolution
  exactly where it already lives.
- **Leave the no-op network source and `DEFAULT_CONFIG` duplicated, since each is a one-line
  definition.** Rejected: a one-line duplicate is still a duplicate value with no independent
  reason to differ, and both this item's other two units are already touching import boundaries
  between modules — folding these two low-risk, low-effort consolidations into the same PR is
  cheaper than opening a separate item for a one-line fix.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `base.default_wait_for` and have `idb.py`, `adb.py`, `xcuitest.py`, and `playwright.py`
      delegate their `wait_for` to it.
- [ ] Add `base.frame_center` and `base.gesture_anchor` and route the five call sites
      (`idb.py`, `adb.py`, `playwright.py`) through them.
- [ ] Consolidate `_no_net` / `_no_network` into one definition imported by both
      `runner/pipeline.py` and `orchestrator/loop.py`.
- [ ] Have `cli/_shared.py` re-export `config_source.DEFAULT_CONFIG` instead of redefining it.

## References

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the shared selector-resolution
  core (`find_all`, `resolve_unique`, `wait_until`) this item extends with the hoisted helpers.
- [`bajutsu/drivers/idb.py:385`](../../bajutsu/drivers/idb.py),
  [`bajutsu/drivers/adb.py:526`](../../bajutsu/drivers/adb.py),
  [`bajutsu/drivers/xcuitest.py:367`](../../bajutsu/drivers/xcuitest.py),
  [`bajutsu/drivers/playwright.py:683`](../../bajutsu/drivers/playwright.py) — the four identical
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
