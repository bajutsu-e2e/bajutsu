**English** · [日本語](BE-XXXX-fix-issue-skill-ja.md)

# BE-XXXX — A fix-issue skill ships small GitHub issues without a roadmap item

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-fix-issue-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds `fix-issue`, a skill that ships a plain GitHub issue end to end: implementation,
review, the gate, and a Draft pull request (PR). A plain issue is a small bug, a papercut, or a
scoped improvement that never warranted a roadmap (Bajutsu Evolution, BE) item. The skill touches
no roadmap file and needs no new label. It plays the same role for a bare issue that
[`implement-be`](../../.agent-workflows/implement-be/workflow.md) plays for a numbered BE item, and
it reuses that skill's implementation, review, and follow-up steps directly. It diverges where a
plain issue genuinely differs from a BE item: how the skill claims ownership, and what closes the
loop once the fix merges.

## Motivation

A contributor can already file a lightweight bug or improvement without writing a BE proposal. The
`bug` and `enhancement` issue templates
([`bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml),
[`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml)) exist for that case, and
their `config.yml` already tells a filer to reach for the roadmap when an idea is large. The
[`task-select`](../../.agent-workflows/task-select/workflow.md) skill already surveys
these open issues alongside the roadmap and ranks them as candidates for the next task.

Nothing ships the candidate once someone picks it, though. `implement-be` turns a plan into a
merged fix, but every one of its steps assumes a BE file exists. It reads a `Status` field, claims a
bot-managed tracking issue (BE-0109), flips that `Status` to `Implemented`, and prefixes the PR title
with `[BE-NNNN]`. A one-line bug fix has none of that scaffolding available. A session
that picks up a plain issue today faces two bad options: inflate the issue into a BE proposal it
does not need, or reproduce `implement-be`'s branch, review, and gate discipline from memory. Two
sessions doing the latter have no guarantee they do it the same way.

A natural first fix is a new label. A maintainer would apply it after judging an issue small
enough to skip a BE item, the way [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
already labels roadmap-tracking issues. This item skips that label on purpose. The boundary between
"ship it as a plain fix" and "this needs a proposal" is a judgment call. That call can surface
mid-fix instead of at filing time alone, so the skill itself makes it.
[`ideation`](../../.agent-workflows/ideation/workflow.md) already judges the opposite boundary this
way: it redirects a user who wants to build, rather than propose, without leaning on a label to
draw that line.

## Detailed design

1. **No new label; the skill judges scope-fit and escalates when a fix does not fit.** Before
   planning a fix, `fix-issue` checks the issue's scope against a short bar: one clear cause, a
   localized change, no new user-facing behavior or configuration surface, and no tension with the
   [three prime directives](../../CLAUDE.md#prime-directives-do-not-violate). When a fix turns out
   to need a design decision, a cross-cutting change, or a reshaping of an idea that brushes a
   directive, the skill stops. It tells the user why, and points at
   [`ideation`](../../.agent-workflows/ideation/workflow.md) or
   [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md) rather than
   continuing. This valve can trigger at any point through step 3's planning stage, because a fix's
   true shape does not always show in the issue body alone.
2. **Ownership through the issue's own assignee field, not a bot-managed tracking issue.** BE-0109
   never syncs a plain issue, so `fix-issue` checks and claims ownership on the issue directly:
   `gh issue view <N> --json assignees` first, then `gh issue edit <N> --add-assignee @me` when the
   issue carries no assignee or already carries the current user. An issue already assigned to
   someone else stops the skill. `implement-be` applies that same rule to a BE tracking issue.
3. **The ship-the-code steps reuse `implement-be` almost word for word.** The skill grounds itself in
   the linked code and tests, opens a one-topic branch (`claude/fix-issue-<N>-<slug>`), and drafts a
   short plan: the files it will touch, the machine-checkable outcome, and the tests to add. It
   confirms that plan with the user before writing code. Implementation matches the codebase's
   grain, then a two-role self-review checks the diff against
   [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)
   ([BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md)). `make check`
   — never an AI call — is the sole verdict on the [Tier-2 gate](../../docs/glossary.md#the-two-tiers).
   None of these steps need re-specifying here; `fix-issue` points straight at `implement-be`'s steps.
4. **What differs from a BE-item PR.** There is no `Status` field to flip, so `implement-be`'s
   roadmap-promotion step has no counterpart here. The PR title keeps the plain scoped subject with
   no `[BE-NNNN]` prefix, matching the shape
   [`docs/ai-development.md`](../../docs/ai-development.md) already documents for a PR with no
   roadmap item. Its body adds `Closes #<N>`, so merging the PR closes the source issue on its own.
   The Draft PR still opens on its own once the self-review and the gate both come back clean
   ([BE-0230](../BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md)).
   The same bounded `pr-followup` loop then drives it to quiet-and-green, using the same stop
   conditions, escalation rules, and iteration caps `implement-be` already uses.
5. **New skill files.** A host-neutral `.agent-workflows/fix-issue/workflow.md` carries the steps
   above. A Claude Code adapter, `.claude/skills/fix-issue/SKILL.md`, declares `model: opus` in its
   frontmatter — the same Heavy tier as `implement-be`, since both ship product code. A Codex
   adapter, `.agent-hosts/codex/skills/fix-issue/SKILL.md`, keeps the host parity every existing
   skill has today ([`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout)).
6. **Documentation and hand-off wiring.**
   - [`docs/ai-development.md`](../../docs/ai-development.md) (and its `docs/ja/` mirror) gains a
     `fix-issue` → `opus` (Heavy) line in the per-skill model list, next to `propose-and-build`'s
     entry and for the same reason. It also gains a short note beside "Authoring and shipping
     roadmap items: the three skills," pointing at `fix-issue` as the sibling path for work that
     never receives a BE id.
   - [`CLAUDE.md`](../../CLAUDE.md)'s "Who opens the PR depends on the work (BE-0230)" bullet names
     `fix-issue` alongside `implement-be` under "Implementation work." A `fix-issue` PR is the same
     self-contained, gate-green change that bullet already auto-opens as Draft and drives with the
     paced follow-up loop.
   - [`task-select`](../../.agent-workflows/task-select/workflow.md) step 5's recommended next
     command becomes `fix-issue #<N>` when the chosen candidate is a bare GitHub issue with no BE
     id. Its existing `implement-be BE-NNNN` recommendation stays for a roadmap item.

## Alternatives considered

- **A new `quick-fix` label, applied once a maintainer judges an issue small enough to skip a BE
  item.** Rejected. The label would need its own maintained taxonomy: who applies it, whether it
  needs a sync workflow the way BE-0109's `roadmap-tracking` label does, and what happens when
  someone applies it to the wrong issue. The skill can already draw this distinction by reading the
  issue itself, and a static label fixed at filing time cannot see a fix turn out to need a design
  call mid-investigation. Design point 1's escalation valve can.
- **Repurposing the existing `good first issue` label.** Rejected. That label already carries an
  established, human-facing meaning: approachable for a newcomer. Overloading it with an "AI-ready"
  sense would make the two meanings hard to tell apart on sight.
- **Extending `implement-be` to accept a bare issue number.** Rejected. Every step of that skill
  assumes a BE file exists: the `Status` metadata, the `Implementing PR` row, the `[BE-NNNN]`
  prefix. Accepting an issue would mean branching around each of those instead of reusing them,
  adding conditional complexity to a skill whose contract is otherwise unconditional. A smaller
  sibling skill keeps both contracts easy to follow.
- **Leaving the work to ad hoc sessions, as today.** Rejected. A session reproduces `implement-be`'s
  branch, review, and gate discipline from memory each time, with no guaranteed consistency. It also
  does not compose with `task-select`, which already surfaces plain issues as candidates but has
  nothing to hand off to once someone picks one.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1. Scope-fit judgment in place of a label, including the escalation valve to `ideation` /
  `propose-and-build`.
- [ ] 2. Ownership check and claim through the issue's native assignee field.
- [ ] 3. Ship-the-code steps reused from `implement-be` (branch, plan confirmation, implementation,
  two-role self-review, the gate).
- [ ] 4. The differences from a BE-item PR: no `Status` flip, no `[BE-NNNN]` prefix, `Closes #<N>`
  in the body, the same auto-opened Draft PR and bounded `pr-followup` loop.
- [ ] 5. New skill files: the host-neutral workflow, the Claude Code adapter, and the Codex adapter.
- [ ] 6. Documentation wiring: `docs/ai-development.md` (+ ja), `CLAUDE.md`, and `task-select`.

## References

- [`implement-be`](../../.agent-workflows/implement-be/workflow.md) — the BE-item counterpart this
  skill mirrors for a plain issue, and the source of the steps it reuses unmodified.
- [`ideation`](../../.agent-workflows/ideation/workflow.md) — the skill this one's escalation valve
  points to, and the precedent for judging a scope boundary inside a skill rather than with a label.
- [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md) — the other escalation
  target, for a small idea whose design is already settled enough to author and build in one PR.
- [`task-select`](../../.agent-workflows/task-select/workflow.md) — the read-only skill that already
  ranks plain GitHub issues as candidates, and gains the hand-off to `fix-issue` in design point 6.
- [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) — GitHub Issues as
  the ownership tracker for open roadmap items, and the bot-managed mechanism this item's issues stay
  outside, since they are never tied to a BE id.
- [BE-0230](../BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md) —
  the auto-opened Draft PR and bounded `pr-followup` loop this item reuses for a plain-issue fix.
- [BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md) — the two-role,
  two-model local self-review this item's implementation step runs before opening the PR.
- [`.github/ISSUE_TEMPLATE/bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml) and
  [`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml) — the existing intake
  templates this item ships fixes for, unchanged.
- [`docs/glossary.md`](../../docs/glossary.md#the-two-tiers) — the Tier-2 gate this item's
  implementation step defers to as the sole pass/fail authority.
