**English** · [日本語](BE-0240-ios-capability-aware-actuator-selection-ja.md)

# BE-0240 — Capability-aware automatic actuator selection for iOS (idb/XCUITest transparency)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0240](BE-0240-ios-capability-aware-actuator-selection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0240") |
| Implementing PR | [#981](https://github.com/bajutsu-e2e/bajutsu/pull/981) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md) |
<!-- /BE-METADATA -->

## Introduction

iOS has two actuators: idb and XCUITest. `bajutsu/backends.py` already declares them as an ordered
ladder (`PLATFORMS["ios"] = ("xcuitest", "idb")`, BE-0019). In principle, a scenario author writes
`platform: ios` (or `backend: [ios]`) and never names an actuator.

In practice, every real config pins `backend: [idb]`. A whole second scenario directory exists
solely to route the handful of scenarios that need XCUITest's richer capabilities. That directory is
`demos/showcase/ios/scenarios-xcuitest/`.

This proposal makes the *scenario itself* decide which iOS actuator it needs. The decision comes from
the capability requirements that the scenario's own steps already imply. It closes the gap between
what the author writes (a platform) and what actually ran the scenario (an actuator). Today that gap
leaks into scenario file layout and CLI flags, instead of staying an implementation detail.

## Motivation

### The gap between the designed ladder and how it is actually used

BE-0019 built `select_actuator` to expand a platform token to its actuators in stability order and
pick the first available one. `--backend ios` was therefore meant to prefer XCUITest, and to fall
back to idb only when XCUITest's toolchain (`xcodebuild`) is unavailable. But
`demos/showcase/showcase.config.yaml` does not use that default resolution for any real target:

```yaml
defaults:
  backend: [idb]
  ...
  showcase-swiftui-noax:
    ...
    backend: [idb]   # idb + vision (BE-0176): crawl drives this -noax app with no XCUITest runner
    scenarios: demos/showcase/ios/scenarios-xcuitest
    # XCUITest backend (BE-0019) stays wired for explicit `--backend xcuitest`: the generic runner
    # reaches elements idb's tree can't (e.g. a TabView's tabs) by label, where idb needs vision.
```

Every target pins `backend: [idb]` explicitly. The two `-noax` targets also carry an entirely
separate `scenarios` directory. Its sole reason to exist is a limit of idb's `describe-all`. It
collapses a `UITabBarController` / `TabView` into one opaque "Tab Bar" group with no per-tab
children (documented in BE-0107 and BE-0019's on-device investigation note). Crossing a tab therefore needs
`--backend xcuitest` and a scenario written against XCUITest's addressing.

Today, the scenario author or the config author must do three things:

1. Know in advance which of the two backends a given scenario needs.
2. Split the scenario into the matching directory.
3. Invoke it with the matching flag.

The principle "a platform is a backend behind one interface" ([DESIGN §1](../../DESIGN.md)) is meant
to keep this backend-awareness out of the author's hands. The awareness has merely moved from a per-run CLI flag into a per-directory, per-target config convention, which
is no more transparent.

### idb's capability set is a strict subset of XCUitest's — cost, not capability, is why it's still preferred

Reading the two drivers' declared `CAPABILITIES` side by side settles a question this proposal would
otherwise have to hedge on:

```python
# bajutsu/drivers/idb.py
CAPABILITIES = frozenset({QUERY, ELEMENTS, SCREENSHOT}) | DEVICE_CONTROL_ALL

# bajutsu/drivers/xcuitest.py
CAPABILITIES = frozenset({QUERY, ELEMENTS, SCREENSHOT, SEMANTIC_TAP, CONDITION_WAIT, MULTI_TOUCH}) | DEVICE_CONTROL_ALL
```

XCUITest's set is a strict superset of idb's. XCUITest backs every device-control operation that idb
backs: `setLocation` / `clipboard` / `push` / `clearKeychain` / `appLifecycle` / `statusBar`. XCUITest
shares the same `simctl`-driven Simulator lifecycle (BE-0128 / BE-0212), so no construct exists that
idb supports and XCUITest does not. No scenario *needs* idb specifically.

What idb has going for it is cost, not capability:

- It needs only the `idb` / `idb_companion` client, with no Xcode toolchain.
- It needs no built test runner (`xcuitest.testRunner` / `xcuitest.build`, an extra per-target setup
  step).
- It runs no resident process per leased device.
- It carries years of hardening, such as the weekly `idb-monitor.yml` compatibility watch.

BE-0019's richness-first ladder prefers XCUITest and falls back to idb only on unavailability. It
optimizes for capability and ignores this cost difference. That is why every real config overrides
the ladder back to a hard idb pin. The right default has the opposite priority. It prefers the cheap
actuator. It promotes to the richer one **only** for the scenarios whose own steps need it.

### The preflight mechanism already computes exactly what's needed — today only to reject, never to choose

`bajutsu/capability_preflight.py` (BE-0082) already walks a scenario's whole step tree, recursing
into `if` and `forEach`. As a pure function of `(scenario, capability set)`, it derives every
construct that needs a capability the set lacks:

- `pinch` and `rotate` need `multiTouch`.
- A `visual` assertion needs `screenshot`.
- Each device-control step needs its own `deviceControl.*` token.

Its only caller today is `runner/pipeline.py`. That caller uses `unsupported(scenario, self.caps)`.
It fails a scenario before any device work when the actuator **already selected** does not cover
the scenario.

The same computation can run against each candidate actuator's capability set, instead of one fixed
set. It is then the natural resolver for "which is the cheapest actuator in this platform's ladder
that this scenario can actually run on". That needs no new capability model and no duplicated
analysis. It is merely a different consumer of the function that already exists.

## Detailed design

### A cost-ordered, capability-checked resolver

Add a resolution function. Its name is illustrative, for example `select_actuator_for_scenario`. It
takes a platform's actuator candidates and a scenario. It returns the first candidate, in cost order
with the cheapest first, that is both available and sufficient. Sufficient means
`capability_preflight.unsupported(scenario, capabilities_for(candidate)) == []`. The function
escalates to the next, richer candidate only when the cheaper one fails the check.

For iOS, the cost order is `("idb", "xcuitest")`. That is the reverse of BE-0019's existing
capability-first stability order `("xcuitest", "idb")`. The two orderings answer different questions:
which actuator is most capable, and which is cheapest to run. On a future platform with more than two
actuators, the two orderings need not be exact reverses of each other. The cost order should therefore be its own explicit ordering.
It sits next to `PLATFORMS`, for example as a parallel `COST_ORDER` table. It is not an implicit
`reversed()` of the stability ladder.

`select_actuator` (availability-only, no scenario) is unchanged. It keeps serving every caller that
has no scenario in hand yet. Those callers are `doctor`, the pool's up-front environment setup, and
an explicit single-actuator pin.

An explicit single-actuator request stays a hard override with no fallback. This is consistent with
the existing rule that `--backend <one>` behaves like `--udid`: pinned means pinned (DESIGN §3.3).
The resolver activates only when the requested `backend` list resolves to more than one iOS actuator.
Examples are `backend: [ios]` and the explicit `backend: [idb, xcuitest]`. That is also the migration
path for `showcase.config.yaml`. Replace the hard `backend: [idb]` pins with the ladder, and the
resolver takes over per scenario.

### Moving selection from once-per-invocation to once-per-scenario

Today `device_pool()` (`bajutsu/runner/pool.py:106`) calls `select_actuator` exactly once. Every
`lease()` call closes over the result for the rest of the pool's life. Every scenario in one CLI
invocation, parallel or not, therefore runs on the same fixed actuator. In `pipeline.py`,
`_ScenarioRunner.actuator` is one frozen field reused across the thread pool.

Making the choice capability-aware means making it **per scenario**. The existing seam is closer than
it looks. `lease(eff, scenario)` already takes the scenario as a parameter (`pool.py:147`). Inside
`lease()`, `launch_driver(udid, eff, actuator, ...)` is called with the actuator closed over from the
outer scope.

The change is to move the resolver call from `device_pool()`'s setup into `lease()`. The actuator is
then resolved from `scenario` right where the scenario is already available. This keeps the
single-actuator-per-scenario-execution invariant exactly as strict as today.

BE-0019 and BE-0020 say the actuator is "fixed once, held for the whole run". That now reads as "held
for the whole scenario", not "held for the whole invocation". The unit narrows, but the rule does not
relax: at every instant, exactly one actuator acts on the leased device.

Some state must follow that move. Three things are computed once today, outside `lease()`, from the
one pool-wide actuator: `pool_env` / `environment_for(actuator, ...)`, the evidence-provider
resolution (BE-0020), and the device catalog. Whichever of them differ meaningfully between idb and XCUITest must move inside
`lease()` too, and be resolved per scenario. The chief difference is whether the actuator needs a
resident runner started and torn down, which BE-0019 already scopes per lease.

This is real, scoped surgery inside `pool.py`, not a new subsystem. The shape (per-lease environment,
per-lease driver, per-lease evidence hookup) already exists for exactly this reason.

### Disclosure needs no schema change

`bajutsu/report/manifest.py:37-44` already anticipates this. `_run_backend()` joins the distinct
`RunResult.backend` values across a run's scenarios. It does so *precisely because* each `RunResult`
already carries its own `backend` field. Its comment reads: "one actuator is fixed per run, so this is
normally a single name; if scenarios somehow differ, they are joined."

That seam has sat unused, because every scenario has only ever gotten the same actuator. This
proposal is the first change that makes the actuators actually differ, and the manifest already knows
how to report it.

`doctor --target` gains a small extension. Alongside its existing idb and XCUITest availability
checks, it summarizes how many of the target's scenarios will resolve to which actuator. The summary
comes from a pure `capability_preflight` pass that needs no device. It is informational disclosure, not a new
gate.

### Migration for the showcase config

With the resolver in place, the `-noax` targets in `demos/showcase/showcase.config.yaml` drop the
`backend: [idb]` pin. They use `backend: [ios]` or `[idb, xcuitest]` instead. The
`ios/scenarios-xcuitest/` directory is folded back into the shared `scenarios/` directory.

A tab-crossing scenario then resolves to `xcuitest` on its own, and everything else stays on `idb`.
Both live in the same directory, with no `--backend` flag anywhere in the invocation. This is the
concrete, dogfooded proof that the gap described in *Motivation* is closed.

### Validation

- **Fast gate (no device).** The resolver is a pure function of `(candidates, scenario, available)`.
  - Unit tests build small fake scenarios: a plain-tap scenario, a `pinch` scenario, and a
    device-control-only scenario.
  - The tests assert cost-first selection.
  - They assert escalation only when the cheap actuator's `unsupported()` is not empty.
  - They assert that an explicit single-actuator request never escalates.
  - `_run_backend()`'s join-when-differing behavior gets a test with two `RunResult`s carrying
    different `backend` values. That case is now reachable, no longer a hypothetical in a comment.
- **On-device (e2e path).** Run the migrated showcase `-noax` target through `--backend ios`. In the
  same invocation, the manifest records `idb` for the ordinary scenarios and `xcuitest` for the
  tab-crossing one, with no separate scenario directory.

## Alternatives considered

- **Keep the existing richness-first ladder and just stop overriding it (always prefer XCUITest when
  available).** Rejected. This optimizes for capability and ignores cost, which is exactly why every
  real config pins idb today. It would trade a fast, low-dependency default for a heavier one on every
  scenario, not just the few that need it.
- **Step-level actuator hand-off within a single scenario execution.** Considered directly, because
  it is the most literal reading of "let bajutsu pick per interaction, not per file". Rejected, for
  two reasons.
  - idb's capability set is a strict subset of XCUITest's (see *Motivation*). No scenario ever mixes a
    step only idb can do with a step only XCUITest can do. Whatever a mixed scenario needs, running
    the whole scenario on XCUITest already satisfies. A runtime hand-off would need state continuity
    across a live driver swap mid-scenario and strictly sequential exclusive access. It would add a
    real determinism risk for zero capability benefit.
  - It reverses a decision already made explicitly twice. BE-0019's alternatives considered rejects
    "route only specific gestures to XCUITest while idb stays the actuator", because that reintroduces
    "the non-determinism the single-actuator rule (DESIGN §3.3/§5) exists to prevent". BE-0020's
    alternatives considered rejects letting "a fallback backend actuate when it is 'better' for a
    step" for the same reason.

  This proposal keeps that rule intact. It narrows the *unit* the rule applies to, one scenario's
  execution instead of one whole CLI invocation. It never lets two actuators act on one device at
  once.
- **Fix idb's tab-bar blindness directly (BE-0223's Android-style fix, applied to iOS).** This is a
  real, complementary option. BE-0223 made adb's single actuator resolve the shared tab-bar selector
  instead of switching backends. But idb's tab-bar gap is a `describe-all` tree limitation, not the
  parsing gap that adb's fix addressed. And idb's multi-touch gap is architectural: idb is
  single-touch only. Narrowing what needs escalation is a valid follow-up. It does not remove the
  need for automatic, capability-aware selection for whatever gap remains, starting with `multiTouch`,
  which no idb-side fix can close.
- **A scenario-level declared `requires: [multiTouch]` field instead of deriving it from steps.**
  Rejected as a second source of truth. `capability_preflight.py` already derives the exact same set
  from the steps themselves. A separate declaration could drift from what the scenario actually does,
  declaring less or more than it uses, with nothing to catch the mismatch.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Cost-ordered resolver `select_actuator_for_scenario` in `bajutsu/backends.py` (with an explicit
      `COST_ORDER` table), reusing `capability_preflight.unsupported` against each candidate's
      capability set; a fast-gate unit-tested pure function.
- [x] Per-scenario resolution wired into `runner/pool.py`'s `lease()` (moved from `device_pool()`'s
      one-time setup), narrowing the single-actuator invariant to "per scenario execution" without
      ever relaxing it. The pipeline preflight (`runner/pipeline.py`) resolves the same pure function
      per scenario, so a scenario no available actuator can run still fails fast before any device.
- [x] Whatever pool-level state depends on the actuator resolved per lease instead of once per pool:
      `environment_for` and evidence-provider resolution move inside `lease()`, and `launch_driver`
      gains an `environment` param so the instance that *starts* a lease is the one that *tears it
      down* — fixing a latent XCUITest resident-runner lifecycle bug (two separate env instances)
      that per-scenario XCUITest selection would otherwise hit.
- [x] `doctor --target` disclosure of the per-scenario actuator resolution (informational, no new
      gate).
- [x] `demos/showcase/showcase.config.yaml` migration — **a11y-ON targets only.** `showcase-swiftui`
      / `showcase-uikit` move to `backend: [ios]`; the shared `gestures_multitouch.yaml` (multiTouch)
      is the dogfooded per-scenario escalation. The `-noax` targets are deliberately **not** migrated
      and `ios/scenarios-xcuitest/` is **not** folded back: their XCUITest need is app-level (no
      accessibility ids; idb's `describe-all` collapses the tab bar), which the capability model does
      not represent — a `[ios]` ladder would mis-route their (capability-clean) tab-crossing scenarios
      to idb. That gap is BE-0223-style actuator strengthening, which this proposal's *Alternatives*
      already assigns there; recorded in the config comments.
- [ ] On-device validation (follow-up, needs a Simulator + a built XCUITest runner, outside the fast
      gate): confirm the migrated `make -C demos/showcase run-swiftui` records `idb` for ordinary
      scenarios and `xcuitest` for `gestures_multitouch` in one manifest, through the full `run` path.

## References

[DESIGN §1 / §3.3 / §5](../../DESIGN.md), `docs/drivers.md`, `docs/multi-platform.md`;
`bajutsu/backends.py` (`PLATFORMS`, `select_actuator`, `capabilities_for`),
`bajutsu/capability_preflight.py` (`unsupported`), `bajutsu/runner/pool.py` (`device_pool`,
`lease`), `bajutsu/runner/pipeline.py` (`_ScenarioRunner.actuator`), `bajutsu/report/manifest.py`
(`_run_backend`), `bajutsu/drivers/idb.py` / `bajutsu/drivers/xcuitest.py` (`CAPABILITIES`),
`demos/showcase/showcase.config.yaml`.

**Dependencies / related items:** [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
(the actuator this proposal learns to reach for), [BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)
(the sibling mechanism this proposal deliberately does not touch — evidence fallback stays
read-only, actuation stays single), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
(supplies the exact function this proposal reuses as a resolver input), [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md)
(documents the idb tab-bar gap this proposal routes around automatically), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md)
(the Android precedent for fixing the same class of gap by strengthening the one actuator instead of
switching backends — a complementary, not competing, direction for iOS).
