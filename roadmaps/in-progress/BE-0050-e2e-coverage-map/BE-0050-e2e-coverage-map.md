**English** · [日本語](BE-0050-e2e-coverage-map-ja.md)

# BE-0050 — E2E coverage map

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0050](BE-0050-e2e-coverage-map.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Topic | Candidates from competitive research (Maestro) |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

A read-only report of how much of an app's surface the scenario suite exercises — which screens
are visited, which stable `id`s are interacted with or asserted on, and which network endpoints
are observed — measured against the app's declared id namespaces. Derived from run evidence plus
static scenario analysis; no LLM, no effect on pass/fail.

## Motivation

Teams routinely ask "what do our E2E tests actually cover?" and UI E2E tools rarely answer it —
Maestro has no coverage notion at all. Yet Bajutsu already holds the raw material: every run
captures element trees, screenshots, and `network.json`, and scenarios statically declare the
selectors they touch (and, with the behavioral-assertions sibling item, the endpoints they
assert on). The app config already declares its stable-id namespaces
(`apps.<name>.idNamespaces`), which gives a denominator.

Aggregating across a suite turns that into a coverage map: the set of `id` namespaces and screens
the suite reaches, the endpoints it observes, and — crucially — the **gaps**: declared namespaces
with no scenario touching them, screens never visited. Paired with autonomous crawl
([BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)),
the crawler's discovered surface becomes a second denominator: "of the screens exploration found,
how many does the suite visit?" This is a support tool that makes test gaps legible, and a
capability the UI-only competitors do not offer.

## Detailed design

Proposal altitude.

- **Inputs.** (1) Static scenario parse — the selectors, screens (via `setup` / deeplinks), and
  asserted endpoints each scenario references. (2) Run evidence across a run set — element trees
  actually rendered, screens actually reached, exchanges in `network.json`.
- **Denominator.** The app's declared `idNamespaces` and, when available, the discovered surface
  from a [BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  crawl. App-agnostic: the denominator comes from `apps.<name>` config, not hard-coded knowledge.
- **Output.** An HTML / JSON coverage report: per-namespace coverage, screens visited vs known /
  discovered, endpoints observed vs declared, and an explicit gap list (untested namespaces /
  unvisited screens). Read-only — it never changes a verdict and is not part of the CI gate
  (though a team may choose to track the number in CI as informational).
- **Determinism.** Every figure is a deterministic aggregation over captured artifacts; there is
  no model and no judgement call. This keeps the feature firmly on the Tier-1 / reporting side.

### Implementation status

The first slice ships the **id-namespace dimension** statically — the highest-value figure, whose
denominator (declared `idNamespaces`) is already fully defined. `bajutsu coverage --app <name>`
(`bajutsu/coverage.py`, `bajutsu/cli/commands/coverage.py`) walks every scenario in the app's
configured `scenarios` dir, groups the stable ids each references (via `bajutsu.audit.referenced_ids`,
reusing the audit selector walk) by namespace, and reports per-namespace coverage, the gap list, and
off-namespace ids — read-only, AI-free, exits 0 even with gaps. It is the suite-level cousin of
`doctor`'s per-screen convention score.

The second slice adds the **endpoints-observed-vs-declared** dimension, now that the
behavioral-assertions sibling ([BE-0048](../../implemented/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md))
ships the "declared" half. `bajutsu coverage --runs <dir>` reads the union of every `network.json`
under the run set (the first run evidence the map folds in) and measures how many **observed**
endpoints (`METHOD path`) the suite's network assertions — `request` / `event` / `requestSequence`,
collected by `coverage.referenced_requests` and tested with `assertions.match_request` — cover,
reporting the asserted fraction, the **unasserted** observed endpoints (untested traffic), and
matchers declared but observed in no run. (`responseSchema`'s `request` joins the declared set once
[#212](https://github.com/bajutsu-e2e/bajutsu/pull/212) lands.)

The third slice folds **observed ids** into the id-namespace map alongside the static analysis,
under the same `--runs` flag. `coverage.observed_id_coverage` collects the stable ids the runs
actually *rendered* — each element's `identifier` from every per-step `elements.json` under the run
set (null ids dropped) — and groups them by the declared `idNamespaces`, mirroring the static
`coverage()`: per-namespace observed ids, namespaces *observed in no run*, and off-namespace
observed ids. It complements the static "referenced" figure (which ids the scenarios *write*) with
the run-evidence "observed" figure (which ids the runs *showed*), exposing namespaces a run set
never actually exercised. Still read-only, AI-free, and exits 0 even with gaps.

Still deferred: the **screens-visited** dimension (needs a crawl-discovered denominator,
[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)).

## Alternatives considered

* **Source-level code coverage instrumentation.** A different layer (it measures app code paths,
  is language/build-specific, and requires instrumenting the app), which breaks app-agnosticism.
  Coverage here is deliberately defined over the *testable UI / protocol surface* Bajutsu already
  observes, not the app's source.
* **Have an LLM estimate coverage from the scenarios.** Rejected: non-deterministic and
  unnecessary — coverage against declared namespaces and captured evidence is an exact count.
* **Do nothing (status quo).** Acceptable, but "what's covered?" stays unanswered and gaps stay
  invisible; the evidence and namespace declarations needed to answer it already exist.

## References

`bajutsu/doctor.py` (id namespaces / convention score),
[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md),
[evidence.md](../../../docs/evidence.md), [configuration.md](../../../docs/configuration.md),
[DESIGN §2 / §7](../../../DESIGN.md)
