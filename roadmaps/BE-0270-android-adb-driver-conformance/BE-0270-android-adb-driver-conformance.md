**English** · [日本語](BE-0270-android-adb-driver-conformance-ja.md)

# BE-0270 — Driver conformance for the adb backend on-device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0270](BE-0270-android-adb-driver-conformance.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0270") |
| Implementing PR | [#1119](https://github.com/bajutsu-e2e/bajutsu/pull/1119) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

The driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
is one backend-agnostic contract with four clauses:

- An ambiguous selector fails instead of acting on the first match.
- A zero-match selector fails instead of reporting success.
- `capabilities()` matches observed behavior.
- `wait_for` is a single-shot check that the shared `wait_until` loop turns into a condition wait.

The contract lives in `tests/driver_conformance.py` and runs today against three drivers:

- `FakeDriver`, on the fast Linux gate.
- Playwright, in web CI (`tests/test_driver_conformance_web.py`).
- The two iOS backends, idb and XCUITest, on-device (`tests/test_driver_conformance_ondevice.py`,
  driven by the `conformance (idb + xcuitest)` job in `ios-e2e.yml`).

The adb backend (Android, BE-0007) is the one shipped driver that the contract never runs against.
Android's on-device lane exercises functional smoke, the element-tree golden, and the pixel visual
regression test (VRT). No job asserts that the adb driver honors the determinism-core invariants on a
real device, the way idb, XCUITest, and Playwright are each proven to. This item closes that gap.

## Motivation

- **The contract is only as strong as its coverage.** The point of BE-0114 is that the determinism-core invariants hold *identically* on every real actuator, not merely on `FakeDriver`. An unproven backend is where a divergence hides. For example, an ambiguous adb selector that taps the first match instead of failing (a prime-directive-2 violation) would pass every existing Android job, because smoke, golden, and visual all use unambiguous selectors by construction.
- **adb resolves selectors differently from the other backends.** The adb driver reads over two channels: the resident UI Automator server (BE-0245) and the `uiautomator dump` fallback. It also matches ids in two forms, dotted and underscore (BE-0221). The iOS and web conformance jobs cannot cover these adb-specific paths. The contract's ambiguous-match and zero-match cases are where the paths most need pinning.
- **Parity was explicitly deferred, not dismissed.** The in-flight platform-E2E-parity item (the `e2e-workflow-structural-parity` proposal) splits the Android lane into per-concern jobs. That item records the missing `conformance (adb)` job as a real coverage hole, and rules it out of scope because it is test-authoring work, not workflow restructuring. This item is that follow-up.

## Detailed design

This item ships product code (an app-side conformance mode and a test harness), so the prime directives apply:

- The suite is deterministic, with no LLM anywhere near the verdict.
- The suite reseeds screens with a condition wait, not a fixed `sleep`.
- Any app-side hook sits behind the same uniform opt-in that the showcase already uses, with no per-app branching in the tool.

The work divides into five units that do not overlap.

1. **An Android conformance mode in the showcase app.** The mode mirrors iOS's `ConformanceView`. A screen renders the identifier set that a spec channel names, and no other ids. Each id appears at its requested multiplicity, so the ambiguous "two `dup`s" case is real. The screen also carries an always-present readiness marker, which makes readiness a positive check and not an inference from an empty tree. The mode covers both Android UI toolkits that the lane tests, Compose and Views (BE-0221), because their id forms differ. The other option is to scope the mode to Compose and record the reason.
2. **A reseed channel over adb.** The channel is the analogue of iOS writing `conformance-spec.txt` into the app's Documents directory. The natural adb form is an `adb push` (or an intent extra) of the spec into the app's files directory. The conformance screen polls that location. `with_screen` writes the spec, then waits until the driver's own `query()` reflects the new screen. The wait is condition-backed and uses no sleep. Implementation time settles push versus intent, in favor of whichever the emulator handles most reliably.
3. **An adb conformance harness and a pytest module.** The module runs the *same* `tests/driver_conformance.py` contract. It is either a new `test_driver_conformance_ondevice_android.py` or a parametrization of the existing on-device module. The module carries the `ondevice` marker, which the gate's default `-m 'not web and not ondevice'` deselects. It also skips at module level when its serial environment variable (for example `BAJUTSU_CONFORMANCE_SERIAL`) is unset. The fast Linux gate never picks it up, which is the shape the idb, XCUITest, and web modules use.
4. **A `conformance (adb)` job in `android-e2e.yml`** (Linux with KVM). The job wires the harness to a booted Android Virtual Device (AVD). It builds the conformance-capable APK(s) and boots the emulator. It runs the suite serially against that one emulator. Like the rest of the Android lane, the job is non-required. It joins the per-concern job set that the parity item proposes: `smoke`, `golden`, `visual`, and now `conformance`.
5. **An honest `capabilities()` for the capability-matching tests.** The contract treats `capabilities()` as a promise. A `MULTI_TOUCH` or `SELECT_OPTION` backend must actually perform the action. A backend that lacks the capability must raise `UnsupportedAction`, and must not do nothing quietly. For these cases to pass, the declared capabilities of the adb driver must match its real behavior. Confirming that declaration, and correcting it if needed, is part of this work.

## Alternatives considered

- **Assert the invariants inside the existing Android smoke scenarios instead of adding a conformance job.** Rejected. Smoke scenarios drive the real app with unambiguous selectors, so they cannot exercise the ambiguous-match and zero-match cases without contorting the fixture. The conformance contract needs an app screen that it can reseed to arbitrary identifier sets. That is a purpose-built conformance mode, which the iOS suite already uses.
- **Reuse the element-tree golden as the conformance signal.** Rejected. The golden pins a *tree shape*, not *selector-resolution semantics*. A driver could match the golden and still tap the first of two ambiguous matches. Only the contract's own assertions catch that.
- **Cover only Compose and skip the Views toolkit.** This is a reasonable narrowing, because the two toolkits share the driver path. It leaves the underscore-id form (BE-0221) unproven by conformance. Implementation time (unit 1) settles the choice. Either outcome gets a record, so nothing drops out unseen.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add an Android conformance mode to the showcase app (Compose; Views per unit 1's decision).
- [x] Add the adb reseed channel (spec push/intent + condition-backed `with_screen`).
- [x] Add the adb conformance harness + `ondevice`-marked pytest module over the shared contract.
- [x] Add the `conformance (adb)` job to `android-e2e.yml`.
- [x] Confirm the adb driver's `capabilities()` matches behavior for the capability tests.

**Unit 1 decision: Compose only, Views out (recorded per *Alternatives considered*).** The contract seeds plain identifiers (`dup`, `ok`, `a`, `b`, `Log`, `g`, `sel`, `s`). None of them carries the `.` or `_` over which the two toolkits' id forms (BE-0221) differ. A Views screen would therefore resolve the identical ids through the identical `_strip_pkg` path, and add no conformance coverage.

A spec-driven screen of arbitrary ids is also expressible naturally only in Compose. Its `testTag` accepts any runtime string, surfaced as a `resource-id` through `testTagsAsResourceId`. A Views `resource-id` must be a compile-time `R` entry. Compose is the one toolkit that can render the contract's screens at all.

**Unit 2 decision: intent over file push.** The reseed re-launches the `singleTask` activity with a new `SHOWCASE_CONFORMANCE` intent extra. It delivers the extra through `onNewIntent`, the proven deep-link path. It does not push a spec file, which is iOS's channel. `adb push` cannot reach the app's sandbox. The intent reuses the `launchEnv`-to-intent-extras convention (BE-0007) and needs no new app-side polling.

The empty (zero-match) screen uses a leading `,` sentinel, so the value is never the empty string. The reason is that adb's `am start --es KEY ""` would drop the value, the trailing-empty-argument quirk that the clipboard channel also documents.

**Unit 5 finding: no `capabilities()` correction needed.** The adb driver declares `QUERY`, `ELEMENTS`, and `MULTI_TOUCH`, and omits `SELECT_OPTION`. That set already matches behavior for the capability tests on the rooted `google_apis` emulator. `pinch` and `rotate` actuate over the rooted `sendevent` sweep (BE-0232) without raising. `select_option` raises `UnsupportedAction`. The `conformance (adb)` job runs `adb root`, as the smoke lane's `gestures_multitouch` already does, to hold the `MULTI_TOUCH` precondition.

### Log

- Implemented in [#1119](https://github.com/bajutsu-e2e/bajutsu/pull/1119): Compose
  `ConformanceScreen` + `AppModel.conformanceIds`/`applyConformance` + `MainActivity`/`RootScreen`
  wiring; `tests/test_driver_conformance_ondevice_android.py` over the shared contract with the
  `am start` reseed channel; the `conformance (adb)` job in `android-e2e.yml` and the
  `e2e-conformance` Makefile target.

## References

- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md): the
  backend-agnostic conformance contract this item extends to the adb backend.
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md): the adb backend under test.
- [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md):
  the dotted/underscore id forms the conformance screen must cover.
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md):
  the resident and `uiautomator dump` read channels that the adb driver resolves selectors over.
- The `e2e-workflow-structural-parity` proposal (BE id allocated on its own merge): proposes
  splitting the Android lane into per-concern jobs and flags this `conformance (adb)` gap as
  out-of-scope there.
- `tests/driver_conformance.py`, `tests/test_driver_conformance_ondevice.py`,
  `tests/test_driver_conformance_web.py`, [`android-e2e.yml`](../../.github/workflows/android-e2e.yml).
