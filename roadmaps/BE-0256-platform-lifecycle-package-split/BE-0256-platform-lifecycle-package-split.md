**English** · [日本語](BE-0256-platform-lifecycle-package-split-ja.md)

# BE-0256 — Split platform_lifecycle into a package and route device resolution through the Environment seam

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0256](BE-0256-platform-lifecycle-package-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0256") |
| Implementing PR | [#1080](https://github.com/bajutsu-e2e/bajutsu/pull/1080) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[BE-0197](../BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape.md) evened out
the shape of the `Environment` Protocol and its implementers, so a third platform could adopt it
without guessing. It deliberately left two things untouched: the module's *size*, and the CLI's use
of the seam.

Two problems remain:

- `bajutsu/platform_lifecycle.py` is now 1076 lines. It carries six-plus distinct responsibilities
  in one file.
- Four call sites outside it (`record`, `run`, `audit`, `doctor`) still branch on the actuator name
  string. They do not ask the `Environment` what it can do. One of those branches is outright wrong
  for `xcuitest`.

This item splits the module into a package along its existing seams. It also adds the two
`Environment` query points the CLI is missing. The actuator's identity then stops leaking past the
seam that BE-0197 evened out.

## Motivation

A 1076-line module is hard to navigate. `platform_lifecycle.py` mixes the following in one file:

- the `Environment` family of Protocols;
- six concrete environment classes;
- the `environment_for` factory;
- a second readiness-polling implementation, distinct from `base.wait_until`;
- the relauncher factories;
- the `DeviceControl` factories;
- XCUITest-only `.xctestrun` packaging helpers.

Every platform's code loads this file, whichever platform is in play. `XcuitestEnvironment.start`
alone runs to roughly 95 lines.

This is a god-module. Questions like "where does the iOS device-catalog logic live" or "what does
adding a platform touch" are harder to answer than they need to be. BE-0197's own motivation raised
the same navigability concern about the Protocol shape. This item raises it one level up, at the
module boundary instead of the type boundary.

Separately, reading the seam's consumers turned up a second problem. Four CLI and doctor call sites
hard-code actuator identity instead of asking the `Environment`:

- `bajutsu/cli/commands/record.py:259` sets `capture_video=actuator == "idb"`. This is wrong for
  `xcuitest`, which shares the simctl-backed device lifecycle of `idb` and can record video the
  same way. Today a scenario run under XCUITest never captures video, and nothing reports it.
- `bajutsu/cli/commands/run.py:911` picks `resolve_device` by testing `actuator == "adb"`.
- `bajutsu/cli/commands/audit.py:157` assumes any actuator other than `playwright` resolves through
  `simctl`.
- `bajutsu/doctor.py:230-234` repeats the same `xcuitest`/`adb`/else branching. It uses the branch
  to pick a query actuator and a resolver.

Each of the four sites reimplements, in the CLI layer, a decision that the `Environment` returned
by `environment_for` already knows how to make. Prime directive 3 says this should not happen:
the tool is app-agnostic, and platform differences live behind the seam, not scattered through the
tool.

The leak is a smaller version of the one BE-0197 closed at the Protocol level. BE-0197 made the
*shape* of "what must a platform implement" unambiguous. This item makes the *consumers* of that
shape stop reaching around it to test the actuator string.

Splitting the module and closing the leak are two facets of the same pass over this seam. This item
proposes both together instead of as two roadmap entries.

## Detailed design

Both facets preserve behavior for every existing platform (iOS, web, fake, XCUITest, Android),
except for one named correctness fix below. Each bullet is an independently landable, mutually
exclusive unit of work.

- **Turn `platform_lifecycle.py` into a `platform_lifecycle/` package.** The split follows the
  module's existing internal seams and changes no behavior. The proposed layout is:
  - `protocols.py`: the `ReadinessResult` dataclass and the three Protocols (`RunEnvironment`,
    `CrawlEnvironment`, `Environment`). It also holds the module-level "declining a method" and
    "adding a platform" documentation that BE-0197 wrote, which belongs beside the types it
    describes.
  - `readiness.py`: `_await_ready` and `_await_boot`, the two deadline-polling loops the module
    hand-rolls today. Moving them out beside each other is the natural place to fold them onto
    the deadline discipline of `base.wait_until` (see the next bullet). The module then stops
    keeping a second, subtly different readiness loop.
  - `device_control.py`: the `device_control` and `android_device_control` factories (the
    `_Control` classes backing the `DeviceControl` protocol). They share nothing with the
    environment classes beyond the `simctl.Env` and `adb.Env` handles they wrap.
  - `environments/`: one module per concrete implementer.
    - `ios.py`: `_DeviceEnvironment` and `IosEnvironment`.
    - `android.py`: `AndroidEnvironment`.
    - `web.py`: `WebEnvironment`.
    - `xcuitest.py`: `XcuitestEnvironment`, `_patch_xctestrun_env`, `_allocate_port`, and
      `_RUNNER_STARTUP_TIMEOUT`.
    - `fake.py`: `FakeEnvironment`.

    This layout also moves the `plistlib`, `tempfile`, and `shlex` imports out of the module that
    every platform loads. Only `.xctestrun` packaging needs them.
  - `__init__.py`: re-exports the public names (`Environment`, `environment_for`,
    `device_relauncher`, `device_control`, `android_device_control`, and the rest). Every existing
    import site (`from bajutsu import platform_lifecycle` and `from bajutsu.platform_lifecycle
    import …`) keeps working unchanged. `environment_for` and the relauncher factories
    (`_web_relauncher`, `device_relauncher`) stay at the package root. They are the module's public
    factory surface and are not tied to one platform.
  - The split is a pure reorganization. No class gains or loses a method, and no Protocol changes
    shape. `environment_for`'s branching stays exactly as it is today. The later bullets only add
    *new* query methods and do not touch the factory's existing dispatch.
- **Unify `_await_ready` and `_await_boot` onto the deadline discipline of `base.wait_until`.**
  The module hand-rolls a second deadline-and-poll loop today. `_await_ready` serves the
  device/web families, and `_await_boot` serves Android. The loop is distinct from the one
  `base.wait_until` implements, which the runner's step-level waits rely on.

  The two contracts differ, so this is not a call-through.
  - `base.wait_until` (`base.py:359`) requires a concrete `sel: Selector`. It polls
    `driver.wait_for(sel)` at a fixed interval and returns a bare `bool`.
  - `_await_ready` polls `driver.query()`. When no selector is given, it falls back through
    `ready_sel`, then `id_namespaces`, then a bare-count heuristic. It backs off exponentially
    (`poll_init` to `poll_max`). It returns a `ReadinessResult` (signal plus elapsed time) that
    the timeout diagnostics of BE-0231 depend on.

  The plan is therefore to extract the shared monotonic-deadline and exponential-backoff *loop
  skeleton* into one primitive. Both `base.wait_until` and `_await_ready` build on it, and each
  keeps its own poll body and return type. Routing `_await_ready` through `base.wait_until` would
  drop the fallback tiers and `ReadinessResult`, so the plan avoids it.

  The split thus removes a second hand-rolled deadline implementation as a side effect. This honors
  determinism-first (prime directive 2). Neither caller changes what it waits *for*.
- **Add `Environment.resolve_device(actuator, udid) -> str` to the Protocol.** Each environment
  already knows how its own platform resolves a device handle: `simctl.resolve_udid` for the iOS
  family, `adb.resolve_serial` for Android, and a passthrough for web. Exposing that knowledge as a
  Protocol method lets `run.py:911`, `audit.py:157`, and `doctor.py:230-234` call
  `environment_for(actuator, udid).resolve_device(...)`. Today the three sites re-derive the same
  `actuator == "adb"` / `else simctl` branch. The three call sites collapse to the same one-line
  call, whichever platform is added next.
- **Add a new `Environment.captures_video -> bool` predicate.** The seam then answers
  `capture_video`, instead of the CLI spelling it out as `actuator == "idb"`.
  - The predicate must be new. It cannot reuse the existing `records_video_up_front`. That
    predicate answers the orthogonal axis "wired up front versus on demand". Every simctl-backed
    environment returns `False`, so a platform that *can* record (idb, xcuitest) and one that
    *can't* (fake) share the same value. It also already has a caller with that meaning
    (`runner/pool.py:172`). It has no slot for the axis "can this platform record at all".
  - This is the one behavior change in the item. `record.py:259` never captures video under
    `xcuitest` today, although `XcuitestEnvironment` shares the simctl-backed device of
    `IosEnvironment` and can record the way `idb` does.
  - Routing the check through the `Environment` fixes the bug as a direct consequence of closing
    the leak. The fix needs no separately reasoned-about change.
  - A regression test pins `capture_video` true for both `idb` and `xcuitest`. It pins `false`
    where a platform genuinely cannot record (for example `fake`).
- **Update the four call sites to use the two new seam methods and delete their actuator-string
  branches.** `record.py`, `run.py`, `audit.py`, and `doctor.py` each replace their local
  `actuator == "..."` conditional with a call through the `Environment`. The surrounding code
  already constructs one, or the call site constructs one where none exists yet. No new CLI flag or
  config key appears. The change routes an existing decision through the existing seam.

Nothing here changes a scenario's YAML or a selector's semantics. Nothing introduces a fixed
`sleep`. Prime directive 2 stays intact: the readiness unification *removes* a duplicate deadline
loop and adds none.

The package split and the seam methods both serve prime directive 3. Platform differences keep
living behind `Environment`. After this item, the CLI/doctor layer no longer contains an actuator
name that it has to get right by hand.

No LLM call enters this path. `platform_lifecycle` remains deterministic-core code, exercised by
the Tier-2 `run`/CI gate, so prime directive 1 is unaffected.

## Alternatives considered

- **Add the two seam methods (`resolve_device`, `captures_video`) without splitting the module.**
  Rejected as the whole fix. The god-module is the harder-to-navigate half of the problem. At 1076
  lines, `platform_lifecycle.py` would keep taking every future platform and seam method in one
  file. Both facets touch the same seam and are best reviewed together while the file is open.
  Two roadmap items would mean re-deriving this context twice.
- **Split the module but leave the four actuator-string branches alone.** Rejected, for two
  reasons. The branches hide a live correctness bug (`xcuitest` never captures video), found while
  reading the seam's consumers in this same pass. And a package split with no reason to touch its
  call sites is a lower-value refactor on its own. The navigability win is real. Pairing it with
  closing the leak turns the item into a prime-directive-3 fix, where alone it would be a pure
  tidy-up.
- **Encode `resolve_device` as a free function keyed on actuator (a module-level
  `resolve_device(actuator, udid)` dispatcher) instead of an `Environment` method.** Rejected. A
  free function keyed on the actuator string is the exact shape this item removes. It would move the
  `actuator == "adb"` branch from the four call sites into one new function, not delete it. An
  `Environment` method keeps the dispatch where BE-0009 and BE-0197 already put every other
  per-platform decision.
- **Defer the split until Android's own environment work needs it.** Rejected on the reasoning
  BE-0197 used against deferring to the Android build. The module is already large with two
  platform families in it (device-style and web). Android is a third, with more code
  (`AndroidEnvironment`) already living here. Splitting now is cheaper than splitting after a
  fourth platform's code adds to the same file.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Split `platform_lifecycle.py` into the `platform_lifecycle/` package (`protocols.py`,
      `readiness.py`, `device_control.py`, `environments/{ios,android,web,xcuitest,fake}.py`,
      `__init__.py` re-exports) with no behavior change
- [x] Unify `_await_ready` / `_await_boot` onto `base.wait_until`'s deadline discipline
- [x] Add `Environment.resolve_device(udid)` and route `run.py` / `audit.py` / `doctor.py`
      through it
- [x] Add a new `Environment.captures_video` predicate (not a reuse of `records_video_up_front`) and route
      `record.py`'s `capture_video` through it, fixing the XCUITest video-capture bug, with a
      regression test

**Log**

- Implemented as one PR: the `platform_lifecycle/` package split (`protocols.py`, `readiness.py`,
  `device_control.py`, `relaunchers.py`, `factories.py`, `environments/{ios,android,web,xcuitest,fake}.py`),
  a shared `base.deadline_ticks` primitive backing `base.wait_until` / `_await_ready` / `_await_boot`,
  the `resolve_device` / `captures_video` seam methods, and the four CLI/doctor call sites routed
  through them (fixing the XCUITest video-capture bug).

## References

- [BE-0197](../BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape.md) evened out
  the `Environment` Protocol's shape for a third platform; this item continues that pass over the
  same seam, one layer out — the module's internal organization and the seam's CLI-facing callers.
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  introduced the `Environment` Protocol and the `environment_for` seam this item's package split and
  new methods build on.
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) is the platform whose
  `AndroidEnvironment` code already lives in this module; the split makes room for its continued
  growth without adding to one monolithic file.
- Prime directive 3 (app-agnostic: platform differences live in the seam, not in the CLI) is the
  guiding constraint for the `resolve_device` / `captures_video` half of this item.
- `bajutsu/platform_lifecycle.py`, `bajutsu/cli/commands/record.py:259`,
  `bajutsu/cli/commands/run.py:911`, `bajutsu/cli/commands/audit.py:157`, `bajutsu/doctor.py:230-234`
  are the files this item touches.
