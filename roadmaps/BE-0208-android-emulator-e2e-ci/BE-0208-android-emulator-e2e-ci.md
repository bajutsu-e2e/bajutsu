**English** · [日本語](BE-0208-android-emulator-e2e-ci-ja.md)

# BE-0208 — Android on-device e2e in CI (emulator via KVM)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0208](BE-0208-android-emulator-e2e-ci.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0208") |
| Implementing PR | [#851](https://github.com/bajutsu-e2e/bajutsu/pull/851) |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

iOS has an on-device e2e workflow (`.github/workflows/e2e.yml`) and the web backend has its own
(`web-e2e.yml`), but there is no Android e2e lane. The Android backend
([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) was validated once on a local
arm64 emulator (2026-07-07), and nothing in CI keeps that validation from silently regressing. This
item adds an Android emulator e2e workflow — a Linux runner booting an AVD under KVM and driving the
showcase scenarios over `--backend android` — kept off the fast `make check` gate, exactly as the
idb and web e2e lanes are.

## Motivation

The on-device actuation and device-control work for Android (the Android actuation-fidelity and
device-control items authored in this same batch) only manifests against a real device, so the fast
Linux gate cannot cover it. Without a CI emulator lane, those behaviors are validated once by hand
and can regress unnoticed on any later change. iOS and the web both already have an e2e lane; adding
the Android one restores that safety net for the third backend and makes the on-device slices
verifiable in CI rather than only locally. BE-0007's own phasing note already anticipated this:
"the emulator runs on Linux CI via KVM (the `android-emulator-runner` action)".

## Detailed design

The workflow mirrors the existing e2e lanes: its own file, triggered on the relevant paths, not
part of `make check`. It runs no LLM — a deterministic `run` over fixed scenarios — so it stays
within the prime directives.

### Work breakdown (MECE)

1. **The workflow** (`.github/workflows/android-e2e.yml`). A Linux runner using
   `reactivecircus/android-emulator-runner` with KVM, booting an AVD at the API level the local
   validation used (arm64 API 34), gated by path filters like the other e2e workflows.
2. **Build and install the showcase.** Build the Android showcase (the Compose + Views twins) and
   install it on the booted emulator.
3. **Run the passing scenarios.** Drive the core id/tap/type/value scenarios that already pass
   on device over `--backend android`, asserting the deterministic pass/fail.
4. **Visual/golden baseline parity.** Include the Android visual/golden baseline check, the one
   evidence dimension the 2026-07-07 validation left unverified, as part of the lane's coverage.
5. **Grow with the on-device slices.** As the actuation-fidelity and device-control items land,
   extend the scenario set to the flows they fix (`notices`, `gestures`, `controls`,
   location/clipboard), so the lane tracks the growing on-device surface.

## Alternatives considered

- **A self-hosted macOS runner.** Rejected: the Android emulator runs on Linux via KVM, which is
  cheaper and matches BE-0007's phasing choice (Android exercises the lean end on Linux CI). A
  macOS runner is only needed for the idb lane.
- **Fold the emulator run into `make check`.** Rejected: booting an emulator is far too heavy for
  the fast gate, which must run anywhere including Linux without a device. The e2e lane is separate
  by design, as it is for idb and the web.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The workflow (`.github/workflows/android-e2e.yml`) — `android-emulator-runner` + KVM, path-gated.
- [x] Build and install the Android showcase on the booted emulator.
- [x] Run the passing core scenarios over `--backend android`.
- [ ] Visual/golden baseline parity check.
- [ ] Grow the scenario set with the actuation-fidelity and device-control slices as they land.

### Log

- 2026-07-09 — First slice (units 1-3): added `.github/workflows/android-e2e.yml`, a Linux lane that
  boots an AVD under KVM (`reactivecircus/android-emulator-runner`) and runs the core
  id/tap/type/value scenarios (smoke, firstlook, search, components, data_driven, modals, relaunch,
  system) through `--backend android`, driven by a new `e2e` target in
  `demos/showcase/android/Makefile`. The AVD is **x86_64** API 34, not the local validation's arm64:
  KVM acceleration on the x86_64 GitHub Linux runner needs an x86_64 system image (a foreign-arch
  image falls back to slow software emulation) — the API level matches, only the ABI tracks the CI
  host. Documented in `docs/ci.md` (+ ja). Path-gated like `web-e2e.yml`, off the fast `make check`
  gate. Units 4 (visual/golden baseline parity) and 5 (grow with the actuation/device-control
  slices) stay open: the baseline dimensions need a first on-device capture, and the extra scenarios
  need their BE-0007 follow-up slices to land first. Item stays **In progress**.

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
`.github/workflows/e2e.yml`, `.github/workflows/web-e2e.yml`
