**English** · [日本語](BE-0279-crossbackend-e2e-required-gate-ja.md)

# BE-0279 — Align required E2E checks across every backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0279](BE-0279-crossbackend-e2e-required-gate.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0279") |
| Implementing PR | [#1177](https://github.com/bajutsu-e2e/bajutsu/pull/1177) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This BE defines a single policy for which on-device checks gate a merge, and adds the aggregator
jobs that implement the policy. Today only the iOS `E2E` aggregator is a required status check, and
the Android and web lanes are non-required signals. The new capability jobs proposed by the sibling
items in this batch need a defined gate rule, so that each job does not accrete its own status.

## Motivation

The required gate is the iOS `E2E` aggregator (`needs: [smoke, xcuitest, xcuitest-gestures,
conformance]`). The Android lane
([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)) and the web lane
([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) have no aggregator
and sit outside branch protection. A break in the broadest functional lane, Android's fourteen
scenarios, or in the web conformance contract therefore does not block a merge.

The sibling items add network, iOS functional-parity, push, WebView, and text-editing jobs across
all three lanes. Deciding gate membership job by job as jobs land would leave the policy implicit
and inconsistent. A single rule settles it: a check gates when it is both deterministic and
host-independent. A check that depends on the host or on an upstream dependency stays a signal.

The rule is the existing design, written down. The iOS lane already excludes the pixel VRT (the
Simulator renderer varies by Xcode, device, and OS) and the element-tree golden (the `idb_companion`
upstream dependency can drift independently of any Bajutsu change) from its aggregator's `needs:`.
This item generalizes that boundary to Android and web, and applies it to the new jobs.

## Detailed design

This section works at proposal altitude. The units are mutually exclusive and collectively
exhaustive (MECE) around the aggregators and the ruleset.

- **`E2E (android)` aggregator.** A job in `android-e2e.yml` mirrors the iOS `E2E` job. The job
  `needs` the deterministic host-independent jobs (`smoke`, `conformance`) and excludes `golden` and
  `visual`. It runs with `if: always()`, so a path-skip reports as a pass.
- **`E2E (web)` aggregator.** The web twin `needs` `smoke`, the serve-UI dogfood, and `conformance`,
  plus the network job once it lands.
- **Branch-protection ruleset.** Add the new required aggregator check names to the ruleset of
  `main`. The ruleset is out-of-repo administrative state, so this unit is a human step that records
  the exact check names here and is not a repository change.
- **Written policy.** State the gate boundary in the workflow headers and the contributor docs.
  Deterministic and host-independent capability checks are required. Host-specific checks (pixel
  VRT) and upstream-drift checks (`idb_companion` golden) stay non-required signals.
- **Fold in the sibling jobs.** Route each new job from the network, iOS functional-parity, push,
  WebView, and text-editing items into the `needs:` of the correct aggregator.

## Alternatives considered

* **Keep only iOS required (status quo).** Rejected. The required lane would then carry the thinnest
  functional coverage, and a broad Android regression would stay invisible to the gate.
* **Make every job required, including VRT and golden.** Rejected. Pixel baselines are host-specific
  and `idb_companion` drift is an upstream event, so gating on them would redden PRs for reasons
  unrelated to the change. The iOS lane already documents this rationale.
* **One aggregator across all [backends](../../docs/glossary.md#driver-backend-actuator-platform).** Rejected. Per-backend aggregators preserve attribution, so
  a red check names the backend that broke. Per-backend aggregators also match the repository's
  one-concern-per-job structure.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `E2E (android)` aggregator in `android-e2e.yml` — `needs: [changes, smoke, conformance]`, `if: always()`,
  `golden`/`visual` excluded; the lane converts to the iOS trigger shape (every PR + `merge_group`, a
  `changes` job path-gating the KVM jobs via `scripts/e2e_changes.py` with `E2E_LANE=android`).
- [x] `E2E (web)` aggregator in `web-e2e.yml` — `needs: [changes, web-e2e, serve-ui-dogfood, web-conformance]`, same trigger
  conversion (`E2E_LANE=web`).
- [ ] Add the new required check names — `E2E (android)` and `E2E (web)` — to `main`'s
  branch-protection ruleset (human step; out-of-repo administrative state, cannot be a repository
  change).
- [x] Document the gate boundary (deterministic + host-independent → required) in `docs/ci.md` and its
  `docs/ja/ci.md` mirror, and in each workflow's header.
- [ ] Route the sibling items' new jobs into the right aggregator's `needs:` — forward-looking: the
  network / push / WebView / text-editing jobs do not yet exist in the workflows, so each sibling PR
  adds itself to the correct aggregator when it lands (the aggregators list today's jobs).

## References

`.github/workflows/ios-e2e.yml` (the `E2E` aggregator this generalises),
`.github/workflows/android-e2e.yml`, `.github/workflows/web-e2e.yml`,
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md),
[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md).
