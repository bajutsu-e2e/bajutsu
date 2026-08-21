**English** · [日本語](BE-XXXX-be-progress-tracker-ja.md)

# BE-XXXX — Track BE work in a live progress Artifact via a dedicated low-cost skill

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-be-progress-tracker.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1673](https://github.com/bajutsu-e2e/bajutsu/pull/1673) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Add **`be-progress-tracker`**, a small dedicated skill that [`ideation`](../../.agent-workflows/ideation/workflow.md),
[`implement-be`](../../.agent-workflows/implement-be/workflow.md), and
[`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md) call at their own step
boundaries to keep one live status page per BE item — its overview, implementation progress, and a
work log — so a human watching a long session can see where an item stands without reading the
whole transcript. It is a **separate** skill rather than logic folded into the three callers, and
it defaults to a low-token-consumption model (`haiku`), so every checkpoint stays cheap regardless
of which model the calling workflow itself runs on.

## Motivation

An `implement-be` or `propose-and-build` session can run long: a branch, a confirmed plan, written
code, one or more review rounds, a gate run, an opened PR, and a paced follow-up loop. Today the
only record of that progress is the session transcript itself, plus the eventual roadmap `Status`
flip and the PR — there is no single, shareable place that answers "where does this item stand
right now" without reading the whole conversation.

Two more points sharpen the design:

1. **Reusing the calling skill's own model would tax every checkpoint at that skill's rate.**
   `implement-be` defaults to `opus`; a status-page write is pure transcription of a decision the
   calling skill already made, not new judgment, so paying `opus` rates for it on every checkpoint
   would be wasteful. [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)
   already established the pattern of a lightweight default `model:` on a mechanical skill (see
   [`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md)'s `haiku`); this item reapplies
   it to a new, narrower job.
2. **The three roadmap-authoring/implementing skills already share the same checkpoint shape** — a
   plan confirmed, a review pass clean, the gate green, a PR opened. A single tracker skill, called
   identically from all three, keeps that logic in one place instead of three skills each growing
   their own status-formatting code.

## Detailed design

The work breaks down into five independent pieces:

1. **Shared workflow** — [`.agent-workflows/be-progress-tracker/workflow.md`](../../.agent-workflows/be-progress-tracker/workflow.md)
   defines the document's three fixed sections (Overview, Progress, Work log), the checkpoint
   contract a caller must supply (the BE id, the calling workflow's name and step, and one work-log
   sentence), and the non-goals: never a source of truth (the roadmap item and the PR stay
   canonical), never blocking (a failed or unavailable checkpoint is noted and the calling workflow
   continues), and read-only against everything except its own status page.
2. **Claude adapter** — [`.claude/skills/be-progress-tracker/SKILL.md`](../../.claude/skills/be-progress-tracker/SKILL.md)
   carries `model: haiku` in its frontmatter and publishes the status page as a Markdown Artifact,
   one per BE item, redeployed to the same URL on every later checkpoint rather than republished
   from scratch.
3. **Codex adapter** — [`.agent-hosts/codex/skills/be-progress-tracker/SKILL.md`](../../.agent-hosts/codex/skills/be-progress-tracker/SKILL.md)
   has no hosted-Artifact equivalent to reach for, so it writes the same three-section page to a
   local, gitignored file under `tmp/be-status/` instead and tells the user that path once.
4. **Checkpoint wiring in the three calling workflows** — `implement-be/workflow.md` and
   `ideation/workflow.md` each gain one short paragraph naming which of their own numbered steps
   warrant a checkpoint. `propose-and-build/workflow.md` defines none of its own: it notes that it
   inherits the checkpoints of the `ideation` and `implement-be` phases it delegates to, keyed on
   the same `BE-XXXX` placeholder it already carries through both phases.
5. **Claude-adapter dispatch instructions** — `implement-be`'s, `ideation`'s, and
   `propose-and-build`'s `SKILL.md` each gain one line telling the host to dispatch the checkpoint
   through the Agent tool with `model: "haiku"` passed explicitly, since a subagent call does not
   automatically inherit a skill's own frontmatter model. The checkpoints themselves are
   inheritable across `propose-and-build`'s two delegated phases (point 4 above), but their
   dispatch is not: an adapter loads only its own shared workflow, never a sibling skill's adapter,
   so `propose-and-build`'s adapter needs this line too, not only `implement-be`'s and
   `ideation`'s.

No product code changes: this item is entirely contributor-workflow tooling under `.agent-workflows/`,
`.claude/skills/`, and `.agent-hosts/codex/skills/`, and touches no `bajutsu/`, `BajutsuKit/`, or
`run`/CI path. Prime directive 1 is untouched — the tracker never appears anywhere near the
deterministic gate; it only ever formats decisions a human-in-the-loop skill has already made.

## Alternatives considered

- **Fold the tracker into each calling skill's own steps, instead of a separate skill.** Rejected:
  it would duplicate the "format a short status page" logic three times and tie every checkpoint's
  cost to whichever model the calling skill happens to run at (`opus` for `implement-be`), defeating
  the point of keeping checkpoints cheap.
- **Write the status to a file committed in the repository instead of an Artifact.** Rejected for
  the Claude Code adapter: a live, frequently rewritten scratch document either needs a commit on
  every checkpoint (noisy history, and a race with the actual implementation commits) or stays
  untracked and invisible to anyone who isn't in the session. An Artifact is a hosted page a human
  can open without touching git at all. The Codex adapter, which has no Artifact equivalent, still
  falls back to a local file — but a gitignored one under `tmp/`, never committed.
- **Update the roadmap item's own `Progress` section on every step, instead of a separate page.**
  Rejected: `Progress` is a per-PR log of what actually shipped
  ([`docs/ai-development.md`](../../docs/ai-development.md)), not a step-by-step session trace.
  Conflating the two would make the roadmap item noisy and would touch a product-tracked file on
  every minor step instead of only when something ships.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Shared workflow (`.agent-workflows/be-progress-tracker/workflow.md`)
- [x] Claude adapter with `model: haiku` (`.claude/skills/be-progress-tracker/SKILL.md`)
- [x] Codex adapter with a local-file fallback (`.agent-hosts/codex/skills/be-progress-tracker/SKILL.md`)
- [x] Checkpoint wiring in `implement-be`, `ideation`, and `propose-and-build`'s shared workflows
- [x] Claude-adapter dispatch instructions (`model: "haiku"` via the Agent tool) for `implement-be`, `ideation`, and `propose-and-build`

Authored and implemented together in one change.

## References

- [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) — the
  task→model/effort convention this item reapplies to a new, narrower job.
- [BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md) — precedent for
  dispatching distinct roles to distinct models through the Agent tool.
- [`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md) — the existing `model: haiku`
  skill this item's Claude adapter follows.
- [`implement-be`](../../.agent-workflows/implement-be/workflow.md),
  [`ideation`](../../.agent-workflows/ideation/workflow.md),
  [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md) — the three workflows
  that call this skill.
