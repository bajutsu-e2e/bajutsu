**English** · [日本語](BE-0379-be-progress-tracker-ja.md)

# BE-0379 — Track BE work in a live progress Artifact via a dedicated low-cost skill

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0379](BE-0379-be-progress-tracker.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0379") |
| Implementing PR | [#1673](https://github.com/bajutsu-e2e/bajutsu/pull/1673) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds **`be-progress-tracker`**, a small dedicated skill. The skills
[`ideation`](../../.apm/skills/ideation/SKILL.md),
[`implement-be`](../../.apm/skills/implement-be/SKILL.md), and
[`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md) call it at their own step
boundaries. It keeps one live status page per BE item.
The page holds an overview, the implementation progress, and a work log. A human watching a long session can then see where an item stands without
reading the whole transcript.

The tracker is a **separate** skill, not logic folded into the three callers.
Its default model is the low-token `haiku`, so every checkpoint stays cheap.
That holds whichever model the calling workflow runs on.

## Motivation

An `implement-be` or `propose-and-build` session can run long. The session creates a branch, confirms a plan, and writes code.
It then runs review rounds and the gate, opens a PR, and runs a paced follow-up loop.
Today the only record of that progress is the session transcript, the eventual roadmap `Status` flip,
and the PR. No single, shareable place answers "where does this item stand
right now" without reading the whole conversation.

Two points shape the design.

1. **Reusing the calling skill's model would charge every checkpoint at that skill's rate.**
   `implement-be` defaults to `opus`. A status-page write transcribes a decision the calling skill
   has already made and needs no new judgment, so paying `opus` rates at every checkpoint would be
   wasteful. [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)
   already established a lightweight default `model:` for a mechanical skill (see the `haiku` in
   [`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md)). This item applies the same
   pattern to a new, narrower job.
2. **The three roadmap-authoring and implementing skills already share one checkpoint shape.** The
   checkpoints are a confirmed plan, a clean review pass, a green gate, and an opened PR. A single
   tracker skill, called identically from all three, keeps that logic in one place. Otherwise each
   of the three skills would grow its own status-formatting code.

## Detailed design

The work breaks down into five independent pieces.

1. **Shared workflow.** [`.apm/skills/be-progress-tracker/SKILL.md`](../../.apm/skills/be-progress-tracker/SKILL.md)
   defines the document's three fixed sections: Overview, Progress, and Work log. It also defines
   the checkpoint contract a caller must supply: the BE id, the calling workflow's name and step,
   and one work-log sentence. Its non-goals are:
   - It is never a source of truth. The roadmap item and the PR stay canonical.
   - It never blocks. A failed or unavailable checkpoint is noted, and the calling workflow
     continues.
   - It is read-only against everything except its own status page.
2. **Claude adapter.** [`.claude/skills/be-progress-tracker/SKILL.md`](../../.claude/skills/be-progress-tracker/SKILL.md)
   carries `model: haiku` in its frontmatter. It publishes the status page as a Markdown Artifact,
   one per BE item. Every later checkpoint redeploys the page to the same URL instead of
   republishing it from scratch.
3. **Codex adapter.** `.agent-hosts/codex/skills/be-progress-tracker/SKILL.md` has no hosted-Artifact
   equivalent to use. It writes the same three-section page to a local, gitignored file under
   `tmp/be-status/` and tells the user that path once.
   [BE-0390](../BE-0390-apm-skill-management/BE-0390-apm-skill-management.md) later retired the Codex
   tree and folded the Claude adapter into the single per-skill source.
4. **Checkpoint wiring in the three calling workflows.** `implement-be/workflow.md` and
   `ideation/workflow.md` each gain one short paragraph. The paragraph names which of the workflow's
   own numbered steps warrant a checkpoint. `propose-and-build/workflow.md` defines no checkpoints
   of its own. It notes that it inherits the checkpoints of the `ideation` and `implement-be` phases
   it delegates to, keyed on the same `BE-0379` placeholder it already carries through both phases.
5. **Claude-adapter dispatch instructions.** The `SKILL.md` of `implement-be`, `ideation`, and
   `propose-and-build` each gain one line. The line tells the host to dispatch the checkpoint
   through the Agent tool with `model: "haiku"` passed explicitly, because a subagent call does not
   inherit a skill's frontmatter model automatically. The two delegated phases of `propose-and-build`
   can inherit the checkpoints themselves (point 4), but not their dispatch. An adapter loads only
   its own shared workflow, never a sibling skill's adapter. The `propose-and-build` adapter
   therefore needs this line as well.

The item changes no product code. It is purely contributor-workflow tooling under the skill trees of
the day (`.agent-workflows/`, `.claude/skills/`, and `.agent-hosts/codex/skills/`). It touches no
`bajutsu/`, `BajutsuKit/`, or `run`/CI path. Prime directive 1 stays intact: the tracker never
appears near the deterministic gate, and it only formats decisions that a human-in-the-loop skill
has already made.

## Alternatives considered

- **Fold the tracker into each calling skill's own steps, instead of a separate skill.** Rejected.
  - The "format a short status page" logic would be duplicated three times.
  - Every checkpoint's cost would depend on the model the calling skill happens to run at (`opus`
    for `implement-be`), which defeats the point of cheap checkpoints.
- **Write the status to a file committed in the repository, instead of an Artifact.** Rejected for
  the Claude Code adapter.
  - A live, frequently rewritten scratch document needs a commit at every checkpoint. That makes
    the history noisy and races with the actual implementation commits.
  - Left untracked, the document is invisible to anyone outside the session.
  - An Artifact is a hosted page that a human can open without touching git.
  - The Codex adapter has no Artifact equivalent and still falls back to a local file. That file is
    gitignored under `tmp/` and never committed.
- **Update the roadmap item's own `Progress` section at every step, instead of a separate page.**
  Rejected. `Progress` is a per-PR log of what actually shipped
  ([`docs/ai-development.md`](../../docs/ai-development.md)), not a step-by-step session trace.
  Mixing the two would make the roadmap item noisy. It would also touch a product-tracked file at
  every minor step, not only when something ships.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Shared workflow (`.apm/skills/be-progress-tracker/SKILL.md`)
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
- [`implement-be`](../../.apm/skills/implement-be/SKILL.md),
  [`ideation`](../../.apm/skills/ideation/SKILL.md),
  [`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md) — the three workflows
  that call this skill.
