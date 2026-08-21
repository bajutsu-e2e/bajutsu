**English** · [日本語](BE-XXXX-record-issue-skill-ja.md)

# BE-XXXX — Ship a record-issue skill that files minor bugs and improvements as GitHub Issues

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-record-issue-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item proposes `record-issue`, a skill that turns a minor bug or a small, bounded
improvement noticed during work into a GitHub Issue. It classifies the finding, searches for a
duplicate, drafts a title and body from this repository's own issue templates, and — only after
the invoker gives explicit approval — files the issue with `gh issue create`. The shared procedure
lives in one workflow document so it works two ways: as a standalone skill a person invokes
directly, and as a sub-step another skill (for example `pr-followup`) calls when it notices
something worth flagging but out of the current change's scope. A judge-only skill such as
[`claude-review`](../../.agent-workflows/claude-review/workflow.md) never calls it — that skill
edits and files nothing — so its findings reach `record-issue` only through whatever invoked it.

## Motivation

Bajutsu already draws a line between two weights of idea. A substantial feature goes through the
roadmap (BE) process: [`ideation`](../../.agent-workflows/ideation/workflow.md) drafts a scoped
proposal with a Motivation and a Detailed design, and a human merges it before CI allocates a real
BE ID. A minor defect or a small improvement does not need any of that weight —
[`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml) already says as much:
"Use this issue for a lightweight request or to start the conversation," pointing away from the
roadmap process for anything short of a well-formed design.

Nothing today operationalizes that lightweight side. A contributor or an agent who notices a small
defect while working on something unrelated faces three bad options: fix it inline and enlarge the
current change's scope, keep going and lose track of it, or stop to write an issue by hand — recall
which template applies, search for a duplicate, and phrase the report well. That last option is
correct but has enough friction that a minor finding is more often dropped than filed.

Claude Code's own harness already has a shape for the first half of this problem: the `spawn_task`
tool lets a session flag an out-of-scope finding as a chip the user can act on later, without
derailing the current turn. That chip is ephemeral and scoped to one session — it does not survive
past the session, is not visible to the rest of the team, and is not a GitHub Issue, so
[`task-select`](../../.agent-workflows/task-select/workflow.md) — which already treats open GitHub
Issues as one of its two candidate sources alongside the roadmap — cannot pick it up. `record-issue`
is the durable, repository-level counterpart: filing a persistent, team-visible issue that
`task-select` can surface to anyone, in a later session, on a different machine.

`record-issue` only ever files an issue; it never ships a fix.
[`fix-issue`](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md) is the consuming counterpart
that ships an implementation for an already-filed issue — typically after `task-select` surfaces it
as a candidate — so the two items never overlap in scope.

## Detailed design

**Skill layout.** Following the three-layer convention in
[`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout), the shared procedure lives in
`.agent-workflows/record-issue/workflow.md`; a Claude adapter at
`.claude/skills/record-issue/SKILL.md` and a Codex adapter at
`.agent-hosts/codex/skills/record-issue/SKILL.md` (with its `agents/openai.yaml` interface metadata,
which every existing Codex adapter carries) each load that shared procedure and add only their
host-specific invocation.

**Inputs.** A description of the finding — either typed directly by the invoker, or handed over by
a calling skill that spotted something out of its own scope — plus whatever supporting context is
available: a file and line, a command that reproduces it, or the environment it showed up in. A
calling skill also states whether a human is in the turn, because the skill cannot observe that
itself: the sub-step invocation carries the flag (`pr-followup` sets it when it runs inside
`implement-be`'s hands-free loop), and its absence means attended.

**Step 1 — classify.** Decide among three landings. `feature_request.yml` only points anything short
of a well-formed design away from the roadmap, so this skill states the concrete bar itself: does
this need a Detailed design, a discussion of trade-offs, or changes across multiple modules? If so,
this is not a minor finding —
stop and point the invoker to `ideation` (or, for a small item whose design is already settled,
`propose-and-build`) instead of filing an issue. Otherwise, classify the finding as a **bug**
(something behaves other than intended) or a lightweight **enhancement** (a small, bounded
improvement).

**Step 2 — search for a duplicate.** Run `gh issue list --search "<keywords>" --state all --limit
10` with keywords drawn from the finding, and show the invoker any candidate matches (number,
title, state). When a match looks like the same issue, ask whether to comment on the existing issue
instead of filing a new one, or to proceed anyway — the invoker decides, since only they can judge
whether the match is close enough.

**Step 3 — draft.** Read the matching template — `bug_report.yml` for a bug,
`feature_request.yml` for an enhancement — and synthesize a markdown body whose sections mirror
that template's fields. `gh issue create` posts plain markdown rather than rendering the template's
YAML form, so the skill fills those fields itself instead of relying on `gh` to do it. Pick the
matching label (`bug` or `enhancement`); no new label is needed; see *Alternatives considered*.

**Step 4 — confirm.** Show the invoker the full draft — title, body, and label — and wait for
explicit approval before creating anything. Filing a GitHub Issue publishes content the whole team
sees, so this step runs every time, not only the first time a session uses the skill.

**Step 5 — create and report.** On approval, run `gh issue create --title <title> --body-file
<file> --label <label>` — or, when step 2 picked an existing issue, `gh issue comment <number>
--body-file <file>`, under the same confirmation gate, since a comment publishes to the team just as
a new issue does — and report the resulting issue URL back to the invoker, or, when called as a
sub-step, back into the calling skill's own output, so a review or follow-up pass can list what it
filed alongside what it fixed inline.

Because the workflow document describes one procedure regardless of caller, a standalone
invocation and a call from another skill follow identical steps — including the confirmation
gate, which no calling skill may skip on the invoker's behalf. When no human is in the turn —
`pr-followup` running as one iteration of `implement-be`'s hands-free loop (BE-0230) is the case
that matters — the skill files nothing and stalls nothing: it returns the finished draft in the
iteration's structured summary, the channel `implement-be` step 12 already reads and
`pr-followup` step 4 already uses for a self-review-only finding, so the human sees it when the
loop reports and approves it on a later turn. A pending draft is deliberately not an escalation:
every entry in BE-0230's escalation list stops the loop and hands the PR to the human, which an
incidental out-of-scope note should never do to an otherwise-healthy follow-up loop.

**Caller and documentation wiring.** A calling skill only runs a sub-step its own workflow names —
`be-progress-tracker` runs because `ideation` and `implement-be` each name it — so `pr-followup`
(and any other calling skill) gains an explicit `record-issue` sub-step. The same change wires the
documentation: `docs/ai-development.md` (and its `docs/ja/` mirror) and `CLAUDE.md`, including the
Claude adapter's default `model:` tier (BE-0103).

## Alternatives considered

**File without confirmation.** Rejected: creating an issue is a publishing action visible to the
whole team, and skipping confirmation risks a noisy or misdiagnosed report reaching other
contributors before anyone reviews it.

**Extend `ideation` to also handle minor findings.** Rejected: `ideation`'s contract is authoring a
BE roadmap item — a placeholder ID, a Detailed design, the self-review pass against the CI review
contract. A minor finding needs none of that weight, and folding both into one skill would either
overload `ideation`'s procedure or leave it applied inconsistently across two very different sizes
of idea.

**Reuse `spawn_task`-style ephemeral chips instead of a GitHub Issue.** Rejected: a session-local
chip does not survive past the session, is not visible to the rest of the team, and is not a
candidate source `task-select` reads — so it does not close the gap this item targets.

**Add a new label to mark these issues as lightweight.** Considered, but the absence of the
`roadmap-tracking` label already distinguishes a `record-issue` filing from a BE-linked issue,
and the existing `bug` / `enhancement` labels already say what kind of finding it is. An added label
would add process without adding information.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Author `.agent-workflows/record-issue/workflow.md` (classify → duplicate search → draft →
      confirm → create).
- [ ] Add the Claude adapter `.claude/skills/record-issue/SKILL.md`.
- [ ] Add the Codex adapter `.agent-hosts/codex/skills/record-issue/SKILL.md`.
- [ ] Wire the callers: name the `record-issue` sub-step in `pr-followup` (and any other calling
      skill) the way `ideation` / `implement-be` name `be-progress-tracker`.
- [ ] Documentation wiring: `docs/ai-development.md` (+ ja) and `CLAUDE.md`, including the Claude
      adapter's default `model:` tier (BE-0103).
- [ ] Verify the standalone path and at least one calling-skill path (for example `pr-followup`
      flagging an out-of-scope finding) both exercise the confirmation gate, and that an
      unattended run (`pr-followup` inside `implement-be`'s hands-free loop) returns its draft
      in the iteration's structured summary instead of filing or escalating.

## References

- [`.github/ISSUE_TEMPLATE/bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml) and
  [`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml) — the templates this
  skill's drafts mirror.
- [`ideation`](../../.agent-workflows/ideation/workflow.md) and
  [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md) — the counterparts
  for an idea substantial enough to need a Detailed design.
- [`task-select`](../../.agent-workflows/task-select/workflow.md) — already surveys open GitHub
  Issues as a candidate source; the intended consumer of what this skill files.
- [BE-0380](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill.md) — the consuming counterpart that
  ships a fix for an issue this skill filed.
- [`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout) — the three-layer skill-authoring convention
  this item's layout follows.
