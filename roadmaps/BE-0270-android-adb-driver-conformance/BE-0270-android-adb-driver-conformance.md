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
is one backend-agnostic contract — an ambiguous selector fails rather than acting on the first
match, a zero-match selector fails rather than reporting success, `capabilities()` matches observed
behavior, and `wait_for` is a single-shot check the shared `wait_until` loop turns into a condition
wait. That contract (`tests/driver_conformance.py`) runs today against three drivers: `FakeDriver`
on the fast Linux gate, Playwright in web CI (`tests/test_driver_conformance_web.py`), and the two
iOS backends — idb and XCUITest — on-device (`tests/test_driver_conformance_ondevice.py`, driven by
`ios-e2e.yml`'s `conformance (idb + xcuitest)` job).

The **adb backend (Android, BE-0007) is the one shipped driver the contract never runs against.**
Android's on-device lane exercises functional smoke, the element-tree golden, and the pixel VRT, but
no job asserts that the adb driver honors the determinism-core invariants on a real device the way
idb, XCUITest, and Playwright are each proven to. This item closes that gap.

## Motivation

- **The contract is only as strong as its coverage.** BE-0114's whole point is that the
  determinism-core invariants hold *identically* on every real actuator, not just on `FakeDriver`.
  An unproven backend is exactly where a divergence hides — e.g. an ambiguous adb selector that taps
  the first match instead of failing (a prime-directive-2 violation) would pass every existing
  Android job, because smoke/golden/visual all use unambiguous selectors by construction.
- **adb resolves selectors differently from the other backends.** The adb driver reads over two
  channels (the resident UI Automator server, BE-0245, and the `uiautomator dump` fallback) and
  matches ids in two forms (dotted and underscore, BE-0221). Those are adb-specific paths the iOS
  and web conformance jobs cannot cover; the contract's ambiguous- and zero-match cases are where
  they most need pinning.
- **Parity was explicitly deferred, not dismissed.** The in-flight platform-E2E-parity item (the
  `e2e-workflow-structural-parity` proposal) proposes splitting the Android lane into per-concern
  jobs and notes the missing `conformance (adb)` job as a real coverage hole, out of scope there
  because it is test-authoring work, not workflow restructuring. This item is that follow-up.

## Detailed design

This item ships product code (an app-side conformance mode + a test harness), so it runs under the
prime directives: the suite is deterministic (no LLM anywhere near the verdict), reseeds screens by
a condition wait rather than a fixed `sleep`, and any app-side hook lives behind the same uniform
opt-in the showcase already uses — no per-app branching in the tool. The work is MECE:

1. **An Android conformance mode in the showcase app**, mirroring iOS's `ConformanceView`. A screen
   that renders exactly the identifier set named in a spec channel (each id at its requested
   multiplicity, so the ambiguous "two `dup`s" case is real), plus an always-present readiness
   marker so readiness is a positive check, not an inference from an empty tree. It must cover both
   Android UI toolkits the lane already tests — Compose and Views (BE-0221) — since their id forms
   differ, or be explicitly scoped to Compose with the reason recorded.
2. **A reseed channel over adb**, the analogue of iOS writing `conformance-spec.txt` into the app's
   Documents dir. The natural adb form is `adb push` (or an intent/extra) of the spec into the app's
   files dir, which the conformance screen polls; `with_screen` writes the spec, then waits until the
   driver's own `query()` reflects the new screen (condition-backed, no sleep). Decide push-vs-intent
   at implementation time based on which the emulator handles most reliably.
3. **An adb conformance harness + pytest module** running the *same* `tests/driver_conformance.py`
   contract — either a new `test_driver_conformance_ondevice_android.py` or a parametrization of the
   existing on-device module. It carries the `ondevice` marker (deselected by the gate's default
   `-m 'not web and not ondevice'`) and a module-level skip when its serial env var
   (e.g. `BAJUTSU_CONFORMANCE_SERIAL`) is unset, so the fast Linux gate never picks it up — the same
   shape the idb/XCUITest and web modules use.
4. **A `conformance (adb)` job in `android-e2e.yml`** (Linux+KVM), wiring the harness to a booted
   AVD: build the conformance-capable APK(s), boot, run the suite serially against the one emulator.
   It is non-required, like the rest of the Android lane, and joins the per-concern job set the
   parity item proposes (`smoke` / `golden` / `visual` / now `conformance`).
5. **Honest `capabilities()` for the capability-matching tests.** The contract asserts
   `capabilities()` is a promise: a `MULTI_TOUCH` / `SELECT_OPTION` backend must actually perform the
   action, and one lacking the capability must raise `UnsupportedAction` rather than silently no-op.
   The adb driver's declared capabilities must match its real behavior for these cases to pass; part
   of this work is confirming (and, if needed, correcting) that declaration.

## Alternatives considered

- **Assert the invariants inside the existing Android smoke scenarios instead of a conformance
  job.** Rejected: smoke scenarios drive the real app with unambiguous selectors, so they cannot
  exercise the ambiguous- and zero-match cases without contorting the fixture. The conformance
  contract needs an app screen it can reseed to arbitrary identifier sets — a purpose-built
  conformance mode — which is exactly what the iOS suite already does.
- **Reuse the element-tree golden as the conformance signal.** Rejected: the golden pins a *tree
  shape*, not *selector-resolution semantics*. A driver could match the golden yet still tap the
  first of two ambiguous matches; only the contract's own assertions catch that.
- **Only cover Compose, skip the Views toolkit.** A reasonable narrowing given the two toolkits
  share the driver path, but it leaves the underscore-id form (BE-0221) unproven by conformance.
  Left as an implementation-time decision (unit 1), to be recorded either way rather than silently
  dropped.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add an Android conformance mode to the showcase app (Compose; Views per unit 1's decision).
- [x] Add the adb reseed channel (spec push/intent + condition-backed `with_screen`).
- [x] Add the adb conformance harness + `ondevice`-marked pytest module over the shared contract.
- [x] Add the `conformance (adb)` job to `android-e2e.yml`.
- [x] Confirm the adb driver's `capabilities()` matches behavior for the capability tests.

**Unit 1 decision — Compose only, Views out (recorded per *Alternatives considered*).** The contract
seeds plain identifiers (`dup` / `ok` / `a` / `b` / `Log` / `g` / `sel` / `s`), none carrying the
`.`/`_` the two toolkits' id forms (BE-0221) differ over, so a Views screen would resolve the
identical ids through the identical `_strip_pkg` path — no added conformance coverage. And a
spec-driven arbitrary-id screen is only naturally expressible in Compose: its `testTag` accepts any
runtime string (surfaced as a `resource-id` via `testTagsAsResourceId`), while a Views `resource-id`
must be a compile-time `R` entry. Compose is the one toolkit that can render the contract's screens
at all.

**Unit 2 decision — intent over file push.** The reseed re-launches the `singleTask` activity with a
new `SHOWCASE_CONFORMANCE` intent extra, delivered via `onNewIntent` (the proven deep-link path),
rather than pushing a spec file (iOS's channel): `adb push` cannot reach the app's sandbox, and the
intent reuses the `launchEnv`→intent-extras convention (BE-0007) with no new app-side polling. The
empty (zero-match) screen is encoded with a leading-`,` sentinel so the value is never the empty
string, which adb's `am start --es KEY ""` would drop (the trailing-empty-arg quirk the clipboard
channel also documents).

**Unit 5 finding — no `capabilities()` correction needed.** The adb driver's declared set
(`QUERY` / `ELEMENTS` / `MULTI_TOUCH`, `SELECT_OPTION` absent) already matches behavior for the
capability tests on the rooted `google_apis` emulator: `pinch`/`rotate` actuate over the rooted
`sendevent` sweep (BE-0232) without raising, and `select_option` raises `UnsupportedAction`. The
`conformance (adb)` job runs `adb root` (as the smoke lane's `gestures_multitouch` already does) to
hold the `MULTI_TOUCH` precondition.

### Log

- Implemented in [#1119](https://github.com/bajutsu-e2e/bajutsu/pull/1119): Compose
  `ConformanceScreen` + `AppModel.conformanceIds`/`applyConformance` + `MainActivity`/`RootScreen`
  wiring; `tests/test_driver_conformance_ondevice_android.py` over the shared contract with the
  `am start` reseed channel; the `conformance (adb)` job in `android-e2e.yml` and the
  `e2e-conformance` Makefile target.

## References

- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the
  backend-agnostic conformance contract this item extends to the adb backend.
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) — the adb backend under test.
- [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)
  — the dotted/underscore id forms the conformance screen must cover.
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
  — the resident vs `uiautomator dump` read channels the adb driver resolves selectors over.
- The `e2e-workflow-structural-parity` proposal (BE id allocated on its own merge) — proposes
  splitting the Android lane into per-concern jobs and flags this `conformance (adb)` gap as
  out-of-scope there.
- `tests/driver_conformance.py`, `tests/test_driver_conformance_ondevice.py`,
  `tests/test_driver_conformance_web.py`, [`android-e2e.yml`](../../.github/workflows/android-e2e.yml).
