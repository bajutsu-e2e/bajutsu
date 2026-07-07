**English** · [日本語](BE-0079-consolidate-demos-on-showcase-ja.md)

# BE-0079 — Consolidate the demo & dogfood apps onto the showcase suite

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0079](BE-0079-consolidate-demos-on-showcase.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0079") |
| Implementing PR | [#371](https://github.com/bajutsu-e2e/bajutsu/pull/371), [#418](https://github.com/bajutsu-e2e/bajutsu/pull/418), [#438](https://github.com/bajutsu-e2e/bajutsu/pull/438) |
| Topic | Dogfood fixtures (demo apps) |
| Related | [BE-0107 — reach showcase tabs by navigation](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

[BE-0045](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) shipped
the **showcase** suite — the same app in UIKit and SwiftUI, each in an accessibility-on / -off
variant (four products from two codebases) — as Bajutsu's next-generation dogfood target. But it
deliberately scoped the migration *out*: its own design note says the older `sample` fixture
"stays until the showcase covers its on-device CI and Web UI tours; superseding it is a follow-up,
not part of this item."

This item is that follow-up. It makes the showcase the **single** iOS fixture: it brings the
showcase to feature parity with the three legacy apps, re-points every demo and on-device CI job at
it, and then **retires** `demo` ([`demos/app`](../../demos/app)), `sample`
([`demos/features/app`](../../demos/features/app)), and `sample2`
([`demos/record/app`](../../demos/record/app)). The web fixture
([`demos/web`](../../demos/web), the Playwright backend) is a different platform and out of
scope — it stays.

## Motivation

**1. Four iOS apps fragment the dogfood story and multiply the upkeep.** Today the demos and the
on-device verification spread across four codebases:

| Fixture | Location | Bundle id | Used by |
|---|---|---|---|
| `demo` | [`demos/app`](../../demos/app) | `com.bajutsu.demo` | the `tour` demo (and top-level `make -C demos features`) |
| `sample` | [`demos/features/app`](../../demos/features/app) | `com.bajutsu.sample` | the `webui` tour, **and the on-device CI** ([`e2e.yml`](../../.github/workflows/e2e.yml): `smoke (idb)` + `xcuitest (codegen)`) |
| `sample2` | [`demos/record/app`](../../demos/record/app) | `com.bajutsu.sample2` | the `record` demo |
| `showcase` (4 products) | [`demos/showcase`](../../demos/showcase) | four `targets.<name>` | `run` / `doctor` / `record`, and the future `crawl` |

Each is a separate Xcode project to keep building, keep accessible, and keep aligned with DESIGN
and the conventions; every new feature demo must pick one; and a contributor has to learn "which
app proves what." A single fixture removes that tax.

**2. The showcase is strictly the richer successor, and the docs already say so.** It packs the
full interaction surface (five tabs, navigation pushes, all four modal styles, text entry, async
loading, live + mockable networking, an OS-alert screen) *plus* the two axes no single-variant app
can show — the **toolkit** axis (UIKit vs SwiftUI element-tree differences) and the
**accessibility** axis (`-a11y` ↔ `-noax`, the controlled experiment for selector stability,
[DESIGN §5](../../DESIGN.md)). The showcase README and `SPEC.md` already describe it as
superseding `sample`. Meanwhile [DESIGN §1.1](../../DESIGN.md) still names `demos/features/app`
(`com.bajutsu.sample`) as "the first dogfood target" — a statement this item makes true again by
pointing it at the showcase.

**3. One fixture is one contract to practise against.** Because per-app differences live entirely
in config ([DESIGN §8](../../DESIGN.md)), a single suite gives every future capability —
visual-regression baselines, data-driven runs, the crawl screen-map, `doctor`'s whole-app coverage
— one ready, representative subject instead of a throwaway app each time (BE-0045 motivation #4,
now realised by removing the alternatives).

**4. It respects every prime directive** ([CLAUDE.md](../../CLAUDE.md)). The change is purely
test *subjects* + config + scenarios + CI wiring + docs: it adds **no** LLM call to any gate, and
keeps the tool / drivers / runner unchanged — the showcase is onboarded entirely through
`targets.<name>` entries, exactly as [DESIGN §7.1](../../DESIGN.md) prescribes.

## Detailed design

**Goal state.** [`demos/showcase`](../../demos/showcase) is the only iOS fixture. The three
legacy app directories are gone, with their configs, scenarios, harnesses, and READMEs, and every
reference to them across the repo is updated. [`demos/web`](../../demos/web) (the Playwright
fixture) and [`demos/serve-ui`](../../demos/serve-ui) (the web-UI dogfood,
[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md)) are a different
platform / subject and are **not** touched.

### A. What the showcase already covers (the honest baseline)

The showcase is already close, so the work is *filling specific gaps*, not rebuilding. Per
[`SPEC.md` §5](../../demos/showcase/SPEC.md) it exposes today: five tabs with push navigation;
all four modal styles (detented sheet, full-screen cover, action sheet, transient toast); text
entry (`log.note`, `search.field`), a stepper (`log.count`) and toggles (`log.intense`,
`horse.favorite` with a mirrored `selected` state); async loading (the Stable catalog); a
scroll-to-element long list (Notices); live + mockable networking through the **same** BajutsuKit
integration as `sample`, including a deliberate secret header/body for redaction; and the OS-alert
screen (Permissions). Its `scenarios/` already drive tabs, navigation, modals, search, the network
mock, notices, permission, and a smoke path.

### B. What gets implemented (the gaps to close before any deletion)

1. **codegen → XCUITest target.** `sample` ships a `BajutsuSampleUITests` target + a `UITests`
   scheme, and CI's `xcuitest` job runs `make -C demos/features ui-test` to generate
   `ComponentsUITests.swift` from `components.yaml` and run it under `xcodebuild test`. The showcase
   has none of this. Add a UITests target + `UITests` scheme to one showcase codebase
   (`{swiftui,uikit}/project.yml`), a scenario that drives a representative slice, and a `ui-test`
   target to [`demos/showcase/Makefile`](../../demos/showcase/Makefile).
2. **Visual-regression scenario + baselines.** `sample` has `visual.yaml` and a `baselines/`
   workflow (`make vrt` / `vrt-approve`). Add `scenarios/visual.yaml` and the `vrt` / `vrt-approve`
   Make targets so the visual-regression tour has a subject.
3. **The few genuinely-missing interaction targets** — audited against `sample`'s screens, added
   inside the existing tabs and gated by the same `ACCESSIBLE` compile flag (SPEC §8), no forked
   source, ids kept within the namespace discipline (SPEC §9):
   - **Gestures:** explicit long-press and double-tap targets (the showcase has `swipe` via the
     Notices scroll, but no dedicated long-press / double-tap element; `sample` exercises both and
     ships `GesturesUITests.swift`).
   - **Device / system state:** a small surface that *mirrors* the result of the device steps
     (`background`, `clearClipboard`, `clearKeychain`, `overrideStatusBar`) into a readable value
     so a scenario can assert it — what `sample`'s `SystemView` does.
   - **Controls:** a slider / segmented control if the controls surface needs rounding out beyond
     the existing stepper + toggles.
   - **Reusable setup:** a shared component prelude (today demonstrated by `demo`'s
     `_components/login.yaml`) re-expressed over a showcase flow — e.g. a "navigate + seed" prelude
     rather than a login, since the showcase is deliberately a no-auth app.
   [`SPEC.md`](../../demos/showcase/SPEC.md) (§5 / §9) is updated alongside any new id.
4. **The full evidence tour.** Port [`WEBUI.md`](../../demos/features/WEBUI.md)'s walk through
   *every* evidence type — screenshots, video, device logs, network (observed + mocked), visual
   regression, system-alert handling — onto the showcase, and make sure the showcase `scenarios/` +
   `capturePolicy` actually fire each one (adding the `evidence` / `network` / `relaunch` /
   `controls` / `gestures` / `system` / `text` / `async` scenario equivalents where the surface now
   exists; `sample`-only screens such as `settings`/`reindex` are re-expressed over a showcase flow,
   e.g. `stable.refresh` or `log.submit`).
5. **A focused first-look slice.** `demo` / `sample2` are deliberately minimal
   (onboarding → login → counter) so `tour` and `record` read cleanly at a glance. The showcase has
   no login/counter, so that minimal *narrative* is preserved as a **focused scenario / goal** over
   an existing showcase flow (e.g. open a horse and toggle `horse.favorite`, or step `log.count` and
   assert the mirrored value — a clean "modify it and watch the assertion break" target) — the same
   run → modify → triage and record → self-heal lifecycle, on a slice of the one app rather than a
   second binary. (`record` already targets the showcase via `make -C demos/showcase record` +
   `record/goals.txt`, so only the top-level menu entry moves.)

### C. What gets re-pointed (same behaviour, new subject)

- **On-device CI** — [`e2e.yml`](../../.github/workflows/e2e.yml): point `smoke (idb)` and
  `xcuitest (codegen)` at the showcase (build → install → run), and update the `changes` path-gate
  (`demos/features/app/`, `demos/features/demo.config.yaml` → `demos/showcase/…`), the DerivedData
  cache path + `hashFiles` key, the install path, and the [`bajutsu-e2e`](../../.github/actions)
  action inputs (`scenarios` / `target` / `config`). **Hazard:** these jobs are *not* in the local
  `make check` gate and `.github/` is missed by a default ripgrep sweep — the change must
  explicitly cover `e2e.yml` and the composite action.
- **Demo menu** — re-point the top-level [`demos/Makefile`](../../demos/Makefile)
  (`tour` / `features` / `offline` / `webui` / `record`, `app-build`) and
  [`demos/demo.config.yaml`](../../demos/demo.config.yaml) at the showcase; retarget or fold in
  the harness scripts that hard-code the legacy apps
  ([`demos/tour/demo.sh`](../../demos/tour/demo.sh),
  [`demos/tour/tour.py`](../../demos/tour/tour.py)'s bundle id,
  [`demos/record/demo.sh`](../../demos/record/demo.sh)); rewrite
  [`demos/README.md`](../../demos/README.md) (+ `.ja`) and each demo's README so the whole
  `make -C demos …` menu drives the one app.
- **Docs (bilingual — update each EN file and its `docs/ja` mirror).** `DESIGN.md` §1.1 (the "first
  dogfood target" sentence) and the `bajutsusample` examples in §6.1 / §8; the "Validated on a real
  Simulator" note in [`architecture.md`](../../docs/architecture.md);
  [`docs/sample-app.md`](../../docs/sample-app.md) → a showcase page; the `sample` references in
  `docs/getting-started.md`, `docs/cli.md`, `docs/configuration.md`, `docs/evidence.md`, and the
  `docs/README.md` index; the config example in the root `README.md` / `README.ja.md`; the root
  [`Makefile`](../../Makefile) help lines; and [`.gitignore`](../../.gitignore) (drop the three
  apps' `build/` + `*.xcodeproj/` lines). Finally remove the "supersedes `sample` / kept until …"
  caveats from the showcase `README` / `SPEC` once they are no longer true.

### D. What gets deleted (the retirement)

Only after B/C land and the on-device path is green on the showcase:

- [`demos/app/`](../../demos/app) — the `demo` app (`BajutsuDemo`: 5 Swift files + `Info.plist` +
  `project.yml`), its `scenarios/` (`counter.yaml`, `features.yaml`, `_components/login.yaml`), and
  READMEs.
- [`demos/features/app/`](../../demos/features/app) — the `sample` app (`BajutsuSample`, ~15
  Swift files), `BajutsuSampleUITests/`, all 21 `scenarios/*.yaml` + `baselines/`, `project.yml`,
  README — once their coverage is reproduced on the showcase (B).
- [`demos/record/app/`](../../demos/record/app) — the `sample2` app (`BajutsuSample`,
  `project.yml`, README).
- The now-orphaned per-app configs and harnesses: `demos/demo.config.yaml`,
  `demos/features/demo.config.yaml`, `demos/record/demo.config.yaml`, and whatever of the
  `demos/tour/`, `demos/record/`, `demos/features/` top-level scripts/scenarios the showcase wiring
  replaces (the exact disposition — retarget vs delete vs move into `demos/showcase/scenarios/` — is
  settled per phase; the firm part is that the three **app** directories are removed).

### E. Phasing (one item, phased PRs)

To keep each PR reviewable and CI green throughout — the migration crosses `.github/` and the
on-device path the local gate cannot run:

1. **Parity.** Implement B on the showcase (UITests/codegen target, visual regression, the missing
   interaction targets, the evidence tour) and add showcase CI jobs *alongside* the existing
   `sample` ones. Nothing is deleted; the on-device path is green on both.
2. **Switch.** Re-point C (`tour` / `features` / `webui` / `record`, the top-level Makefile/config,
   the docs) at the showcase; flip the CI jobs to the showcase and drop the `sample` ones.
3. **Retire.** Execute D — delete the three legacy apps + their configs/scenarios/READMEs; update
   DESIGN §1.1 and `architecture.md`; remove the showcase "supersedes" caveats.

## Alternatives considered

- **Keep the minimal `demo` / `sample2` apps for the first-look demos.** Rejected: the goal is one
  iOS fixture. The minimal onboarding → login → counter story survives as a focused scenario on the
  showcase, so first-look clarity is kept without a second (third) binary to maintain.
- **One big-bang PR.** Rejected: it would cross `.github/` + the on-device jobs (which the local
  `make check` gate cannot run) *and* delete three apps at once. Phasing keeps each PR reviewable
  and the on-device path continuously green.
- **Drop the redundant `sample` scenarios rather than reach full parity.** Rejected: full parity
  preserves the evidence and interaction coverage the `webui` tour and the codegen CI job depend on;
  trimming would quietly weaken the on-device regression net.
- **Fold this into BE-0045.** Rejected: BE-0045 is shipped and explicitly scoped the migration out,
  and BE IDs are permanent — the migration is a distinct, trackable piece of work.

## Progress

- [x] codegen → XCUITest and visual-regression paths on the showcase — a `UITests` target (`demos/showcase/swiftui/UITests/`) and the VRT scenario/baselines ([#371](https://github.com/bajutsu-e2e/bajutsu/pull/371)).
- [x] On-device CI on the showcase — `e2e.yml` runs showcase smoke + xcuitest jobs alongside the sample ([#371](https://github.com/bajutsu-e2e/bajutsu/pull/371)).
- [x] Remaining parity (E.1) — added a button-backed segmented control (`log.segment.*`) and an in-app pasteboard round-trip (`sys.*`) to both toolkits, plus the evidence-tour and first-look scenarios (`controls` / `system` / `network_live` / `evidence` / `relaunch` / `firstlook`); all pass on `run` against `showcase-swiftui` **and** `showcase-uikit`. Gestures already shipped in [#371](https://github.com/bajutsu-e2e/bajutsu/pull/371); the external-clipboard and background-counter mirrors were dropped as non-deterministic without AI (iOS's paste-permission prompt; `simctl ui home` is not a valid step on the CI toolchain).
- [x] Switch (E.2) — the demo menu (`demos/Makefile`, `demos/demo.config.yaml`) and on-device CI already pointed at the showcase; ported the BE-0006 goldens to the showcase (`scenarios/golden.yaml` + recorded `goldens/`) and re-pointed `idb-monitor.yml`, and ported the scenario-engine feature demos with no showcase equivalent (`data_driven.yaml`, `device.yaml`, the FakeDriver `run_demo.py` / `run_tree_report.py`, and the offline-record `generate_from_nl.py`).
- [x] Retire (E.3) — deleted the legacy `demos/app/`, `demos/features/app/`, `demos/record/app/` and the orphaned configs / harnesses / redundant scenarios (`demos/features/`, `demos/record/` top-level), replaced `tests/test_sample_fixtures.py` with the showcase equivalent, and swept the retirement through the bilingual docs (DESIGN, docs/*, README, CONTRIBUTING, CLAUDE, demos/README) — the sample-app page became `docs/showcase.md`.
- [x] Partial "no launch-time shortcut to a screen/state" — dropped `SHOWCASE_SEED` (the catalog is fixed) and the deeplink detail-push (a detail is reached only by tapping its row), both toolkits. Kept `SHOWCASE_TAB`: idb cannot tap a native tab bar, so switching tabs by tapping needs the XCUITest backend — deferred to [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md) (reach showcase tabs by navigation, retiring `SHOWCASE_TAB`), which depends on BE-0019 maturing.

## References

- [BE-0045 — Dogfood showcase apps](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) — the suite this completes (and whose deferred "Migration" note this item discharges)
- [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — the web-side dogfood counterpart
- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the showcase is its first real target
- [`demos/showcase/SPEC.md`](../../demos/showcase/SPEC.md) — the screen-by-screen contract · [`demos/README.md`](../../demos/README.md) — the demo menu
- [`.github/workflows/e2e.yml`](../../.github/workflows/e2e.yml) — the on-device CI to re-point
- [DESIGN §1.1 / §5 / §7.1 / §8](../../DESIGN.md) — first dogfood target, stability ladder, per-target onboarding, config · [architecture.md](../../docs/architecture.md) — implementation status
