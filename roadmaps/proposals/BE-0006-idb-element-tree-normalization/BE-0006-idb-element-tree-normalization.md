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

## Alternatives considered

- **Rely on the existing per-scenario e2e demos.** The demo scenarios already touch idb on-device, but they assert on application behaviour, not on the normalized tree of each control type. A regression confined to, say, how `.searchable` reports its identifier might not break any current scenario yet would break the next one written against it. A dedicated controls catalogue makes the representation itself the thing under test.
- **Harden `query()` to repair odd trees.** We could add heuristics that rewrite an unexpected SwiftUI shape into the "expected" one inside the normalization path. That hides drift instead of reporting it and loads the determinism-critical path with app-specific guesses. Asserting against a golden and updating the normalizer deliberately keeps `query()` simple and the change reviewed.
- **Snapshot every screen of a real app.** Maximal coverage, but brittle and app-specific: it couples the check to one app's evolving UI and re-introduces per-app concerns into a tool-level guarantee. A focused catalogue of standard controls is more stable and stays app-agnostic.

## References

[DESIGN §11](../../../DESIGN.md)
