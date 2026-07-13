**English** · [日本語](BE-0256-platform-lifecycle-package-split-ja.md)

# BE-0256 — Split platform_lifecycle into a package and route device resolution through the Environment seam

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0256](BE-0256-platform-lifecycle-package-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0256") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[BE-0197](../BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape.md) evened out
the shape of the `Environment` Protocol and its implementers so a third platform could adopt it
without guessing. It deliberately left the module's *size* and the CLI's use of the seam untouched.
`bajutsu/platform_lifecycle.py` is now 1076 lines carrying six-plus distinct responsibilities in one
file, and four call sites outside it (`record`, `run`, `audit`, `doctor`) still branch on the
actuator name string instead of asking the `Environment` what it can do — one of those branches is
outright wrong for `xcuitest`. This item splits the module into a package along its existing
seams and adds the two `Environment` query points the CLI is missing, so the actuator's identity
stops leaking past the seam BE-0197 evened out.

## Motivation

A 1076-line module is hard to navigate: `platform_lifecycle.py` mixes the `Environment` family of
Protocols, six concrete environment classes, the `environment_for` factory, a second readiness-polling
implementation distinct from `base.wait_until`, the relauncher factories, the `DeviceControl`
factories, and XCUITest-only `.xctestrun` packaging helpers, all in one file every platform's code
loads regardless of which platform is in play. `XcuitestEnvironment.start` alone runs to roughly 95
lines. This is exactly the kind of god-module that makes "where does the iOS device-catalog logic
live" or "what does adding a platform touch" harder to answer than it needs to be — the same
navigability concern BE-0197's own motivation raised about the Protocol shape, just one level up, at
the module boundary instead of the type boundary.

Separately, and independently discovered while reading the seam's consumers, four CLI/doctor call
sites hard-code actuator identity instead of asking the `Environment`:

- `bajutsu/cli/commands/record.py:259` sets `capture_video=actuator == "idb"` — wrong for
  `xcuitest`, which shares the same simctl-backed device lifecycle as `idb` and can record video the
  same way; today it silently never captures video when the scenario runs under XCUITest.
- `bajutsu/cli/commands/run.py:911` picks `resolve_device` by testing `actuator == "adb"`.
  `bajutsu/cli/commands/audit.py:157` assumes any actuator other than `playwright` resolves through
  `simctl`. `bajutsu/doctor.py:230-234` repeats the same `xcuitest`/`adb`/else branching to pick a
  query actuator and a resolver.

Each of these four sites is reimplementing, in the CLI layer, a decision the `Environment` returned
by `environment_for` already knows how to make — that is precisely what prime directive 3
(app-agnostic; platform differences live behind the seam, not scattered through the tool) says
should not happen. It is a smaller version of the same leak BE-0197 closed at the Protocol level:
that item made the *shape* of "what must a platform implement" unambiguous; this item makes sure the
*consumers* of that shape stop reaching around it to test the actuator string directly. Splitting
the module and closing the leak are naturally two facets of the same pass over this seam, so this
item proposes both together rather than as two separate roadmap entries.

## Detailed design

Both facets are behavior-preserving for every existing platform (iOS, web, fake, XCUITest, Android)
except the one named correctness fix below, and each bullet is an independently landable, mutually
exclusive unit of work.

- **Turn `platform_lifecycle.py` into a `platform_lifecycle/` package, split along the module's
  existing internal seams — no behavior change.** Proposed layout:
  - `protocols.py` — the `ReadinessResult` dataclass and the three Protocols (`RunEnvironment`,
    `CrawlEnvironment`, `Environment`), plus the module-level "declining a method" / "adding a
    platform" documentation BE-0197 wrote, which belongs beside the types it describes.
  - `readiness.py` — `_await_ready` and `_await_boot`, the two deadline-polling loops the module
    hand-rolls today; moving them out beside each other is also the natural place to fold them onto
    `base.wait_until`'s deadline discipline (see below) rather than keeping a second, subtly
    different readiness loop.
  - `device_control.py` — the `device_control` and `android_device_control` factories (the
    `_Control` classes backing the `DeviceControl` protocol), which share nothing with the
    environment classes beyond the `simctl.Env` / `adb.Env` handles they wrap.
  - `environments/` — one module per concrete implementer: `ios.py` (`_DeviceEnvironment`,
    `IosEnvironment`), `android.py` (`AndroidEnvironment`), `web.py` (`WebEnvironment`), `xcuitest.py`
    (`XcuitestEnvironment`, `_patch_xctestrun_env`, `_allocate_port`, `_RUNNER_STARTUP_TIMEOUT`), and
    `fake.py` (`FakeEnvironment`). This also isolates the `plistlib` / `tempfile` / `shlex` imports
    that only `.xctestrun` packaging needs out of the module every platform currently loads.
  - `__init__.py` — re-exports the public names (`Environment`, `environment_for`,
    `device_relauncher`, `device_control`, `android_device_control`, …) so every existing import
    site (`from bajutsu import platform_lifecycle` / `from bajutsu.platform_lifecycle import …`)
    keeps working unchanged; `environment_for` and the relauncher factories (`_web_relauncher`,
    `device_relauncher`) stay at the package root since they are the module's public factory
    surface, not tied to one platform.
  - This is a pure reorganization: no class gains or loses a method, no Protocol changes shape, and
    `environment_for`'s branching stays exactly as it is today (item two below only adds *new*
    query methods; it does not touch the factory's existing dispatch).
- **Unify `_await_ready` / `_await_boot` onto `base.wait_until`'s deadline discipline.** The module
  currently hand-rolls a second deadline-and-poll loop (`_await_ready` for the device/web families,
  `_await_boot` for Android) distinct from the one `base.wait_until` already implements and the
  runner's step-level waits rely on. These are not the same contract, so this is not a call-through:
  `base.wait_until` (`base.py:359`) requires a concrete `sel: Selector`, polls `driver.wait_for(sel)`
  at a fixed interval, and returns a bare `bool`, whereas `_await_ready` polls `driver.query()`,
  falls back through `ready_sel` → `id_namespaces` → a bare-count heuristic when no selector is
  given, backs off exponentially (`poll_init` → `poll_max`), and returns a `ReadinessResult` (signal
  + elapsed) that BE-0231's timeout diagnostics depend on. So the plan is to extract the shared
  monotonic-deadline/exponential-backoff *loop skeleton* into one primitive that both
  `base.wait_until` and `_await_ready` build on — each keeping its own poll body and return type —
  rather than routing `_await_ready` through `base.wait_until` (which would drop the fallback tiers
  and `ReadinessResult`). That removes a second hand-rolled deadline implementation as a side effect
  of the split, honoring determinism-first (prime directive 2), without changing what either caller
  waits *for*.
- **Add `Environment.resolve_device(actuator, udid) -> str` to the Protocol.** Each environment
  already knows how its own platform resolves a device handle (`simctl.resolve_udid` for the iOS
  family, `adb.resolve_serial` for Android, a passthrough for web); expose it as a Protocol method
  so `run.py:911`, `audit.py:157`, and `doctor.py:230-234` call `environment_for(actuator,
  udid).resolve_device(...)` instead of re-deriving the same `actuator == "adb"` / `else simctl`
  branch three times. The three call sites collapse to the same one-line call regardless of which
  platform is added next.
- **Add a new `Environment.captures_video -> bool` predicate so `capture_video` is asked of the
  seam, not spelled out as `actuator == "idb"`.** This must be a new predicate, not a reuse of the
  existing `records_video_up_front`: that one answers the orthogonal "wired up front vs. on demand"
  axis (every simctl-backed env returns `False`, so a platform that *can* record (idb/xcuitest) and
  one that *can't* (fake) share the same value), and it already has a caller with that meaning
  (`runner/pool.py:172`), so it has no slot for the "can this platform record at all" axis. This is the one behavior change in the item: `record.py:259` currently never
  captures video under `xcuitest`, even though `XcuitestEnvironment` shares `IosEnvironment`'s
  simctl-backed device and can record the same way `idb` does. Routing the check through the
  `Environment` fixes the bug as a direct consequence of closing the leak, rather than as a
  separately reasoned-about fix; a regression test pins `capture_video` true for both `idb` and
  `xcuitest` and false only where a platform genuinely cannot record (e.g. `fake`).
- **Update the four call sites to use the two new seam methods and delete their actuator-string
  branches.** `record.py`, `run.py`, `audit.py`, and `doctor.py` each replace their local
  `actuator == "..."` conditional with a call through the `Environment` the surrounding code already
  constructs (or constructs one where it does not yet). No new CLI flag or config key is introduced;
  this is purely routing an existing decision through the existing seam.

Nothing here changes a scenario's YAML, a selector's semantics, or introduces a fixed `sleep`
(prime directive 2 stays intact — the readiness unification *removes* a duplicate deadline loop,
it does not add one). The package split and the seam methods are both squarely in service of
prime directive 3: platform differences keep living behind `Environment`, and after this item the
CLI/doctor layer no longer contains an actuator name it has to get right by hand. No LLM call is
introduced anywhere in this path; `platform_lifecycle` remains deterministic-core code exercised by
the Tier-2 `run`/CI gate (prime directive 1 is unaffected).

## Alternatives considered

- **Add the two seam methods (`resolve_device`, `captures_video`) without splitting the module.**
  Rejected as the whole fix: the god-module is the harder-to-navigate half of the problem, and
  leaving `platform_lifecycle.py` at 1076 lines means every future platform or seam method still
  lands in one file. Both facets touch the same seam and are best reviewed together while the file
  is already open; splitting them into two roadmap items would just mean re-deriving this context
  twice.
- **Split the module but leave the four actuator-string branches alone.** Rejected because the
  branches are a live correctness bug (`xcuitest` never captures video) discovered by reading the
  seam's consumers during this same pass, and because a package split with no reason to touch its
  call sites is a lower-value refactor on its own — the navigability win is real, but pairing it
  with closing the leak is what turns this into a prime-directive-3 fix rather than a pure tidy-up.
- **Encode `resolve_device` as a free function keyed on actuator (e.g. a
  `resolve_device(actuator, udid)` module-level dispatcher) instead of an `Environment` method.**
  Rejected: a free function keyed on the actuator string is the exact shape this item removes — it
  would just move the `actuator == "adb"` branch from the four call sites into one new function
  instead of deleting it. An `Environment` method keeps the dispatch where BE-0009 and BE-0197 already
  put every other per-platform decision.
- **Defer the split until Android's own environment work needs it.** Rejected on the same reasoning
  BE-0197 used against deferring to the Android build: the module is already large with two platform
  families in it (device-style and web), and Android is a third with more code (`AndroidEnvironment`)
  already living here. Splitting now, before a fourth platform's code adds to the same file, is
  cheaper than splitting after.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Split `platform_lifecycle.py` into the `platform_lifecycle/` package (`protocols.py`,
      `readiness.py`, `device_control.py`, `environments/{ios,android,web,xcuitest,fake}.py`,
      `__init__.py` re-exports) with no behavior change
- [ ] Unify `_await_ready` / `_await_boot` onto `base.wait_until`'s deadline discipline
- [ ] Add `Environment.resolve_device(actuator, udid)` and route `run.py` / `audit.py` / `doctor.py`
      through it
- [ ] Add a new `Environment.captures_video` predicate (not a reuse of `records_video_up_front`) and route
      `record.py`'s `capture_video` through it, fixing the XCUITest video-capture bug, with a
      regression test

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
