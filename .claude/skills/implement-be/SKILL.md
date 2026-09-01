---
name: implement-be
model: opus
description: Implement an existing numbered Bajutsu Evolution roadmap item end to end, including tests, roadmap status, verification, a Draft PR, and follow-up.
---

# Implement a BE item

Take one roadmap (BE) item from its proposal to shipped, green code. You are the
**implementer**; the deterministic gate (`make check`) is the judge — never an LLM. The
proposal's **Detailed design** is your spec. Converse in the user's language; write code,
commits, and PR text per the conventions below.

This is the counterpart to [`ideation`](../../../.apm/skills/ideation/SKILL.md): that skill *authors* a BE
proposal, this one *ships* it. When the author is confident enough to write the proposal and
its implementation at once, [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) runs both in
one BE-creation PR, reusing this skill's steps 3–9 against the still-placeholder item (CI
allocates the id when the PR merges).

## Prime directives (these bound every line you write)

Re-read [`AGENTS.md`](../../../AGENTS.md) and the source of truth it names before you touch code.
[`DESIGN.md`](../../../DESIGN.md) runs to 54K, so reach into it in stages
(`make repo-map ARGS="--headings DESIGN.md"`, then the sections your item cites) rather than loading
it whole — the three directives are restated below in full so the common case needs no such read.
The implementation must honor them,
and you must stop and flag — not silently work around — anything that brushes a boundary:

1. **AI authors and investigates, never judges.** Never add an LLM call to the Tier‑2
   `run`/CI gate. Pass/fail comes only from machine-checkable assertions. AI belongs in
   `record` / `triage` / draft paths. If the item's design seems to need a model in the
   verdict, you've misread it — re-read, then ask.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
   fails immediately rather than tapping the first match.
3. **App-agnostic.** Per-app differences live in config (`targets.<name>`); the tool,
   drivers, and runner stay unchanged across targets.

## Workflow

Alongside the steps below, keep [`be-progress-tracker`](../../../.apm/skills/be-progress-tracker/SKILL.md)
current, invoking it through the Agent tool with `model: "haiku"` passed explicitly — a subagent
call does not inherit that skill's own frontmatter model. Check in at minimum after step 1 (the item's overview
is now known), step 5 (the plan is confirmed), step 6 (code is written), step 7 (the review pass
comes back clean), step 9 (the gate is green), step 10 (the PR is open), and each step 12
follow-up iteration. It only turns decisions this workflow already made into a glanceable status
page for the human watching the session — never let it gate or slow this workflow down; skip a
checkpoint rather than block on it. **Without an Agent tool** (a subagent running this skill has
none) that dispatch is impossible: invoke the skill inline if the checkpoint is cheap, otherwise
skip the tracker entirely and say so once in your report. Its absence never blocks the work.

### 1. Resolve the item

Accept any of: a full ID (`BE-0066`), a bare number (`66` / `0066`), a slug fragment, or a plain
description carrying no id at all. Locate the directory (every item lives at a permanent flat
`roadmaps/BE-NNNN-<slug>/` path — BE-0159; there are no `proposals/` / `implemented/`
subdirectories):

```bash
ls -d roadmaps/BE-*<id-or-slug>*/
```

Read the **English** `BE-NNNN-<slug>.md`. It is the authoritative spec, and per
[`CLAUDE.md`](../../../CLAUDE.md) the `-ja.md` mirror holds nothing the English file lacks, so
don't spend a read on the mirror to understand the item — you still *edit* both at step 8.

**When more than one directory matches, read them all before choosing, and tell the user which you
picked.** A description-only request is the dangerous case: the item whose title restates the
request most literally is often the dead one, superseded by a live successor phrased differently.
So whenever an item is `Rejected` or `Deferred`, read its `Superseded by` and `Related` rows before
reporting — that row, not the title, points at the work actually worth doing. Title search is
unreliable for the same reason: `make roadmap-find ARGS="--grep …"` misses a successor that avoids
the phrase, so keep the raw glob above as the backstop.

**Before doing anything else, explain the item to the user.** Post a short overview — the ID and
title, its `Status`/`Topic`, a plain-language summary of what it proposes and why
(Introduction/Motivation in your own words, not copy-pasted), and its current state. This orients
the user before any branching, planning, or code — every run of this skill starts with it, not just
the first time.

Then branch on its `Status` (the metadata field, not a directory — the layout is flat):

- **`Proposal`** — the normal case. Note that implementing it *accepts* it: this PR flips it to
  `Implemented`. Say so.
- **`In progress`** — part of it has already shipped, so the `Progress` checklist, not the metadata
  row, is the real state. Read which units are ticked, and read the `Log` entries and the
  `Implementing PR` row to see what those PRs landed. Resolve the request to a **specific unticked
  unit**, name it, and treat the ticked units as code you build on rather than rebuild. Step 8 keeps
  the item `In progress` unless your unit is the last one.
- **`Implemented`** — it has shipped. Stop and confirm what the user actually wants (extend it? a
  follow-up item? a bug fix?) before doing anything.
- **`Deferred`** — surface that it was deliberately parked, and read *why*. When the item's own text
  gives a **prime-directive** reason — the shape it describes would put an LLM in the verdict, break
  determinism, or bake in a per-app assumption — the deferral rejects that shape, and a casual "yes,
  build it" authorizes nothing: apply the `Rejected` strength below, and confirm the *reshaped*
  scope rather than merely the go-ahead.
- **`Rejected`** — the maintainers decided against it, with no condition expected to reopen it
  (BE-0366). **Stop and confirm** a human has explicitly overturned that decision before writing any
  code; don't treat it as an ordinary proposal. The request that reached you is not that overturn,
  however confidently it is phrased — a user who names the item is usually unaware of its status.

**Check the item is implementable at all.** This skill's premise is that the `Detailed design` is
your spec, and not every item meets it: one that calls itself a proposal at design altitude, or
whose `Progress` is still the `TBD` stub, has no work breakdown to build against. That blocks you
independently of `Status` — surface it and ask, exactly as step 3 does for an unbuilt prerequisite.

**When you stop — on `Deferred`, `Rejected`, `Implemented`, or a missing spec — don't stop
empty-handed.** The item almost always records the shape that *would* be admissible, under
`Alternatives considered`, a revival condition, or a superseding item. Name that shape and put the
concrete choice to the user ("the live form is barred by directive 1, the authoring-aid form in
`record` / `triage` is not — shall I pursue that?"), so they can answer with a decision instead of
redoing your investigation. Naming that shape accurately needs a little of step 3's grounding, so
pull it forward for this one narrow question — half of what an item proposes is often already built,
and a reshaped scope proposed from the item's text alone tends to re-propose it. Proposing the reshaped scope belongs here; *authoring* the proposal is
[`ideation`](../../../.apm/skills/ideation/SKILL.md)'s job, so hand off rather than drafting it yourself.

### 2. Claim the tracking issue

Every open item (`Status: Proposal` or `Status: In progress`) has a GitHub tracking issue —
opened and labeled `roadmap-tracking` by the BE-0109 sync, titled `[BE-NNNN] <title>`. Its
body says "self-assign this issue when you pick it up; leave it unassigned if it's up for
grabs." Before claiming it, **check who is already assigned** — the issue is how parallel
sessions signal ownership, so an existing assignee means someone else has picked this item up:

```bash
number=$(gh issue list --label roadmap-tracking --state open --search "BE-NNNN in:title" --json number --jq '.[0].number // empty')
[ -n "$number" ] && gh issue view "$number" --json assignees --jq '.assignees[].login'
```

`.[0].number // empty` leaves `number` empty (not `null`) when no issue matches, so the guarded
`gh issue view` simply doesn't run — fall through to the "no matching open issue" note below.

- **Someone else is already assigned** (a login that isn't the account `gh` is authenticated
  as — check with `gh api user --jq .login`): **stop.** Tell the user the item is already
  claimed, name the assignee, and don't branch, plan, or write any code. Let the user decide
  whether to coordinate with that person, pick a different item, or override deliberately.
  Only continue if the user explicitly tells you to proceed anyway.
- **Unassigned, or already assigned to you** — claim it (idempotent) and continue:
  ```bash
  gh issue edit "$number" --add-assignee @me
  ```

`--add-assignee @me` assigns the human account `gh` is authenticated as — the same account
commits and PRs are attributed to. It's idempotent (re-assigning yourself is a no-op), so
running it again on a resumed session is harmless. If no matching open issue turns up (the
sync lags `main` by one run, or you're reviving a `Deferred` / `Rejected` item per step 1),
don't block on it — note it and continue.

**If `gh` itself is missing or unauthenticated**, reach for whatever GitHub client this host does
give you — a web session ships GitHub MCP tools and no `gh` at all — and say which you used. Don't
collapse "I could not look" into "no issue found": the assignee check is a *stop* condition, so a
lookup you couldn't perform means ownership is **unknown**, not that the item is free. With no
GitHub access at all, say so in your overview and continue, having told the user they now carry the
collision risk.

### 3. Ground yourself in the spec and the code

Don't start typing from the title. Build the real picture first:

- Read the proposal's **Detailed design** and **Alternatives considered** closely — the
  latter records paths already rejected (often for prime-directive reasons); don't
  re-propose them.
- Open **every file the proposal links** (proposals here reference their touch-points
  heavily) and read the surrounding code, so your change matches what exists.
- Check what already exists, so you neither rebuild something shipped nor assume something
  absent. `make repo-map ARGS="--headings docs/architecture.md"` names the groups under
  **Implemented**; read the one covering your area rather than the whole section.
- **Check dependencies.** If the References / design lean on another BE item, verify that
  item's status with `make roadmap-find ARGS="--id BE-NNNN"`, which answers in one row instead
  of opening the item. A prerequisite still at `Status: Proposal` is a blocker — surface it and
  ask how to proceed (build the prerequisite first? a thinner first slice?). For an `In progress`
  item the prerequisite is often **another unit of this same item**, whose state lives in the
  `Progress` checklist rather than in any metadata row `roadmap-find` can answer — read the
  checklist and the log.
- **Assume the proposal has gone stale, and check.** It was written against an older tree, and
  items that landed in between move the ground under it: a renamed symbol or flag, a branch it
  calls unguarded that has since been guarded, a file that moved. Look up every symbol, flag, and
  file the Detailed design names and confirm it still exists as described. Where it doesn't,
  build the proposal's *intent* against today's code and record each deviation in the step 5 plan.
  Implementing the stale letter produces a change that contradicts `main`; silently papering over
  it hides a spec bug the item's author should see.

For a large item, fan out discovery across `Explore` subagents and keep only their synthesis in
the main thread. Draft the implementation strategy with the `Plan` agent.

### 4. Set up a focused workspace

Follow the parallel-work rules ([`docs/ai-development.md`](../../../docs/ai-development.md)):

- **One topic per branch.** If you're on `main`, branch off the *latest* origin first:
  `git fetch origin && git switch -c claude/be-NNNN-<slug> origin/main`. If the session is
  already on a dedicated branch / worktree, stay there.
- **Stay in your lane.** Touch only the files this item needs. If the design forces a
  cross-cutting change (e.g. a driver-API change), say so up front.

### 5. Plan, then confirm before writing code

Implementing a whole roadmap item is large and hard to reverse, so **get the user's
go-ahead on a concrete plan first**. The plan should name:

- the files you'll add/change and the shape of the change;
- the **machine-checkable outcome** that proves it works (the assertion / behavior a
  deterministic `run` or unit test will check) — and explicitly, where AI is and isn't
  allowed to sit;
- the tests you'll add or change;
- any docs that must move (and therefore need both languages);
- any tension with the prime directives, and how you've reshaped the design to fit (the
  way `ideation` reshapes a conflicting idea rather than dropping it).

Only implement once the user is happy with the plan.

**If no human is in the turn** — steps 10–12 run unattended, and a session can be launched that way
from the start — neither proceed on an approval nobody gave nor stall indefinitely: post the plan,
say plainly that it is unapproved, and stop there. A whole roadmap item is too large and too costly
to reverse to build on an assumed yes.

### 6. Implement

Build to the Detailed design, matching the codebase's grain:

- **Match surrounding style.** Comments explain **why**, not what, at the surrounding
  density — no narration. `mypy` is **strict** and `ruff` is configured in
  [`pyproject.toml`](../../../pyproject.toml); fullwidth/Japanese strings are intentional.
- **Honor the directives in the code itself** — determinism (condition waits, no `sleep`;
  ambiguous selectors fail), app-agnostic (new knobs go in `targets.<name>` config), and no
  LLM anywhere on the `run`/CI path.
- **Tests are the regression net.** If you change behavior, a test changes with it. The
  Python core needs no Simulator, so cover the logic in the fast suite.
- **A consequence the design implies but doesn't enumerate is in scope.** Retiring a flag breaks
  the code that renders it; deleting the last model call leaves a usage ledger dead. Name such
  fallout in the plan rather than silently widening the change or silently skipping it — "stay in
  your lane" bounds the *feature*, not the change's own consequences.
- **Docs are bilingual.** If you change a *documented* behavior, update `docs/` **and** its
  `docs/ja/` mirror. The documented surface is usually wider than the design's list, so grep
  `docs/` for the behavior's own vocabulary (the flag, the field, the error text) to find every page
  now asserting something false, rather than trusting the item to have enumerated them. Write the Japanese side under the
  [`japanese-document-writing`](../../../.apm/skills/japanese-document-writing/SKILL.md) skill — natural Japanese, not
  a literal rendering of the English.

### 7. Review and refine the diff

`make check` proves the change is green — it does **not** judge design, simplicity, or
logic. Close that gap on the diff you just wrote. Lean on the repository's own review contract
and this host's authoring aids. This stays inside directive #1: they advise the author and never
judge — the gate (step 9) is still the only verdict, and no LLM touches the `run`/CI path.

**Read [`references/self-review.md`](references/self-review.md) now and follow it.** It holds the
two-role procedure (a judge-only review/plan pass, then an implement pass), the two inputs that
differ from `ideation`'s, the specialized review lenses, and the loop's 3-round cap.

If this host offers no Agent tool and no `pr-review-toolkit` — a subagent running this skill has
neither — apply the review contract to the diff yourself and record in your report that the pass ran
single-handed. A degraded review beats a skipped one.

**Don't open the PR (step 10) until that pass is clear.**

### 8. Record the item's new state

The implementing PR is what ships the item, so record its new state in this same change. **What you
set depends on whether your change completes the item's work breakdown**, because most items ship in
several units across several PRs — BE-0365, BE-0339, and BE-0381 each did:

- **Every `Progress` box is now ticked** — set `Status` to **Implemented**.
- **Units remain unticked** — leave `Status` at **In progress** (promote it from `Proposal` to that
  if it was still a proposal). Announcing `Implemented` while units 4 and 5 are open misreports the
  item on the [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html), which
  reads the bucket straight from this field.

Either way, in **both** language files: tick the `Progress` box for the unit you landed, append a
`Log` entry saying what this change did, and **append** your PR to the `Implementing PR` row rather
than overwriting it — the row accumulates, as in `[#1699](…) (unit 1), [#1788](…) (unit 2)`. That
row goes directly **after `Tracking issue`**, the slot
[`scripts/check_roadmap_format.py`](../../../scripts/check_roadmap_format.py) enforces; put it
anywhere else and `make check` fails on metadata field order. It can only be filled once the PR
number exists, so the ordinary path adds it at step 10 and the item legitimately carries a status
with no PR row between steps 8 and 10.

**The Japanese mirror uses Japanese field names and values**, so translate instead of copying the
English across: `Status` → `状態`, `Proposal` → `提案`, `In progress` → `実装中`, `Implemented` →
`実装済み`, `Tracking issue` → `トラッキング Issue`, `Implementing PR` → `実装 PR`, `Progress` →
`進捗`, `Unit N` → `単位 N`. Write the log entry as natural Japanese under the
[`japanese-document-writing`](../../../.apm/skills/japanese-document-writing/SKILL.md) skill, not as a literal
rendering of the English one.

Nothing else to regenerate: the item's directory never moves (BE-0159), so `Status` decides only the
dashboard bucket, read straight from the metadata you just edited. **Never renumber the item**; its
ID is permanent.

### 9. Verify — the gate

```bash
make check    # format-check + the lint suite (lint, docstrings, imports, sh, actions, js,
              #   roadmap, skills, module-map, secrets) + lock-check + typecheck + test
              #   + lint-coverage-floors
```

Two of those steps bite most often, and knowing them beforehand is cheaper than reading the
failure. `lint-roadmap` checks the metadata field order step 8 just touched. `lint-coverage-floors`
fails when a file drops below its own recorded floor
([`coverage-floors.json`](../../../coverage-floors.json), BE-0385); the fix is normally a real test,
not a rewritten snapshot, so reach for `make coverage-floors` only when the drop is one you can
defend — a change that deletes covered lines genuinely reweights a file's remaining branches — and
say so in the PR body.

It must be green; **never push red** (the tracked pre-push hook runs it for you). It needs
no Simulator and runs anywhere. On-device E2E (`make -C demos/showcase run-swiftui`) is a separate,
heavier path and is **not** part of this gate — don't block core work on it. But if the
item's correctness genuinely depends on a Simulator or browser run, drive it yourself — the iOS
Simulator tools for a Simulator run, the Browser pane for a web one — and report what you saw,
rather than claiming it works untested.

### 10. Auto-open a Draft PR

Once step 7's review pass is clear, step 9's `make check` is green, and the branch is pushed,
**open the PR yourself** — this skill's output is always a self-contained, gate-green change with
every review finding resolved, so there is no reason to wait for a human to open it. This is the
*one* skill that auto-opens: the BE-*authoring* skills
([`ideation`](../../../.apm/skills/ideation/SKILL.md), the proposal phase of
[`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md)) never do, because a proposal PR is a human
checkpoint whose id is allocated only on merge (see [`AGENTS.md`](../../../AGENTS.md) and the
source of truth it names).

- **Draft by default:** `gh pr create --draft`. **Title and body in English**, always — prefix the
  title with the ID (`[BE-NNNN] feat(<scope>): …`), write a thorough body from
  [the template](../../../.github/PULL_REQUEST_TEMPLATE.md), and close with the `make check`
  verification line. Commits are imperative and scoped (`feat(run): …`).
- **Doc-only exception:** if this item's change is purely documentation/prose (skills, agent
  guidance,
  roadmap `*.md`/`*-ja.md` — no product code under `bajutsu/` / `BajutsuKit/` / runner / drivers),
  the repository guidance's "documentation-only PRs open Ready for review" rule takes precedence: open it
  **Ready** (omit `--draft`) with `--reviewer bajutsu-e2e/steering-committee`. (BE-0230 itself is
  exactly this case.)
- Then **fill the `Implementing PR:` row** in both BE files with the real number and push that
  follow-up, so the shipped record points at its PR.
- The Draft + never-mark-ready-while-red rules from the repository guidance still hold: a Draft PR is only
  marked ready (`gh pr ready`) by the **human**, never automatically while CI is red.

### 11. Keep follow-up work lean

The implementation transcript is dead weight for CI and review follow-up. Run token-heavy
follow-up work (reading CI logs, diffs, and review comments, and making fixes) in a **fresh
Agent-tool subagent** for each iteration. Keep only the short structured summary in the main
thread.

### 12. Run bounded PR follow-up

Pace the iterations with `/loop`: a **short** interval while CI is actively running and a **longer**
interval while waiting on human review. Each iteration does three things:

1. **Check for a merge conflict**, because today's `pr-followup` does not query `mergeable`:
   ```bash
   gh pr view <PR> --json mergeable --jq .mergeable
   ```
   - `CONFLICTING` → **stop and escalate** immediately (don't spawn the subagent). `pr-followup`
     never rebases or force-pushes; the human rebases and resolves, then restarts the loop.
   - `UNKNOWN` → GitHub is still computing mergeability (e.g. right after a push); treat as "no
     conflict yet", proceed, and re-check next iteration.
   - `MERGEABLE` → proceed.
2. **Start a fresh Agent-tool subagent** and give it
   [`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md)'s steps directly, **stating that no human is in the
   turn** — the loop runs unattended, and only this layer knows that, so
   [`record-issue`](../../../.apm/skills/record-issue/SKILL.md) would otherwise wait on an approval nobody is there to
   give. Its task for
   the PR: assess CI and review comments, make targeted fixes, self-review the fix against the CI
   review contract, run `make check`, push, request the live review on demand, reply to and resolve
   threads, and **return a short structured summary** — what it changed, whether it pushed, whether
   its self-review came back clean and whether it therefore requested a live review, the resulting
   CI/review state, any **pending draft** the `record-issue` sub-step returned, and whether it hit
   one of `pr-followup`'s escalations. A pending draft sits in a field of its own, never in the
   escalation field, so an out-of-scope note never stops the loop. The
   fresh context is what keeps the implement transcript out of the expensive work (step 11).
3. **Read the summary and evaluate the stop conditions.** The loop layer owns the
   conflict / `CHANGES_REQUESTED` checks and the counters; `pr-followup` itself is unchanged.

**Read [`references/pr-followup-loop.md`](references/pr-followup-loop.md) before the first
iteration.** It holds the three stop conditions, the escalations, the two iteration backstops that
decide when this loop ends, and the rule for carrying the iterations' pending drafts into the loop's
final report. The loop never marks the PR ready — that sign-off is the human's.

## If you stop early

Steps 1, 3, and 5 can each end a run without code, and that is an outcome, not a failure. Leave the
worktree and branch in place so the user can resume, flip no roadmap metadata, and hand the tracking
issue back (un-assign it) if step 2 claimed it and you are releasing the item. Report the question
you need answered together with the shape you would propose (step 1), so the reply can be a decision
rather than a repeat of your investigation.

## References

- [`AGENTS.md`](../../../AGENTS.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime
  directives every change must honor.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — parallel-work rules, the
  gate, and the strict BE-ID lifecycle (Status ⇒ dashboard bucket, flat one-directory layout, permanent IDs).
- [`roadmaps/README.md`](../../../roadmaps/README.md) — how to add a roadmap item and the per-item format.
- [`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md) — the skill steps 11–12 loop over: after this skill
  opens the Draft PR, bounded follow-up drives the mechanical tail (CI fixes, review replies) to
  quiet-and-green, running each iteration's `pr-followup` in a fresh subagent, so implement → PR →
  followup is one automated flow.
- [`ideation`](../../../.apm/skills/ideation/SKILL.md) — the upstream skill that authors the proposal this one builds.
- [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) — composes `ideation` + this skill for a
  small, settled item: author the proposal and implement it together in one BE-creation PR,
  reusing this skill's steps 3–9 against the still-placeholder item (CI allocates the id on merge).
- [`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) — the review
  contract step 7 applies to the diff, alongside the `pr-review-toolkit` lenses it names. They
  advise the author; only `make check` judges.
- [`references/self-review.md`](references/self-review.md) and
  [`references/pr-followup-loop.md`](references/pr-followup-loop.md) — the depth behind steps 7
  and 12, loaded at those steps rather than up front.
