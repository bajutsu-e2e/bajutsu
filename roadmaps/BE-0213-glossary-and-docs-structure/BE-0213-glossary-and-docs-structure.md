**English** · [日本語](BE-0213-glossary-and-docs-structure-ja.md)

# BE-0213 — Terminology glossary and documentation structure review

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0213](BE-0213-glossary-and-docs-structure.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0213") |
| Implementing PR | [#853](https://github.com/bajutsu-e2e/bajutsu/pull/853) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's documentation uses a cluster of closely related domain terms — `scenario`, `goal`,
`step`, `driver` / `backend` / `actuator` / `platform`, `target` / `app` / `device`, `trace` /
`triage`, and the `capturePolicy` "rule" — without one place that defines each term and how it
relates to its neighbors. This proposal adds a dedicated glossary page and revisits the
surrounding documentation structure (the reading order in [`docs/overview.md`](../../docs/overview.md),
and where a first-time reader meets these terms) so that a newcomer is never left guessing which
of two similar-sounding words is which.

## Motivation

No existing page collects term definitions: a repo-wide search for "glossary" / "用語" /
"terminology" turns up nothing but tangential mentions. [`docs/concepts.md`](../../docs/concepts.md)
explains the design *rationale* (why the two-tier structure exists, why selectors prefer an id),
not a term-by-term reference a reader can look a word up in.

A concrete inconsistency turned up while auditing the docs for this proposal:
[`docs/getting-started.md:81`](../../docs/getting-started.md) calls a scenario file "a list of
named **tests**", while [`docs/scenarios.md`](../../docs/scenarios.md) and the schema itself
(`SCHEMA_VERSION`, `bajutsu/scenario/models/scenario.py`) call each entry "a **scenario**". A
reader who lands on both pages meets two names for the same object with no signal that they're the
same thing.

Beyond that one slip, several term clusters are genuinely non-trivial and currently reconstructed
piecemeal by the reader:

- `driver` (the abstract `Driver` interface) / `backend` (idb, xcuitest, adb, playwright, fake — the
  implementations of it, and the config's stability-ordered list) / `actuator` (the one backend
  chosen for a given run) / `platform` (`ios` / `android` / `web` / `fake` — a token that expands to
  a backend). [`docs/drivers.md`](../../docs/drivers.md) and
  [`docs/concepts.md`](../../docs/concepts.md)`#5` each explain part of this, but neither states
  the full relationship in one place. (The enumeration here follows `bajutsu/backends.py`'s
  `IMPLEMENTED` set / `PLATFORM_ACTUATORS`, not the docs — `docs/multi-platform.md` and CLAUDE.md
  still call Android "planned" though `adb` is already wired as an actuator, which is itself a
  drift this inventory is meant to catch.)
- `trace` (inspect a finished run as a text timeline) and `triage` (diagnose a failed run and
  propose a fix) are two distinct CLI commands one edit apart in spelling — an easy pair to
  conflate on a first read of [`docs/cli.md`](../../docs/cli.md).
- `target` (a `targets.<name>` config entry) / `app` (the application under test itself) / `device`
  (the Simulator instance a target is driven on) look interchangeable to a newcomer even though
  BE-0057 deliberately renamed the config concept from `app` to `target` to separate it from the
  app it describes.
- A `capturePolicy` entry is called a "rule" throughout [`docs/evidence.md`](../../docs/evidence.md);
  the schema does define a `CaptureRule` type (`bajutsu/scenario/models/evidence.py`), but the three
  names for the same concept — `capturePolicy`, `CaptureRule`, and "rule" in prose — aren't
  reconciled anywhere.

The project is pre-alpha and the [public docs site](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)
has already shipped, so more readers and more pages will accumulate around today's terminology
choices from here on. CLAUDE.md's own documentation-style rule already asks for "no coined terms"
and "no omissions" per sentence; this proposal applies the same bar structurally, with one page a
reader (and every other page) can point to instead of re-defining a term locally each time.

## Detailed design

The work breaks down into five independent, sequential units:

1. **Term inventory.** Enumerate every domain term surfacing in scenario authoring, natural-language
   goals/instructions, and the CLI/config surface — `scenario`, `goal`, `step`, `precondition`,
   `expect` / assertion, selector / identifier, `component`, `capturePolicy` rule, evidence, Tier 1 /
   Tier 2, `driver` / `backend` / `actuator` / `platform`, `target` / `app` / `device`, and the CLI
   verbs (`record` / `crawl` / `run` / `trace` / `triage` / `codegen` / `doctor`), plus `from`
   (provenance). The source of truth is the current implementation (the DSL grammar and the pydantic
   models under `bajutsu/scenario/`, and the backend registry in `bajutsu/backends.py`), not how a
   given doc page happens to phrase it today — the docs already lag the code on this point (Android /
   `adb`).
2. **A new page, `docs/glossary.md` (+ `docs/ja/glossary.md`).** One entry per term: a one-sentence
   definition, a pointer to the page/module that explains it in depth, and — for each cluster
   identified above — an explicit disambiguation (e.g. a short table for `driver` / `backend` /
   `actuator` / `platform`; one line settling "a scenario **file** holds a list of scenarios; each
   named entry is itself **a scenario**, never a *test*"; one line distinguishing `trace` from
   `triage`). Existing pages should link a term's first mention to its glossary entry instead of
   re-explaining it inline.
3. **Fix the inconsistencies the inventory surfaces.** At minimum, the confirmed one:
   `docs/getting-started.md:81`'s "a list of named tests" → "a list of named scenarios", matching
   `docs/scenarios.md`'s own definition. Sweep the rest of `getting-started.md`, `docs/index.md`,
   and `docs/overview.md` for the same drift while the inventory is fresh.
4. **Reading-order and structure review.** Decide where the glossary sits in
   `docs/overview.md`'s numbered reading order — a candidate is directly before or after
   `concepts.md`, so a reader has the vocabulary before the design rationale leans on it — and
   whether `concepts.md`'s per-principle prose should shrink to link glossary entries rather than
   re-define terms locally. Update `README.md` and `docs/index.md`'s pointers to match whatever
   order is chosen.
5. **Bilingual pass.** Write `docs/ja/glossary.md` as natural Japanese per the
   [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill, not a literal
   translation — DESIGN.md's own Japanese vocabulary doesn't always map one-to-one onto the English
   glossary entries (it uses 証跡 loosely for both "evidence" and "trace", for instance), so the
   Japanese entry should make that mapping explicit wherever it isn't literal.

Out of scope: renaming any term at the code, CLI-flag, or config-key level (`capturePolicy`,
`backend`, "Tier 1" / "Tier 2", and similar remain unchanged). This proposal documents and
disambiguates the terms Bajutsu already uses; it does not argue for renaming any of them. If the
inventory surfaces a term whose *name itself* (not just its documentation) seems worth revisiting,
that becomes its own future proposal rather than scope creep here.

A sibling proposal, the web-only beginner tutorial drafted alongside this one, depends on this one
landing first so its new tutorial track can use the disambiguated vocabulary this glossary settles,
rather than inventing its own phrasing in parallel. The two are reciprocal siblings; a `Related`
metadata link between them is added once CI allocates their real IDs (a placeholder `BE-0213` cannot
cross-reference another new item — the per-item rewrite would resolve it to this item's own number).

## Alternatives considered

- **Fold the glossary into `concepts.md` instead of a new page.** Rejected: `concepts.md` explains
  *why* Bajutsu is shaped the way it is; a glossary explains *what a word means*. Mixing the two
  means every future term addition edits a page whose job is design rationale, and looking up one
  word requires reading past the rationale to find it.
- **Give each feature page its own small "terms used here" box instead of one canonical glossary.**
  Rejected: this doesn't solve the cross-page inconsistency this proposal is responding to — each
  page would still write its own definition, which is exactly how "test" vs. "scenario" drifted
  apart in the first place. Cross-referencing a term from any page requires one canonical source to
  point to.
- **Leave the current terms and structure as they are, and fix drift opportunistically in future
  doc PRs.** Rejected: without a deliberate, all-pages-at-once inventory pass, a near-synonym
  cluster like `driver` / `backend` / `actuator` / `platform` never surfaces as a problem to any
  single PR — it only became visible here by grepping every page side by side.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Term inventory (scenario / goal / step / driver-backend-actuator-platform /
      target-app-device / trace-triage / capturePolicy rule / Tier 1-2 / CLI verbs / provenance)
- [x] 2. New `docs/glossary.md` (+ `docs/ja/glossary.md`) with disambiguation for each cluster
- [x] 3. Fix confirmed drift (`getting-started.md`'s "named tests" and any siblings found in the sweep)
- [x] 4. Reading-order / structure review (`docs/overview.md`, `README.md`, `docs/index.md` pointers)
- [x] 5. Bilingual pass for the new page, per the `japanese-tech-writing` skill

**Log**

- 2026-07-09 ([#853](https://github.com/bajutsu-e2e/bajutsu/pull/853)): Added `docs/glossary.md` and `docs/ja/glossary.md` — one entry per
  domain term with disambiguation tables for the `driver` / `backend` / `actuator` / `platform`,
  `target` / `app` / `device`, scenario-vs-test, and `trace`-vs-`triage` clusters, grounded in
  `bajutsu/backends.py` and the scenario models rather than any doc's phrasing. Wired the page into
  `mkdocs.yml`'s Concepts section (with the `用語集` nav translation) and as item 2 of
  `docs/overview.md`'s reading order; added pointers from `docs/index.md` and `docs/README.md`.
  Fixed the confirmed `getting-started.md` "a list of named tests" → "scenarios" drift. Trimmed
  `concepts.md` §2 / §5 / §7 to link the glossary instead of re-defining Tier 1-2, the
  driver/backend/actuator/platform cluster, and `capturePolicy` inline (the §5 trim also drops the
  "Android planned" drift and the incomplete backend enumeration from that page). The glossary's
  backend table records that `adb` is implemented, not planned; the remaining "Android planned"
  prose in `docs/multi-platform.md` and `CLAUDE.md` is left as a scoped follow-up. Status →
  Implemented.

## References

- [`docs/concepts.md`](../../docs/concepts.md) — design rationale the glossary should link to, not restate
- [`docs/overview.md`](../../docs/overview.md) — the reading order this proposal revisits
- [`docs/getting-started.md`](../../docs/getting-started.md) — site of the confirmed "named tests" drift
- [`docs/scenarios.md`](../../docs/scenarios.md) · [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md) — the schema-level source of truth for term definitions
- [`docs/drivers.md`](../../docs/drivers.md) · [`docs/cli.md`](../../docs/cli.md) — the driver/backend/actuator and trace/triage clusters
- [BE-0093](../BE-0093-public-docs-site/BE-0093-public-docs-site.md) — the public docs site this glossary will be published on
- [BE-0057](../BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md) — the prior `app` → `target` config rename this glossary documents
- CLAUDE.md's documentation-style rule ("no coined terms", "no omissions") — the per-sentence bar this proposal applies structurally
