**English** · [日本語](BE-0240-ios-capability-aware-actuator-selection-ja.md)

# BE-0240 — Capability-aware automatic actuator selection for iOS (idb/XCUITest transparency)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0240](BE-0240-ios-capability-aware-actuator-selection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0240") |
| Topic | Backend expansion (iOS actuators) |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0020](../BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md) |
<!-- /BE-METADATA -->

## Introduction

iOS has two actuators — idb and XCUITest — and `bajutsu/backends.py` already declares them as an
ordered ladder (`PLATFORMS["ios"] = ("xcuitest", "idb")`, BE-0019). In principle a scenario author
writes `platform: ios` (or `backend: [ios]`) and never names an actuator; in practice, every real
config pins `backend: [idb]` and a whole second scenario directory
(`demos/showcase/ios/scenarios-xcuitest/`) exists solely to route the handful of scenarios that
need XCUITest's richer capabilities. This proposal makes the *scenario itself* decide which iOS
actuator it needs, from the capability requirements it already implies through its own steps —
closing the gap between what the author writes (a platform) and what actually ran it (an actuator),
which today leaks into scenario file layout and CLI flags instead of staying an implementation
detail.

## Motivation

### The gap between the designed ladder and how it is actually used

BE-0019 built `select_actuator` to expand a platform token to its actuators in stability order and
pick the first available one — so `--backend ios` was meant to prefer XCUITest and fall back to
idb only when XCUITest's toolchain (`xcodebuild`) is unavailable. But `demos/showcase/showcase.config.yaml`
does not use that default resolution for any real target:

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

Every target pins `backend: [idb]` explicitly, and the two `-noax` targets carry an entirely
separate `scenarios` directory whose sole reason to exist is that idb's `describe-all` collapses a
`UITabBarController` / `TabView` into one opaque "Tab Bar" group with no per-tab children
(documented in BE-0107 and BE-0019's on-device investigation note), so crossing a tab needs
`--backend xcuitest` and a scenario written against XCUITest's addressing. The scenario author —
or, today, the config author — has to know in advance which of the two backends a given scenario
needs, split it into the matching directory, and invoke it with the matching flag. That is exactly
the backend-awareness the "a platform is a backend behind one interface" principle
([DESIGN §1](../../DESIGN.md)) is meant to keep out of the author's hands; it has simply moved from
a per-run CLI flag into a per-directory, per-target config convention, which is no more
transparent.

### idb's capability set is a strict subset of XCUitest's — cost, not capability, is why it's still preferred

Reading the two drivers' declared `CAPABILITIES` side by side settles a question this proposal
would otherwise have to hedge on:

```python
# bajutsu/drivers/idb.py
CAPABILITIES = frozenset({QUERY, ELEMENTS, SCREENSHOT}) | DEVICE_CONTROL_ALL

# bajutsu/drivers/xcuitest.py
CAPABILITIES = frozenset({QUERY, ELEMENTS, SCREENSHOT, SEMANTIC_TAP, CONDITION_WAIT, MULTI_TOUCH}) | DEVICE_CONTROL_ALL
```

XCUITest's set is a strict superset of idb's — every device-control operation idb backs
(`setLocation` / `clipboard` / `push` / `clearKeychain` / `appLifecycle` / `statusBar`) is backed by
XCUITest too, because it shares the same `simctl`-driven Simulator lifecycle (BE-0128 / BE-0212).
There is therefore no construct a scenario can use that idb supports and XCUITest does not — no
scenario *needs* idb specifically. What idb has going for it is cost, not capability: it needs only
the `idb`/`idb_companion` client (no Xcode toolchain), no built test-runner
(`xcuitest.testRunner`/`xcuitest.build`, an extra per-target setup step), no resident process per
leased device, and it carries years of hardening (the weekly `idb-monitor.yml` compatibility
watch). BE-0019's richness-first ladder (prefer XCUITest, fall back to idb only on unavailability)
optimizes for capability and ignores this cost difference — which is precisely why every real
config overrides it back to a hard idb pin. The right default is the opposite priority: prefer the
cheap actuator, and promote to the richer one **only** for the scenarios whose own steps actually
need it.

### The preflight mechanism already computes exactly what's needed — today only to reject, never to choose

`bajutsu/capability_preflight.py` (BE-0082) already walks a scenario's whole step tree (recursing
into `if` / `forEach`) and derives, as a pure function of `(scenario, capability set)`, every
construct that needs a capability the set lacks: `pinch`/`rotate` need `multiTouch`, a `visual`
assertion needs `screenshot`, each device-control step needs its own `deviceControl.*` token. Its
only caller today, `runner/pipeline.py`, uses `unsupported(scenario, self.caps)` to fail a scenario
before any device work when the actuator **already selected** doesn't cover it. The exact same
computation — run against each candidate actuator's capability set instead of one fixed set — is
the natural resolver for "which is the cheapest actuator in this platform's ladder that this
scenario can actually run on": no new capability model, no duplicated analysis, just a different
consumer of the function that already exists.

## Detailed design

### A cost-ordered, capability-checked resolver

Add a resolution function (name illustrative, e.g. `select_actuator_for_scenario`) that, given a
platform's actuator candidates and a scenario, returns the first candidate — **in cost order**,
cheapest first — that is both available and sufficient (`capability_preflight.unsupported(scenario,
capabilities_for(candidate)) == []`), escalating to the next, richer candidate only when the
cheaper one fails the check. For iOS, cost order is `("idb", "xcuitest")` — the reverse of
BE-0019's existing capability-first stability order `("xcuitest", "idb")`. The two orderings answer
different questions (which actuator is most capable vs. which is cheapest to run) and needn't
always be exact reverses of each other on a future platform with more than two actuators, so this
should be its own explicit ordering next to `PLATFORMS` (e.g. a parallel `COST_ORDER` table), not an
implicit `reversed()` of the stability ladder. `select_actuator` (availability-only, no scenario) is
unchanged and keeps serving every caller that has no scenario in hand yet (`doctor`, the pool's
up-front environment setup, an explicit single-actuator pin).

An explicit single-actuator request stays a hard override with no fallback, consistent with the
existing rule (`--backend <one>` behaves like `--udid`: pinned means pinned, DESIGN §3.3). This
resolver only activates when the requested `backend` list resolves to more than one iOS actuator —
`backend: [ios]` or the explicit `backend: [idb, xcuitest]` — which is also the migration path for
`showcase.config.yaml`: replace the hard `backend: [idb]` pins with the ladder, and the resolver
takes it from there per scenario.

### Moving selection from once-per-invocation to once-per-scenario

Today `device_pool()` (`bajutsu/runner/pool.py:106`) calls `select_actuator` exactly once, and the
result is closed over by every `lease()` call for the rest of the pool's life — every scenario in
one CLI invocation, parallel or not, runs on the same fixed actuator
(`_ScenarioRunner.actuator` in `pipeline.py` is one frozen field reused across the thread pool).
Making the choice capability-aware means making it **per scenario** — but the existing seam for
this is closer than it looks: `lease(eff, scenario)` already takes the scenario as a parameter
(`pool.py:147`), and it is inside `lease()` that `launch_driver(udid, eff, actuator, ...)` is
called using the actuator closed over from the outer scope. Moving the resolver call from
`device_pool()`'s setup into `lease()` — resolving the actuator from `scenario` right where the
scenario is already available — keeps the single-actuator-per-scenario-execution invariant exactly
as strict as it is today (BE-0019/BE-0020's "actuator fixed once, held for the whole run" now reads
as "held for the whole scenario," not "held for the whole invocation" — a narrowing of the unit, not
a relaxation of the rule: at every instant, still exactly one actuator acts on the leased device).

What has to follow that move: `pool_env` / `environment_for(actuator, ...)`, the evidence-provider
resolution (BE-0020), and the device catalog are today computed once outside `lease()` from the one
pool-wide actuator; whichever of these differ meaningfully between idb and XCUITest (chiefly:
whether the actuator needs a resident runner started/torn down, which BE-0019 already scopes per
lease) need to move inside `lease()` too, resolved per scenario. This is real, scoped surgery inside
`pool.py`, not a new subsystem — the shape (per-lease environment, per-lease driver, per-lease
evidence hookup) already exists for exactly this reason.

### Disclosure needs no schema change

`bajutsu/report/manifest.py:37-44` already anticipates this: `_run_backend()` joins the distinct
`RunResult.backend` values across a run's scenarios *precisely because* each `RunResult` already
carries its own `backend` field — "one actuator is fixed per run, so this is normally a single
name; if scenarios somehow differ, they are joined." That seam has sat unused because every
scenario has only ever gotten the same actuator; this proposal is the first thing that makes them
actually differ, and the manifest already knows how to report it. `doctor --target` gains a small
extension: alongside its existing idb/XCUITest availability checks, a summary of how many of the
target's scenarios will resolve to which actuator (a pure `capability_preflight` pass, no device
needed) — informational disclosure, not a new gate.

### Migration for the showcase config

With the resolver in place, `demos/showcase/showcase.config.yaml`'s `-noax` targets drop the
`backend: [idb]` pin (replaced by `backend: [ios]` or `[idb, xcuitest]`) and
`ios/scenarios-xcuitest/` is folded back into the shared `scenarios/` directory: a tab-crossing
scenario resolves to `xcuitest` on its own, everything else stays on `idb`, in the same directory,
with no `--backend` flag anywhere in the invocation. This is the concrete, dogfooded proof that the
gap described in *Motivation* is closed.

### Validation

- **Fast gate (no device).** The resolver is a pure function of `(candidates, scenario,
  available)`: unit tests build small fake scenarios (a plain-tap scenario, a `pinch` scenario, a
  device-control-only scenario) and assert cost-first selection, escalation only when the cheap
  actuator's `unsupported()` isn't empty, and that an explicit single-actuator request never
  escalates. `_run_backend()`'s join-when-differing behavior gets a test with two `RunResult`s
  carrying different `backend` values, now a reachable case rather than a hypothetical in a
  comment.
- **On-device (e2e path).** The migrated showcase `-noax` target run through `--backend ios`: the
  manifest records `idb` for the ordinary scenarios and `xcuitest` for the tab-crossing one, in the
  same invocation, with no separate scenario directory.

## Alternatives considered

- **Keep the existing richness-first ladder and just stop overriding it (always prefer XCUITest
  when available).** Rejected: this optimizes for capability and ignores cost, which is exactly why
  every real config pins idb today — it would trade a fast, low-dependency default for a heavier one
  on every scenario, not just the few that need it.
- **Step-level actuator hand-off within a single scenario execution.** Considered directly, because
  it is the most literal reading of "let bajutsu pick per interaction, not per file." Rejected: idb's
  capability set is a strict subset of XCUITest's (see *Motivation*), so no scenario ever mixes a
  step only idb can do with a step only XCUITest can do — anything a mixed scenario needs, running
  the whole thing on XCUITest already satisfies. Building a runtime hand-off (state continuity
  across a live driver swap mid-scenario, strictly sequential exclusive access) would add a real
  determinism risk for zero capability benefit, and reverses a decision already made explicitly
  twice: BE-0019's alternatives considered rejects "route only specific gestures to XCUITest while
  idb stays the actuator" as reintroducing "the non-determinism the single-actuator rule (DESIGN
  §3.3/§5) exists to prevent," and BE-0020's alternatives considered rejects letting "a fallback
  backend actuate when it is 'better' for a step" for the same reason. This proposal keeps that rule
  intact — it narrows the *unit* the rule applies to (one scenario's execution, not one whole CLI
  invocation) without ever letting two actuators act on one device at once.
- **Fix idb's tab-bar blindness directly (BE-0223's Android-style fix, applied to iOS).** A real,
  complementary option — BE-0223 made adb's single actuator resolve the shared tab-bar selector
  instead of switching backends — but idb's tab-bar gap is a `describe-all` tree limitation, not a
  parsing gap adb's fix addressed, and idb's multi-touch gap is architectural (single-touch only).
  Narrowing what needs escalation is a valid follow-up; it does not remove the need for automatic,
  capability-aware selection for whatever gap remains (starting with `multiTouch`, which no
  idb-side fix can close).
- **A scenario-level declared `requires: [multiTouch]` field instead of deriving it from steps.**
  Rejected as a second source of truth: `capability_preflight.py` already derives the exact same set
  from the steps themselves, so a separate declaration could drift from what the scenario actually
  does (declare less than it uses, or more) with nothing to catch the mismatch.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Cost-ordered resolver (`select_actuator_for_scenario` or equivalent) in `bajutsu/backends.py`,
      reusing `capability_preflight.unsupported` against each candidate's capability set; a fast-gate
      unit-tested pure function.
- [ ] Per-scenario resolution wired into `runner/pool.py`'s `lease()` (moved from `device_pool()`'s
      one-time setup), narrowing the single-actuator invariant to "per scenario execution" without
      ever relaxing it.
- [ ] Whatever pool-level state depends on the actuator (`environment_for`, evidence-provider
      resolution, resident-runner lifecycle) resolved per lease instead of once per pool.
- [ ] `doctor --target` disclosure of the per-scenario actuator resolution (informational, no new
      gate).
- [ ] `demos/showcase/showcase.config.yaml` migration: drop the `-noax` targets' `backend: [idb]`
      pins, fold `ios/scenarios-xcuitest/` back into `scenarios/`.
- [ ] On-device validation: the migrated showcase run records differing per-scenario `backend`
      values in one manifest, through the full `run` path.

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
