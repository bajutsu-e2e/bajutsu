**English** · [日本語](BE-0208-android-emulator-e2e-ci-ja.md)

# BE-0208 — Android on-device e2e in CI (emulator via KVM)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0208](BE-0208-android-emulator-e2e-ci.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0208") |
| Implementing PR | [#851](https://github.com/bajutsu-e2e/bajutsu/pull/851), [#880](https://github.com/bajutsu-e2e/bajutsu/pull/880), [#899](https://github.com/bajutsu-e2e/bajutsu/pull/899), [#901](https://github.com/bajutsu-e2e/bajutsu/pull/901), [#906](https://github.com/bajutsu-e2e/bajutsu/pull/906) |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md) |
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
- [x] Visual/golden baseline parity check — **golden** element-tree dimension (Compose Stable catalog).
- [ ] Visual/golden baseline parity check — **visual** screenshot dimension (deferred; host-sensitive baseline needs a CI capture).
- [ ] Grow the scenario set with the actuation-fidelity and device-control slices as they land.

### Log

- 2026-07-09 — First slice (units 1-3): added `.github/workflows/android-e2e.yml`, a Linux lane that
  boots an AVD under KVM (`reactivecircus/android-emulator-runner`) and runs the core
  id/tap/type/value scenarios through `--backend android`, driven by a new `e2e` target in
  `demos/showcase/android/Makefile`. The AVD is **x86_64** API 34, not the local validation's arm64:
  KVM acceleration on the x86_64 GitHub Linux runner needs an x86_64 system image (a foreign-arch
  image falls back to slow software emulation) — the API level matches, only the ABI tracks the CI
  host. Documented in `docs/ci.md` (+ ja). Path-gated like `web-e2e.yml`, off the fast `make check`
  gate. The first CI run confirmed the emulator boots and drives the app: `smoke`, `firstlook`, and
  `search` passed, but `components` timed out waiting 5s for a sheet to present. The CI emulator
  renders in software (swiftshader), so it presents a modal too slowly for the 5s sheet-open waits
  that pass on the local hardware-accelerated arm64 device; `components` and `modals` (the two
  sheet/cover flows) are therefore held out of the lane's initial set (`smoke`, `firstlook`,
  `search`, `data_driven`, `relaunch`, `system`) and rejoin once their on-device timing is tuned
  (folded into unit 5). Units 4 (visual/golden baseline parity) and 5 (grow the scenario set) stay
  open: the baseline dimensions need a first on-device capture, and the extra scenarios need their
  BE-0007 follow-up slices (and the modal-timing tuning above) to land first. Item stays **In
  progress**.
- 2026-07-10 — Unit 5 (Stable-tab slice): a concurrent change, BE-0107, retired the `SHOWCASE_TAB`
  launch shortcut, so the shared scenarios now reach a tab by tapping the native tab bar. adb cannot
  drive the tab bar (only the XCUITest backend can), so the non-Stable-tab scenarios — `search`,
  `data_driven`, `relaunch`, `system`, and the Log/Notices flows `components`, `modals`, `gestures`,
  `controls`, `notices` — left the adb lane (it shrank to `smoke`, `firstlook`) and now wait on adb
  tab-bar navigation, a BE-0007 driver follow-up. This supersedes the earlier plan to rejoin
  `components` / `modals` by modal-timing tuning alone: reaching the Log tab, not modal latency, is
  now the blocker. Within that constraint the lane grows by adding `navigation`
  (`demos/showcase/android/Makefile` `E2E_SCENARIOS`): it never leaves the Stable tab the app
  launches on — tap a catalog row to push Horse Detail, toggle the favorite, then pop back via the
  cross-backend `back` step (Android system key) — so it needs no tab bar and adds detail-screen
  assertions and back-navigation to the lane's coverage. Verified with the Python gate (`make
  check`); the lane runs in CI (no local emulator). The rest of unit 5 (the tab-dependent scenarios)
  is blocked on adb tab-bar navigation, and unit 4 (visual/golden parity) stays open. Item stays **In
  progress**.
- 2026-07-10 — Unit 4 (golden dimension): added the on-device golden element-tree check to the lane.
  A new scenario `demos/showcase/scenarios/golden/golden_android.yaml` pins the Compose Stable
  catalog's normalized tree (rows, refresh button, mirrored status) against a recorded baseline
  `demos/showcase/scenarios/golden/goldens/lists_android.json` — a backend-specific baseline, the
  adb twin of idb's `lists.json` and XCUITest's `controls.json` (each backend renders a distinct
  accessibility tree; the adb tree traits are `view` / `textView`, not idb's `button` /
  `staticText`). It runs on the Stable tab (the launch tab), so it needs no tab-bar navigation, and
  is wired as a separate `e2e-golden` target in `demos/showcase/android/Makefile`, run in the same
  emulator session as `e2e` in `android-e2e.yml`. The baseline was recorded on a **local arm64**
  emulator (API 34, `google_apis`); it passes on the CI **x86_64** emulator because the golden
  comparison is field-level — identity / label / traits exact, frame only sanity-checked
  (`bajutsu/golden.py`) — and identity / label / traits are stable across the ABI, so only the
  density-scaled frame differs and it is tolerated. The **visual** (screenshot pixel) dimension of
  unit 4 is deliberately deferred: a pixel baseline is host-sensitive (local arm64 vs CI x86_64
  software rendering diverge per-pixel), so it needs a CI-captured baseline — left to a later slice.
  Item stays **In progress** (visual dimension of unit 4 and the tab-dependent rest of unit 5 remain).
- 2026-07-10 — Unit 5 (tab-dependent scenarios): [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md)
  taught the adb driver to drive the native tab bar (a Compose `NavigationBarItem` resolves the
  shared `{ label, traits: [button] }` selector), unblocking this unit. With the tab bar drivable,
  `search`, `data_driven`, `relaunch`, and `system` rejoined `E2E_SCENARIOS`, all verified on a local
  arm64 emulator. The remaining held-out scenarios stay out for reasons unrelated to the tab bar:
  `components` / `modals` pass locally but their 5s sheet-open waits risk the CI x86_64 software
  renderer, and `gestures` (multi-touch, BE-0210) / `controls` (segmented-control value) / `notices`
  (deep scroll) need their own BE-0007 follow-up slices. The **visual** dimension of unit 4 still
  needs a CI-captured baseline. Item stays **In progress**.
- 2026-07-11 — Unit 5 (sheet/cover flows): `components` and `modals` rejoin `E2E_SCENARIOS`. They
  pass on the local arm64 emulator, but their shared 5s sheet-open waits risked timing out on the CI
  x86_64 software renderer, which draws a modal more slowly. Rather than retune the shared scenarios
  per backend (their `timeout: 5` is the same on every backend), the Android lane raises the wait
  *ceiling*: `bajutsu/orchestrator/waits.py` now honours `BAJUTSU_MIN_WAIT_TIMEOUT` as a floor under
  each wait's own timeout, and `demos/showcase/android/Makefile`'s `e2e` target exports it (default
  15s). A condition wait returns the instant it is satisfied, so the larger ceiling is a safe upper
  bound, not a fixed delay (prime directive 2 holds — still a condition wait, never a fixed sleep),
  and no other backend is affected. Documented in `docs/ci.md` (+ ja). The remaining held-out
  scenarios stay out for reasons unrelated to timing: `gestures` (multi-touch, BE-0210), `controls`
  (segmented-control value), `notices` (deep scroll) — each a BE-0007 follow-up. The **visual**
  dimension of unit 4 still needs a CI-captured baseline. Item stays **In progress**.

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
`.github/workflows/e2e.yml`, `.github/workflows/web-e2e.yml`
