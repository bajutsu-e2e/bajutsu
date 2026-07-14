**English** · [日本語](BE-XXXX-e2e-workflow-structural-parity-ja.md)

# BE-XXXX — Structural parity across platform E2E workflows

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-e2e-workflow-structural-parity.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Bajutsu has three per-platform, on-device E2E workflows — [`android-e2e.yml`](../../.github/workflows/android-e2e.yml),
[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml), and [`web-e2e.yml`](../../.github/workflows/web-e2e.yml).
A first step toward parity already landed: the former standalone `e2e.yml` (which carried iOS's
functional jobs and the required `E2E` gate) was merged into `ios-e2e.yml`, so the iOS lane is now
one self-sufficient file with `smoke (idb)`, `xcuitest (codegen)`, `xcuitest (multi-touch)`,
`conformance (idb + xcuitest)`, `visual (idb)`, and the required `E2E` aggregator. Two structural
asymmetries remain across the three files:

- `android-e2e.yml` still runs **one job**, `smoke + golden + visual + fallback (adb)`, that
  bundles a functional scenario run, the element-tree golden, the pixel visual-regression test
  (VRT), and the resident/fallback-channel check together — where `ios-e2e.yml` and `web-e2e.yml`
  each split their work across named jobs.
- iOS has **no per-PR element-tree golden**: `ios-e2e.yml` runs smoke/codegen/gestures/conformance
  /visual, but the idb golden runs only weekly in [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml),
  while `android-e2e.yml` runs its golden on every relevant PR.

The functional heart the three lanes share — "does bajutsu actually drive this platform?" — is the
**smoke** job: a real `bajutsu run` over the showcase scenarios on the real backend, asserting
deterministic pass/fail. This item finishes making that job, and the checks around it, structurally
parallel across all three platforms.

## Motivation

- **A platform's E2E workflow should show, job by job, which capability broke.** `android-e2e.yml`'s
  single job reports one pass/fail for four different checks (smoke, golden, visual, fallback); a
  red check tells a reviewer nothing about which of the four broke without opening the run log.
  [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) already
  established that the checks list, not the YAML, is what gets read first — the same argument
  applies one level down, to job granularity within a workflow, not just workflow names.
  `ios-e2e.yml`'s and `web-e2e.yml`'s multi-job splits already show the target shape; Android is the
  last lane still bundled.
- **Functional coverage should be symmetric where the platforms genuinely support it.** iOS and
  Android both have an element-tree golden (BE-0006) that pins the normalized accessibility tree;
  Android runs it per PR, iOS only weekly. The weekly-only iOS cadence had a real reason —
  decoupling `idb_companion` upstream-drift monitoring from PR activity (`idb-monitor.yml`'s
  header) — but that argument covers the *monitor*, not the *regression check*: nothing stops the
  deterministic, host-independent tree golden from also guarding each PR, the way Android's does.
- **Undocumented asymmetries read as bugs.** That the iOS golden ran only weekly was invisible from
  `ios-e2e.yml` or `android-e2e.yml`; this item's own investigation first read it as an unexplained
  inconsistency rather than a deliberate choice. Writing the per-platform job-set down (unit 3)
  stops the next reader from re-deriving it.

## Detailed design

The work is CI-workflow restructuring only — no product code changes, and nothing here puts an
LLM on the `run`/CI verdict path (prime directive 1 is untouched; this only reorganizes existing
deterministic checks). The iOS-file consolidation the *Introduction* describes already landed
separately; the three units below are what remains.

1. **Split `android-e2e.yml`'s single job into per-concern jobs** — `smoke`, `golden`, `visual` —
   mirroring `ios-e2e.yml`'s and `web-e2e.yml`'s job splits. Each maps to an already-passing
   Android `make` target:
   - `smoke (adb)` → `make -C demos/showcase/android e2e` (Compose over the full scenario set,
     Views over the id-verified subset — the functional heart).
   - `golden (adb)` → `make e2e-golden && make e2e-fallback`: the element-tree golden over the
     resident channel, then re-run with `BAJUTSU_ADB_RESIDENT=0` to exercise the `uiautomator dump`
     fallback and prove both channels yield the same tree (BE-0245). `fallback` stays a step inside
     this job rather than a fourth job because it is the *same* golden re-run on the other channel —
     splitting it out would re-run identical setup for no added attribution.
   - `visual (adb)` → `make e2e-visual` (pixel VRT; non-required, host-specific baseline).

   Each job boots its own AVD. Unlike iOS's metered macOS runners (10x billing), Android runs on
   standard-priced Linux under KVM, so per-job emulator boots are an acceptable cost for the
   attribution win. Setup (uv, JDK, Gradle, KVM, AVD cache) is duplicated per job, matching how
   `ios-e2e.yml` already duplicates its own per-job setup rather than introducing a composite
   action (this item adds no new CI machinery). Android is non-required (not in the branch-
   protection ruleset), so the split needs no aggregator job — three independent jobs, as
   `web-e2e.yml` has.

2. **Add a per-PR `golden (idb)` job to `ios-e2e.yml`**, running `golden.yaml` over idb via the
   `bajutsu-e2e` action (the same scenario/baseline `idb-monitor.yml` runs weekly). It is gated by
   the existing `changes` job but **deliberately excluded from the `E2E` gate's `needs:`**, exactly
   as `visual` is: the tree golden is deterministic and host-independent, but `idb_companion` is an
   upstream dependency whose drift could redden it independently of any Bajutsu change, so a golden
   drift should surface per PR as a signal without blocking merges. This gives iOS golden the same
   effective status as Android's (which runs per PR on a non-required lane). The weekly
   `idb-monitor.yml` golden stays — its purpose is forward-looking (running against the *latest*
   `idb_companion`), which the per-PR job does not replace.

3. **Write down the per-platform E2E job-set convention** — which of smoke / golden / visual /
   conformance / codegen / gestures / fallback each platform's on-device workflow carries, and why
   a platform omits one (e.g. Web has no golden/visual; Android has no conformance yet, unit 4) —
   in [`docs/ai-development.md`](../../docs/ai-development.md) (English and the `docs/ja/` mirror).
   A future backend (Flutter is the planned fourth, per `CLAUDE.md`) then has a written shape to
   match instead of three files to reverse-engineer.

4. **Out of scope, flagged for a follow-up item**: Android has no on-device driver-conformance job
   (`test_driver_conformance_ondevice.py` / `test_driver_conformance_web.py` have no adb
   counterpart) — a real coverage gap, but writing a new conformance suite is test-authoring work,
   not workflow restructuring, so it does not belong in this item's scope.

## Alternatives considered

- **Split Android's golden and fallback into two separate jobs.** Rejected: `fallback` is the same
  `golden.yaml` re-run with the resident channel forced off, so a separate job would duplicate the
  APK build and AVD boot to assert the same tree over a different read path. Keeping it a second
  step in the `golden` job gives the channel-parity coverage without the redundant setup.
- **Make the iOS per-PR golden a required gate member (add it to `E2E`'s `needs:`).** Rejected: an
  `idb_companion` upstream release could then redden the golden and block *all* merges until the
  baseline is re-recorded — the very coupling the weekly `idb-monitor.yml` was created to keep off
  the PR path. Excluding it from `needs:` (like `visual`) keeps the per-PR signal without the
  merge-blocking risk.
- **Consolidate all three platforms' E2E workflows into one parameterized/reusable workflow.**
  Would maximize structural parity in one stroke, but changes the blast radius of any future edit
  from one platform to three, and the required-check branch-protection config would need to move to
  whatever job names the consolidated workflow produces — more risk than the win justifies here.
  Deferred; the per-file job-set convention from unit 3 gets most of the legibility benefit without
  the shared-workflow risk.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Split `android-e2e.yml`'s single job into `smoke` / `golden` (with the `fallback` step) /
      `visual` jobs.
- [ ] Add a per-PR `golden (idb)` job to `ios-e2e.yml`, excluded from the `E2E` gate's `needs:`.
- [ ] Document the per-platform E2E job-set convention in `docs/ai-development.md` (+ ja mirror).

## References

- [`android-e2e.yml`](../../.github/workflows/android-e2e.yml),
  [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml),
  [`web-e2e.yml`](../../.github/workflows/web-e2e.yml),
  [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml)
- [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) — legible
  workflow/job names, the precedent this item extends one level down to job granularity/content.
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
  [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md),
  [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
  — the Android E2E lane's history; the content this item now splits into jobs.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the driver
  conformance suite referenced in unit 5's out-of-scope note.
