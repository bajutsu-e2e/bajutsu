**English** · [日本語](BE-0271-e2e-workflow-structural-parity-ja.md)

# BE-0271 — Structural parity across platform E2E workflows

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0271](BE-0271-e2e-workflow-structural-parity.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0271") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Bajutsu has three per-platform, on-device end-to-end (E2E) workflows:
[`android-e2e.yml`](../../.github/workflows/android-e2e.yml),
[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml), and
[`web-e2e.yml`](../../.github/workflows/web-e2e.yml).

A first step toward parity has already landed. The former standalone `e2e.yml` carried iOS's
functional jobs and the required `E2E` gate. It was merged into `ios-e2e.yml`. The iOS lane is now
one self-sufficient file with these jobs:

- `smoke (idb)`
- `xcuitest (codegen)`
- `xcuitest (multi-touch)`
- `conformance (idb + xcuitest)`
- `visual (idb)`
- the required `E2E` aggregator

Two structural asymmetries remain across the three files:

- `android-e2e.yml` still runs **one job**, `smoke + golden + visual + fallback (adb)`. That job
  bundles four checks: a functional scenario run, the element-tree golden, the pixel
  visual-regression test (VRT), and the resident/fallback-channel check. `ios-e2e.yml` and
  `web-e2e.yml` each split their work across named jobs.
- iOS has **no per-PR element-tree golden**. `ios-e2e.yml` runs smoke, codegen, gestures,
  conformance, and visual. The idb golden runs weekly in
  [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml). `android-e2e.yml` runs its golden
  on every relevant PR.

The three lanes share one functional core. It answers the question "does bajutsu actually drive
this platform?". That core is the **smoke** job: a real `bajutsu run` over the showcase scenarios
on the real backend, with deterministic pass/fail assertions. This item makes that job, and the
checks around it, structurally parallel across all three platforms.

## Motivation

- **A platform's E2E workflow should show, job by job, which capability broke.** The single job
  in `android-e2e.yml` reports one pass/fail for four checks: smoke, golden, visual, and fallback.
  A red check does not tell a reviewer which of the four broke. The reviewer must open the run log.
  [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) established
  that the checks list, not the YAML, is what a reader sees first. The same argument applies one
  level down, to job granularity within a workflow. The multi-job splits in `ios-e2e.yml` and
  `web-e2e.yml` already show the target shape. Android is the last lane that remains bundled.
- **Functional coverage should be symmetric where the platforms genuinely support it.** iOS and
  Android both have an element-tree golden (BE-0006) that pins the normalized accessibility tree.
  Android runs it per PR, and iOS runs it weekly. The weekly iOS cadence had a real reason. It
  decouples monitoring of upstream `idb_companion` drift from PR activity (see the header of
  `idb-monitor.yml`). That reason covers the *monitor*, not the *regression check*. Nothing stops
  the deterministic, host-independent tree golden from guarding each PR, as Android's does.
- **Undocumented asymmetries read as bugs.** The weekly cadence of the iOS golden was invisible
  from `ios-e2e.yml` and `android-e2e.yml`. The investigation behind this item first read the
  cadence as an unexplained inconsistency, not a deliberate choice. Writing the per-platform job
  set down (unit 3) spares the next reader from re-deriving it.

## Detailed design

The work restructures CI workflows and nothing else. No product code changes. Nothing here puts
an LLM on the `run`/CI verdict path, so prime directive 1 is untouched. The work reorganizes
existing deterministic checks. The iOS-file consolidation from the *Introduction* already landed
separately, so three units remain.

1. **Split `android-e2e.yml`'s single job into per-concern jobs.** The jobs are `smoke`, `golden`,
   and `visual`, which mirrors the splits in `ios-e2e.yml` and `web-e2e.yml`. Each job maps to an
   already-passing Android `make` target:
   - `smoke (adb)` runs `make -C demos/showcase/android e2e`. Compose runs over the full scenario
     set, and Views over the id-verified subset. This is the functional core.
   - `golden (adb)` runs `make e2e-golden && make e2e-fallback`. The element-tree golden runs over
     the resident channel first. It then re-runs with `BAJUTSU_ADB_RESIDENT=0` to exercise the
     `uiautomator dump` fallback. That re-run proves that both channels yield the same tree
     (BE-0245). `fallback` stays a step inside this job. It is the *same* golden re-run on the
     other channel. A fourth job would repeat identical setup with no gain in attribution.
   - `visual (adb)` runs `make e2e-visual`, the pixel VRT. This job is non-required and uses a
     host-specific baseline.

   Each job boots its own Android Virtual Device (AVD). iOS runs on metered macOS runners (10x
   billing). Android runs on standard-priced Linux under KVM. Per-job emulator boots are an
   acceptable cost for the gain in attribution.

   The setup (uv, JDK, Gradle, KVM, AVD cache) is duplicated per job. `ios-e2e.yml` already
   duplicates its own per-job setup the same way. The item adds no new CI machinery, such as a
   composite action. Android is non-required, so it is not in the branch-protection ruleset. The
   split needs no aggregator job. It yields three independent jobs, as in `web-e2e.yml`.

2. **Add a per-PR `golden (idb)` job to `ios-e2e.yml`.** The job runs `golden.yaml` over idb
   through the `bajutsu-e2e` action. It uses the same scenario and baseline that `idb-monitor.yml`
   runs weekly. The existing `changes` job gates it. The job is **deliberately excluded from the
   `E2E` gate's `needs:`**, exactly as `visual` is.

   The tree golden is deterministic and host-independent. However, `idb_companion` is an upstream
   dependency. Its drift could turn the golden red independently of any Bajutsu change. A golden
   drift should surface per PR as a signal, without blocking merges. The iOS golden then has the
   same effective status as Android's, which runs per PR on a non-required lane.

   The weekly golden in `idb-monitor.yml` stays. Its purpose is forward-looking, because it runs
   against the *latest* `idb_companion`. The per-PR job does not replace that purpose.

3. **Write down the per-platform E2E job-set convention.** The convention records which of smoke,
   golden, visual, conformance, codegen, gestures, and fallback each platform's on-device workflow
   carries. It also records why a platform omits one. For example, Web has no golden or visual
   job, and Android has no conformance job yet (unit 4). The convention lives in
   [`docs/ai-development.md`](../../docs/ai-development.md), in English and in the `docs/ja/`
   mirror. A future backend (Flutter is the planned fourth, per `CLAUDE.md`) then has a written
   shape to match. It no longer has three files to reverse-engineer.

4. **Out of scope, flagged for a follow-up item.** Android has no on-device driver-conformance
   job. `test_driver_conformance_ondevice.py` and `test_driver_conformance_web.py` have no adb
   counterpart. This is a real coverage gap. Writing a new conformance suite is test-authoring
   work, not workflow restructuring, so it does not belong in this item.

## Alternatives considered

- **Split Android's golden and fallback into two separate jobs.** Rejected. `fallback` is the same
  `golden.yaml` re-run with the resident channel forced off. A separate job would duplicate the APK
  build and AVD boot to assert the same tree over a different read path. A second step in the
  `golden` job gives the channel-parity coverage without the redundant setup.
- **Make the iOS per-PR golden a required gate member, by adding it to `E2E`'s `needs:`.**
  Rejected. An `idb_companion` upstream release could turn the golden red. It could then block
  *all* merges until the baseline is re-recorded. The weekly `idb-monitor.yml` was created to keep
  exactly this coupling off the PR path. Excluding the job from `needs:`, as with `visual`, keeps
  the per-PR signal without the merge-blocking risk.
- **Consolidate the E2E workflows of all three platforms into one parameterized, reusable
  workflow.** This would maximize structural parity in one stroke. It also widens the blast radius
  of any future edit from one platform to three. The required-check branch-protection config would
  need to move to whatever job names the consolidated workflow produces. That is more risk than the
  gain justifies here, so the idea is deferred. The per-file job-set convention from unit 3
  delivers most of the legibility benefit without the shared-workflow risk.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Split `android-e2e.yml`'s single job into `smoke` / `golden` (with the `fallback` step) /
      `visual` jobs.
- [x] Add a per-PR `golden (idb)` job to `ios-e2e.yml`, excluded from the `E2E` gate's `needs:`.
- [x] Document the per-platform E2E job-set convention in `docs/ai-development.md` (+ ja mirror).

### Log

- Proposal + implementation authored together and landed in one PR (BE-creation, so the human
  opens it; `roadmap-id` allocates the real BE id on merge, and the `Implementing PR` row is filled
  then). Splits the Android lane into `smoke`/`golden`/`visual` jobs, adds a per-PR iOS
  `golden (idb)` job excluded from the required `E2E` gate, and documents the per-platform E2E job
  set in `docs/ai-development.md` and its `docs/ja/` mirror.

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
