**English** · [日本語](BE-0006-idb-element-tree-normalization-ja.md)

# BE-0006 — idb element-tree normalization accuracy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0006](BE-0006-idb-element-tree-normalization.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | On-device validation (M1 close-out) |
<!-- /BE-METADATA -->

## Introduction

Continuously confirm on a real device that the tree representation of standard SwiftUI elements (e.g. `.searchable`) does not break.

## Motivation

Selector resolution is the determinism core: a selector matches exactly one element, zero is "not present," and two or more is an ambiguity error. Every one of those decisions is made against the element tree that `IdbDriver.query()` normalizes out of idb's `ui describe-all`. So the correctness of the *whole* run loop is only as good as that normalization. If a standard SwiftUI control does not surface in the tree the way we expect — wrong identifier, missing trait, a container that swallows its child — a scenario written against it either silently can't find the element or, worse, matches a different one.

DESIGN §11 already records two concrete symptoms here. `.searchable` and similar standard SwiftUI elements are called out by name as accuracy risks, and we have observed idb returning a near-empty tree mid-transition (a single element with no identifier) even though the screen has rendered. The second symptom is mitigated in `IdbDriver.query()` by a bounded, gated retry (`_max_seen` / `_READY_MIN`), but that is a defence against a *transient*; it does not tell us whether the *settled* tree for a given SwiftUI control is shaped the way scenarios assume. The retry note itself ends by pointing at "8-second-class pathological cases" that the driver deliberately does not mask. What is missing is a continuous, on-device check that the steady-state representation of the SwiftUI controls real apps use has not drifted — so that a regression in idb, in iOS, or in how a control is built shows up as a failing check rather than as a flaky or wrong scenario in the field.

## Detailed design

The proposal is a maintained on-device fixture that pins the expected normalized tree for the SwiftUI controls scenarios actually depend on, run on the same heavier e2e path that already validates idb (not the fast per-PR gate).

- **A controls catalogue screen in the sample app.** The demo app (`demos/`) hosts a screen that instantiates the standard SwiftUI controls we care about — `.searchable`, `List`, `NavigationStack`, `TextField`, toggles, segmented controls, etc. — each given a stable `accessibilityIdentifier` from the app's id namespace. Because per-app identifiers are an app concern, this fixture exercises the *normalization*, not any one production app; the tool stays app-agnostic.
- **Golden normalized trees.** For each control the expected `Element` shape (the fields the `Selector` semantics rely on: `identifier`, `label`, `traits`, `value`, a sane `frame`) is recorded as a checked-in expectation. The check drives the app to that screen, calls `query()`, and asserts the normalized tree against the golden — a machine-checkable assertion, so it fits the deterministic gate with no LLM in the loop.
- **Settled vs. transient is explicit.** The check waits for the screen via the existing condition-wait path (no fixed sleep), then asserts on the *settled* tree. The `_max_seen`-gated retry continues to absorb mid-transition emptiness; this proposal's golden is about the steady state once that retry has done its job, so the two concerns stay separate and neither masks the other.
- **Drift surfaces as a failing assertion.** When idb, an iOS release, or a control's construction changes the normalized shape, the golden assertion fails on the e2e path with a concrete diff (which field of which control moved), rather than the change leaking out as a field scenario that can no longer find its element. This pairs naturally with BE-0005's version monitoring: that proposal watches *which* idb is installed, this one watches what idb *produces*.

No part of this relaxes selector strictness: ambiguous still fails, missing still fails, and the golden assertions are exact comparisons.

### The controls catalogue screen

The catalogue is not a new app to build — it is the standard-control surface the sample app
(`demos/features/app/BajutsuSample/`) already carries, made the explicit subject of a golden.
`MainTabView` in `RootView.swift` already wires the screens a catalogue needs, each launchable
in isolation through `launchEnv` (`SAMPLE_TAB`), so the check can drive straight to one without
walking the UI:

- **`ControlsView` (`SAMPLE_TAB=controls`)** — the value controls: a `Toggle`
  (`ctrl.toggle`, `.labelsHidden()` so its element is the bare switch), a `Stepper`
  (`ctrl.stepper`), a stepped `Slider` (`ctrl.slider`), a single-select segment built from
  per-segment `Button`s (`ctrl.segment.one` … `ctrl.segment.three`), a `Menu` (`ctrl.menu`,
  whose items are addressed by label from a system popover), and an enabled / permanently
  disabled `Button` pair (`ctrl.button` / `ctrl.buttonDisabled`). Each mirrors its state into
  a sibling `ctrl.<name>.value` result label.
- **`ListsNavView` (`SAMPLE_TAB=lists`)** — the container controls flagged in the Motivation:
  a `NavigationStack`, a `.searchable`-style `TextField` (`lists.search`), a `List`
  (`lists.list`) of `NavigationLink` rows with data-derived ids (`lists.row.<id>`), and an
  `EditButton` (`lists.edit`). This is where "a container that swallows its child" and
  "`.searchable` reports the wrong identifier" would actually show up.

These ids come from the app's own namespace (`idNamespaces` in
[`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml)); the catalogue
exercises how idb *normalizes* a `Toggle` or a `List` into `Element`s, not any one app's choice
of names. If a future control type is worth pinning and no existing screen hosts it, it is
added to the catalogue with a stable `accessibilityIdentifier` — the surface grows, the
mechanism does not change. Keeping the catalogue inside the existing sample app (rather than a
second fixture app) also means the build, boot, and install the e2e jobs already perform cover
it for free.

### Golden format and the assertion

Each control's expectation is one `base.Element` (`bajutsu/drivers/base.py`) — exactly the
shape `query()` produces, so the comparison is value-for-value with no adapter in between. The
golden records the fields the `Selector` semantics in `base.matches` actually read:

- `identifier` — the `id` / `idMatches` selector key; the most load-bearing field, asserted
  exactly. A drift here is precisely the "unfindable element later" failure mode.
- `label` — the `label` / `labelMatches` key; asserted exactly (it is a fixed English string in
  the catalogue, not a localized or runtime value).
- `traits` — the normalized tokens from `idb._traits` (`_norm_type` of the AX type, plus
  `notEnabled` / `selected`); asserted as a set so trait order never makes the golden flaky. This
  is where `ctrl.buttonDisabled` must carry `base.Trait.NOT_ENABLED` and a selected segment must
  carry `base.Trait.SELECTED`.
- `value` — the `value` selector key; the mirrored `ctrl.<name>.value` labels make this a fixed,
  deterministic string at the screen's initial state.
- `frame` — asserted *tolerantly*, see "Where it runs and the validation plan" below.

The golden files live beside the catalogue's scenario, under
`demos/features/app/scenarios/` (a `goldens/` subfolder keyed by control or by screen, e.g.
`goldens/controls.json`), as checked-in JSON — the same `base.Element` dicts
`parse_describe_all` emits, so a refreshed golden is literally a recorded `query()` snapshot
with the frame tolerances applied. The assertion is a single deterministic flow: drive to the
screen via `launchEnv`, wait for the screen to settle (below), call `IdbDriver.query()`, and
compare the normalized tree to the golden field by field per control id. The comparison is plain
data equality — no LLM anywhere in the path, so it fits the run/CI verdict's determinism rule.

### Settled versus transient

The check never sleeps a fixed amount. It waits through the existing condition-wait path —
`IdbDriver.wait_for(sel, timeout)` polling `query()` until a known anchor on the target screen
(e.g. `ctrl.title` or `lists.list`) is present — and only then snapshots the tree to compare.

This keeps the golden cleanly separate from the two existing transient defenses, so neither
masks the other:

- `query()`'s `_max_seen`-gated retry (`_is_transient_empty` / `_READY_MIN`) still absorbs idb's
  mid-transition near-empty tree underneath the assertion. The golden asserts on the tree that
  retry *returns*, i.e. the settled steady state — it is not in the business of catching the
  transient.
- **BE-0087** (idb action settle, in-progress) is the complementary axis: it ensures we assert
  *after* an action's effects have settled. BE-0006 pins *what the settled tree looks like*;
  BE-0087 pins *that we wait for it*. A regression in one fails its own check without hiding a
  regression in the other — BE-0006's golden is read at a quiescent screen, where settle is not
  the variable under test.

### Drift diff

When idb, an iOS release, or a control's construction reshapes the normalized output, the golden
assertion fails on the e2e path with a concrete, field-level diff — "`ctrl.toggle`: traits
expected `{switch}` got `{button}`", or "`lists.search`: identifier expected `lists.search` got
`null`" — naming which field of which control moved. That converts a class of silent, in-the-field
failures (a scenario that quietly can no longer find its element, or matches the wrong one) into
a loud, located build failure with an obvious fix: update the normalizer in `idb.py` deliberately
and re-record the golden in the same reviewed change, or recognize a genuine idb/iOS regression.

This pairs with **BE-0005** (idb_companion version monitoring, implemented) along a clear seam:
BE-0005 watches *which* idb is installed and pins a version range; BE-0006 watches *what that idb
produces*. BE-0005's weekly `idb-monitor.yml` already drives the smoke scenario through
`parse_describe_all` against the latest companion — running the catalogue golden on that same
cadence turns "the schema changed" from a vague smoke failure into the specific field that moved.

### Where it runs and the validation plan

The golden assertion runs on the heavier on-device path, never the fast per-PR gate — the same
macOS + Simulator surface as `make -C demos/features e2e` and the `smoke` job in
[`.github/workflows/e2e.yml`](../../../.github/workflows/e2e.yml), which already builds, boots,
installs the sample app, and runs scenarios through real idb. The catalogue golden is added as a
scenario there (and is a natural addition to BE-0005's weekly monitor), so it costs metered macOS
minutes only on device-relevant PRs and on the scheduled cadence — `make check` stays untouched.

The one real tension is `frame`. Exact pixel coordinates vary with device, runtime, scale, and
safe-area insets, so asserting them exactly would be flaky by construction — directly against the
determinism directive's spirit (an assertion must fail only on a real regression). The resolution
is **exact on identity, tolerant on geometry**: `identifier`, `label`, `traits`, and `value` are
compared exactly, while `frame` is checked only for *sanity* — non-zero width and height, and the
element contained within the screen bounds (reusing the same containment logic as
`base._contains`, which `within` selectors already rely on). That is enough to catch the failure
modes that matter (an element collapsing to a zero frame, or a container that no longer encloses
its child) without pinning to a device's exact pixels. The validation plan, then: drive each
catalogue screen, snapshot `query()`, assert the four identity/state fields against the golden
exactly and the `frame` within bounds, and on any mismatch emit the field-level diff above.

## Alternatives considered

- **Rely on the existing per-scenario e2e demos.** The demo scenarios already touch idb on-device, but they assert on application behaviour, not on the normalized tree of each control type. A regression confined to, say, how `.searchable` reports its identifier might not break any current scenario yet would break the next one written against it. A dedicated controls catalogue makes the representation itself the thing under test.
- **Harden `query()` to repair odd trees.** We could add heuristics that rewrite an unexpected SwiftUI shape into the "expected" one inside the normalization path. That hides drift instead of reporting it and loads the determinism-critical path with app-specific guesses. Asserting against a golden and updating the normalizer deliberately keeps `query()` simple and the change reviewed.
- **Snapshot every screen of a real app.** Maximal coverage, but brittle and app-specific: it couples the check to one app's evolving UI and re-introduces per-app concerns into a tool-level guarantee. A focused catalogue of standard controls is more stable and stays app-agnostic.

## References

[DESIGN §11](../../../DESIGN.md)
