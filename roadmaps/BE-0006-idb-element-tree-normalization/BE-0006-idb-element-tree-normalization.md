**English** · [日本語](BE-0006-idb-element-tree-normalization-ja.md)

# BE-0006 — idb element-tree normalization accuracy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0006](BE-0006-idb-element-tree-normalization.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0006") |
| Implementing PR | [#384](https://github.com/bajutsu-e2e/bajutsu/pull/384), [#386](https://github.com/bajutsu-e2e/bajutsu/pull/386), [#389](https://github.com/bajutsu-e2e/bajutsu/pull/389) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item continuously confirms, on a real device, that the tree representation of standard SwiftUI elements (for example `.searchable`) does not break.

## Motivation

Selector resolution is the determinism core. A selector matches exactly one element; zero matches mean "not present"; two or more matches are an ambiguity error. Every one of these decisions is made against the element tree that `IdbDriver.query()` normalizes out of idb's `ui describe-all`. The correctness of the *whole* run loop is therefore only as good as that normalization.

Suppose a standard SwiftUI control does not surface in the tree the way we expect. The identifier may be wrong, a trait may be missing, or a container may swallow its child. A scenario written against that control then either silently cannot find the element or, worse, matches a different one.

DESIGN §11 already records two concrete symptoms.

- **Named accuracy risks.** `.searchable` and similar standard SwiftUI elements are called out by name.
- **A near-empty tree mid-transition.** We have observed idb returning a single element with no identifier, even though the screen has rendered.

`IdbDriver.query()` mitigates the second symptom with a bounded, gated retry (`_max_seen` / `_READY_MIN`). That retry defends against a *transient*. It does not tell us whether the *settled* tree for a given SwiftUI control is shaped the way scenarios assume. The retry note itself ends by pointing at "8-second-class pathological cases", which the driver deliberately does not mask.

What is missing is a continuous, on-device check that the steady-state representation of the SwiftUI controls real apps use has not drifted. With such a check, a regression in idb, in iOS, or in how a control is built shows up as a failing check. Without it, the regression shows up as a flaky or wrong scenario in the field.

## Detailed design

The proposal is a maintained on-device fixture that pins the expected normalized tree for the SwiftUI controls that scenarios actually depend on. The fixture runs on the heavier e2e path that already validates idb, not on the fast per-PR gate.

- **A controls catalogue screen in the sample app.** The demo app (`demos/`) hosts a screen that instantiates the standard SwiftUI controls we care about: `.searchable`, `List`, `NavigationStack`, `TextField`, toggles, segmented controls, and others. Each control gets a stable `accessibilityIdentifier` from the app's id namespace. Per-app identifiers are an app concern, so the fixture exercises the *normalization*, not any one production app. The tool stays app-agnostic.
- **Golden normalized trees.** For each control, the expected `Element` shape is recorded as a checked-in expectation. The shape covers the fields that the `Selector` semantics rely on: `identifier`, `label`, `traits`, `value`, and a sane `frame`. The check drives the app to that screen, calls `query()`, and asserts the normalized tree against the golden. The assertion is machine-checkable, so it fits the deterministic gate with no LLM in the loop.
- **Settled vs. transient is explicit.** The check waits for the screen through the existing condition-wait path (no fixed sleep), then asserts on the *settled* tree. The `_max_seen`-gated retry continues to absorb mid-transition emptiness. This proposal's golden covers the steady state, after that retry has done its job. The two concerns stay separate, and neither masks the other.
- **Drift surfaces as a failing assertion.** Suppose idb, an iOS release, or a control's construction changes the normalized shape. The golden assertion then fails on the e2e path with a concrete diff that names which field of which control moved. Without the assertion, the change leaks out as a field scenario that can no longer find its element. The assertion pairs naturally with BE-0005's version monitoring: BE-0005 watches *which* idb is installed, and this item watches what idb *produces*.

Nothing here relaxes selector strictness. An ambiguous match still fails, a missing element still fails, and the golden assertions are exact comparisons.

### The controls catalogue screen

The catalogue is not a new app to build. It is the standard-control surface that the sample app
(`demos/features/app/BajutsuSample/`) already carries, made the explicit subject of a golden.
`MainTabView` in `RootView.swift` already wires the screens that a catalogue needs. Each screen is
launchable in isolation through `launchEnv` (`SAMPLE_TAB`), so the check can drive straight to one
without walking the UI:

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

These ids come from the app's own namespace: the `ctrl` and `lists` prefixes that the existing
`ControlsView` and `ListsNavView` scenarios already use. Today the `idNamespaces` list in
[`demos/features/demo.config.yaml`](../../demos/features/demo.config.yaml) holds `home`, `list`,
`comp`, and others, but **not** `ctrl` or `lists`. Wiring the catalogue therefore also adds those two
prefixes there. The change is one line of config, stated here so the proposal stays grounded in the
repo's current state.

The catalogue exercises how idb *normalizes* a `Toggle` or a `List` into `Element`s. It does not
exercise any one app's choice of names.

Suppose a future control type is worth pinning and no existing screen hosts it. It is added to the
catalogue with a stable `accessibilityIdentifier`. The surface grows, and the mechanism does not
change.

Keeping the catalogue inside the existing sample app, rather than in a second fixture app, has one
more benefit. The build, boot, and install that the e2e jobs already perform cover the catalogue
at no extra cost.

### Golden format and the assertion

Each control's expectation is one `base.Element` (`bajutsu/drivers/base.py`). That is exactly the
shape `query()` produces, so the comparison is value for value, with no adapter in between.

The golden records the per-element fields that drive selection and actuation. Four of them are the
fields that `base.matches` consults: `identifier`, `label`, `traits`, and `value`. The fifth is
`frame`. `matches` does *not* read `frame`, but `find_all`'s `within` (geometric containment) and
idb's coordinate taps do, so its normalized shape is worth pinning too:

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
- `frame` — not consulted by `base.matches`, but `find_all`'s `within` scoping and coordinate
  actuation rely on it; asserted *tolerantly*, see "Where it runs and the validation plan" below.

The golden files live beside the catalogue's scenario, under `demos/features/app/scenarios/`.
A `goldens/` subfolder is keyed by control or by screen, for example `goldens/controls.json`.
The files are checked-in JSON holding the same `base.Element` dicts that `parse_describe_all`
emits. A refreshed golden is therefore literally a recorded `query()` snapshot with the frame
tolerances applied.

The assertion is a single deterministic flow:

1. Drive to the screen via `launchEnv`.
2. Wait for the screen to settle (below).
3. Call `IdbDriver.query()`.
4. Compare the normalized tree to the golden, field by field, per control id.

The comparison is deterministic but **per-field, not whole-dict equality**. It is exact for `identifier`,
`label`, and `value`; set equality (order-independent) for `traits`; and a tolerant geometric
check for `frame` (below). No LLM appears anywhere in the path, so the check fits the determinism
rule of the run/CI verdict.

### Settled versus transient

The check never sleeps a fixed amount. It waits through the existing condition-wait path:
`IdbDriver.wait_for(sel, timeout)` polls `query()` until a known anchor on the target screen
(for example `ctrl.title` or `lists.list`) is present. Only then does the check snapshot the tree
to compare.

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

Suppose idb, an iOS release, or a control's construction reshapes the normalized output. The golden
assertion then fails on the e2e path with a concrete, field-level diff, such as "`ctrl.toggle`: traits
expected `{switch}` got `{button}`" or "`lists.search`: identifier expected `lists.search` got
`null`". The diff names which field of which control moved.

The diff converts a class of silent, in-the-field failures into a loud, located build failure.
Those failures are a scenario that quietly can no longer find its element, and a scenario that
matches the wrong one. The fix is then obvious. Either update the normalizer in `idb.py`
deliberately and re-record the golden in the same reviewed change, or recognize a genuine idb or
iOS regression.

The golden pairs with **BE-0005** (idb_companion version monitoring, implemented) along a clear
seam. BE-0005 watches *which* idb is installed and pins a version range. BE-0006 watches *what that
idb produces*.

BE-0005's weekly `idb-monitor.yml` already drives the smoke scenario through `parse_describe_all`
against the latest companion. Running the catalogue golden on that same cadence turns "the schema
changed" from a vague smoke failure into the specific field that moved.

### Where it runs and the validation plan

The golden assertion runs on the heavier on-device path, never on the fast per-PR gate. It uses the
same macOS and Simulator surface as `make -C demos/features e2e` and the `smoke` job in
[`.github/workflows/e2e.yml`](../../.github/workflows/e2e.yml). That surface already builds and
boots the Simulator, installs the sample app, and runs scenarios through real idb.

The catalogue golden is added there as a scenario. It is also a natural addition to BE-0005's
weekly monitor. It costs metered macOS minutes only on device-relevant PRs and on the scheduled
cadence, and `make check` stays untouched.

The one real tension is `frame`. Exact pixel coordinates vary with device, runtime, scale, and
safe-area insets. Asserting them exactly would be flaky by construction, which runs directly
against the spirit of the determinism directive: an assertion must fail only on a real regression.

The resolution is **exact on identity, tolerant on geometry**. `identifier`, `label`, `traits`, and
`value` are compared exactly. `frame` is checked only for *sanity*: the width and height are
non-zero, and the element is contained within the screen bounds. The check reuses the containment
logic of `base._contains`, which `within` selectors already rely on. That is enough to catch the
failure modes that matter, namely an element collapsing to a zero frame and a container that no
longer encloses its child. It does so without pinning to a device's exact pixels.

The validation plan, then, has three steps:

1. Drive each catalogue screen and snapshot `query()`.
2. Assert the four identity and state fields against the golden exactly, and the `frame` within bounds.
3. On any mismatch, emit the field-level diff above.

## Alternatives considered

- **Rely on the existing per-scenario e2e demos.** The demo scenarios already touch idb on a device, but they assert on application behavior, not on the normalized tree of each control type. A regression confined to how `.searchable` reports its identifier, for example, might break no current scenario and yet break the next scenario written against it. A dedicated controls catalogue makes the representation itself the thing under test.
- **Harden `query()` to repair odd trees.** We could add heuristics that rewrite an unexpected SwiftUI shape into the "expected" one inside the normalization path. The heuristics would hide drift instead of reporting it, and they would load the determinism-critical path with app-specific guesses. Asserting against a golden and updating the normalizer deliberately keeps `query()` simple and the change reviewed.
- **Snapshot every screen of a real app.** Snapshotting every screen gives maximal coverage, but it is brittle and app-specific. It couples the check to one app's evolving UI and re-introduces per-app concerns into a tool-level guarantee. A focused catalogue of standard controls is more stable and stays app-agnostic.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §11](../../DESIGN.md)
