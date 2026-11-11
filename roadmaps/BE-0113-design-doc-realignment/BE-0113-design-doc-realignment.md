**English** · [日本語](BE-0113-design-doc-realignment-ja.md)

# BE-0113 — Realign DESIGN.md with the current implementation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0113](BE-0113-design-doc-realignment.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0113") |
| Implementing PR | [#565](https://github.com/bajutsu-e2e/bajutsu/pull/565) |
| Topic | Contributor workflow |
| Related | [BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement.md), [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`DESIGN.md` opens by calling itself the finalized design ("設計確定版"). Several of its statements
have fallen behind the implementation. This item makes three changes:

- It repositions the opening as a *record of design decisions and their rationale*, and points the
  reader to `docs/architecture.md` for current implementation status.
- It corrects or annotates the three concrete divergences below.
- It adds a working norm so the same kind of drift does not silently recur.

The change is documentation, but it warrants a tracked item. `DESIGN.md` is a foundational
document, and `readme = "DESIGN.md"` in `pyproject.toml` makes it the package's published long
description. Its accuracy therefore reaches beyond the repository.

## Motivation

`docs/architecture.md` is the source of truth for implementation status, and that division of labor
is intended. `DESIGN.md` does not say so itself. It presents as the finalized design and never
directs the reader to `docs/architecture.md` for the current state. When the two disagree, a reader
cannot tell from the document which one to trust. Three divergences are confirmed against `main`:

1. **Network evidence source (§3.2, §9).** DESIGN.md describes a single external **mock server** as
   both the network-mocking mechanism and the `network` evidence source.
   [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external.md) deferred that external
   mock server, and the implementation moved to in-scenario `mocks` (in-protocol stubs).
2. **Module layout (§4).** The structure diagram shows a flat file layout (`orchestrator.py`,
   `scenario.py`, …). That layout no longer matches the current package structure (`serve/`,
   `crawl`, `mcp/`, and the rest, ~30,000 lines).
3. **Backend status (§3).** The diagram labels the XCUITest backend "(将来)" (future), but
   [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) is In progress.

None of these divergences is a design error. Each is staleness. The fix makes DESIGN.md honest about
its own role and reconciles the three points. It redesigns nothing.

Nothing currently prompts a contributor who changes behavior to also touch DESIGN.md or
`docs/architecture.md`. The same three kinds of drift are therefore likely to recur once this pass is
done. The fix includes a norm against recurrence, not just the one-time correction.

## Detailed design

The work is MECE along the six work items below. The "Machine-checkable outcome" and
"Prime-directive compliance" subsections record acceptance criteria, not additional work items.
`DESIGN.md` is written in Japanese, so all edits follow the
[`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/SKILL.md) skill.

### 1. Reposition the opening

Change the opening status line. DESIGN.md should state plainly that it records design decisions and
their rationale, and that `docs/architecture.md` is the source of truth for current implementation
status. This resolves the "which document do I trust" ambiguity once, at the top.

### 2. Reconcile the network evidence source (§3.2, §9)

Correct or annotate the external-mock-server description so that it reflects the in-scenario `mocks`
(in-protocol stubs) that shipped. Cite BE-0027 for why the external server was deferred.

### 3. Reconcile the module layout (§4)

Either update the structure diagram to the current package layout, or replace it with a note. The
note says the layout is illustrative and that `docs/architecture.md` holds the authoritative,
current structure. Choose whichever keeps DESIGN.md a decision record rather than a structure
snapshot that will drift again.

### 4. Reconcile the backend status (§3)

Update the XCUITest backend label from "(将来)" so that it reflects BE-0019 being In progress, and
cite BE-0019.

### 5. Sweep for further divergences

While making the edits above, do a bounded pass for other statements that have fallen behind. Fix or
annotate each one in the same change, without expanding into a full rewrite.

### 6. Add a norm against recurrence

Add a line to [`CLAUDE.md`](../../CLAUDE.md)'s Conventions, alongside the existing rule to update
both `docs/` and `docs/ja/` when a documented behavior changes. The line states that a PR changing
behavior described by `DESIGN.md` or `docs/architecture.md` must update the affected document in the
same change.

This norm cannot become a CI gate. Verifying that a paragraph of prose still describes current
behavior requires understanding prose against code, and that would put an LLM on the `run`/CI
verdict path (prime directive 1). The norm stays a review-time norm, like the existing conventions
beside it.

### Machine-checkable outcome

This item is documentation, so it has no behavioral assertion. Its gate is the docs / roadmap
link-integrity and format checks already in `make check`
([BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md)). After
the edit, `make check` stays green and the bilingual links remain intact. Correctness of the prose
is a review judgment, not a machine verdict. We state that plainly rather than dress it up as a
test.

### Prime-directive compliance

This item is documentation only. It adds no code and no LLM, and touches nothing on the `run` / CI
verdict path. It strengthens the directives indirectly. It removes a stale account of them (the
deferred external mock server) that could mislead a reader about how determinism and evidence work
today.

## Alternatives considered

- **Leave DESIGN.md as-is and rely on architecture.md.** Rejected. The unqualified "finalized
  design" claim actively misleads, because the document gives a reader no signal that its network,
  layout, and backend statements are stale. Every new reader pays the cost of the divergence.
- **Delete DESIGN.md and fold everything into architecture.md.** Rejected. DESIGN.md's value is the
  *why*: the design decisions and their rationale. architecture.md is a status document and does
  not carry them. Keep both, with DESIGN.md's role stated explicitly.
- **Fully rewrite DESIGN.md.** Rejected as over-scope. A targeted repositioning plus the three
  reconciliations suffices. A rewrite risks discarding the decision history, which is the
  document's actual worth.
- **Automate staleness detection in CI instead of a norm.** Rejected. Verifying that DESIGN.md's
  prose still matches the implementation requires semantic judgment that an assertion cannot
  express. That would put an LLM on the `run`/CI verdict path, which prime directive 1 forbids. A
  review-time norm is the achievable version of this safeguard.
- **Fix the three divergences without adding a norm.** Rejected. Without something that prompts a
  future contributor to update DESIGN.md and architecture.md alongside a behavior change, the same
  kind of drift reappears. This PR would then be one of a recurring series of one-time fixes.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Reposition the opening (record of design decisions; point to architecture.md for status)
- [x] Reconcile the network evidence source (§3.2, §9) with in-scenario `mocks`; cite BE-0027
- [x] Reconcile the module layout (§4) with the current package structure (or note architecture.md is authoritative)
- [x] Reconcile the backend status (§3) — XCUITest is In progress (BE-0019)
- [x] Bounded sweep for further divergences; fix / annotate in the same change
- [x] Add a CLAUDE.md norm requiring DESIGN.md/architecture.md updates alongside behavior changes

## References

- [DESIGN.md](../../DESIGN.md) — the document this realigns; also the package's published description via `readme = "DESIGN.md"` in `pyproject.toml`
- [docs/architecture.md](../../docs/architecture.md) — the implementation-status source of truth DESIGN.md should defer to
- [CLAUDE.md](../../CLAUDE.md) — where the recurrence-prevention norm is added, alongside the existing bilingual-docs convention
- [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external.md) — why the external mock server was deferred, replaced by in-scenario `mocks`
- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the XCUITest backend now In progress, not "future"
- [BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) — precedent: a documentation item that realigned the scope statement with reality
- [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) — the docs / link-integrity gate this change must keep green
