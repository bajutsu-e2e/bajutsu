---
name: implement-be
model: opus
description: >-
  Implement a Bajutsu roadmap (BE) item end to end, starting from its ID. Use when the
  user names a roadmap item to build — "/implement-be BE-0066", "implement BE-0041",
  "start on BE-0007", a bare number, or a slug — or otherwise asks to turn an existing BE
  proposal into shipped code. Treats the item's proposal as the spec, self-assigns the item's
  GitHub tracking issue, grounds the work in the prime directives, sets up a focused branch,
  plans and confirms before writing, implements with tests, reviews and refines the diff,
  flips the item to Implemented (Status + reindex), and proves the gate is
  green. The deterministic counterpart to the `ideation` skill: ideation authors proposals,
  this one ships them.
---

# Implement a BE item

Take one roadmap (BE) item from its proposal to shipped, green code. You are the
**implementer**; the deterministic gate (`make check`) is the judge — never an LLM. The
proposal's **Detailed design** is your spec. Converse in the user's language; write code,
commits, and PR text per the conventions below.

This is the counterpart to [`ideation`](../ideation/SKILL.md): that skill *authors* a BE
proposal, this one *ships* it. When the author is confident enough to write the proposal and
its implementation at once, [`propose-and-build`](../propose-and-build/SKILL.md) runs both in
parallel as a temporary two-PR stack and hands off to this skill's steps 8–10 once the id is
allocated.

## Prime directives (these bound every line you write)

Re-read [`CLAUDE.md`](../../../CLAUDE.md) and [`DESIGN.md`](../../../DESIGN.md) before you
touch code. The implementation must honor them, and you must stop and flag — not silently
work around — anything that brushes a boundary:

1. **AI authors and investigates, never judges.** Never add an LLM call to the Tier‑2
   `run`/CI gate. Pass/fail comes only from machine-checkable assertions. AI belongs in
   `record` / `triage` / draft paths. If the item's design seems to need a model in the
   verdict, you've misread it — re-read, then ask.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
   fails immediately rather than tapping the first match.
3. **App-agnostic.** Per-app differences live in config (`targets.<name>`); the tool,
   drivers, and runner stay unchanged across targets.

## Workflow

### 1. Resolve the item

Accept any of: a full ID (`BE-0066`), a bare number (`66` / `0066`), or a slug fragment.
Locate its directory (every item lives at a permanent flat `roadmaps/BE-NNNN-<slug>/`
path — BE-0159; there are no `proposals/` / `implemented/` subdirectories):

```bash
ls -d roadmaps/BE-*<id-or-slug>*/
```

Read **both** language files; the **English** `BE-NNNN-<slug>.md` is the authoritative
spec, the `-ja.md` mirror is supporting context.

**Before doing anything else, explain the item to the user.** Post a short overview —
the ID and title, its `Status`/`Topic`, a plain-language summary of what it proposes and
why (Introduction/Motivation in your own words, not copy-pasted), and its current state
(proposal / already implemented / deferred). This orients the user before any branching,
planning, or code — every run of this skill starts with it, not just the first time.

Then branch on its `Status` (the metadata field, not a directory — the layout is flat):

- **`Status: Proposal`** — the normal case. Note that implementing it *accepts* it: this PR
  flips it to `Implemented`. Say so.
- **`Status: Implemented`** — it has shipped. Stop and
  confirm what the user actually wants (extend it? a follow-up item? a bug fix?) before
  doing anything.
- **`Proposal (deferred)`** — surface that it was deliberately parked; confirm the user
  wants to un-defer and build it now.

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
sync lags `main` by one run, or you're un-deferring a `Proposal (deferred)` item per step 1),
don't block on it — note it and continue.

### 3. Ground yourself in the spec and the code

Don't start typing from the title. Build the real picture first:

- Read the proposal's **Detailed design** and **Alternatives considered** closely — the
  latter records paths already rejected (often for prime-directive reasons); don't
  re-propose them.
- Open **every file the proposal links** (proposals here reference their touch-points
  heavily) and read the surrounding code, so your change matches what exists.
- Check [`docs/architecture.md#implementation-status`](../../../docs/architecture.md) — the
  source of truth for what already exists, so you neither rebuild something shipped nor
  assume something absent.
- **Check dependencies.** If the References / design lean on another BE item, verify that
  item's status. A prerequisite still at `Status: Proposal` is a blocker — surface it and
  ask how to proceed (build the prerequisite first? a thinner first slice?).

For a large item, fan this reading out to the `Explore` agent, and use the `Plan` agent to
draft the implementation strategy, so the main thread keeps the synthesis, not the file
dumps.

### 4. Set up a focused workspace

Follow the parallel-work rules ([`docs/ai-development.md`](../../../docs/ai-development.md)):

- **One topic per branch.** If you're on `main`, branch off the *latest* origin first:
  `git fetch origin && git switch -c claude/be-NNNN-<slug> origin/main`. If the session is
  already on a dedicated branch / worktree, stay there.
- **Stay in your lane.** Touch only the files this item needs. If the design forces a
  cross-cutting change (e.g. a driver-API change), say so up front.

### 5. Plan, then confirm before writing code

Implementing a whole roadmap item is large and hard to reverse, so **get the user's
go-ahead on a concrete plan first** (consider `EnterPlanMode`). The plan should name:

- the files you'll add/change and the shape of the change;
- the **machine-checkable outcome** that proves it works (the assertion / behavior a
  deterministic `run` or unit test will check) — and explicitly, where AI is and isn't
  allowed to sit;
- the tests you'll add or change;
- any docs that must move (and therefore need both languages);
- any tension with the prime directives, and how you've reshaped the design to fit (the
  way `ideation` reshapes a conflicting idea rather than dropping it).

Only implement once the user is happy with the plan.

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
- **Docs are bilingual.** If you change a *documented* behavior, update `docs/` **and** its
  `docs/ja/` mirror. Write the Japanese side under the
  [`japanese-document-writing`](../japanese-document-writing/SKILL.md) skill — natural Japanese, not
  a literal rendering of the English.

### 7. Review and refine the diff

`make check` proves the change is green — it does **not** judge design, simplicity, or
logic. Close that gap on the diff you just wrote, with official review tooling as
**authoring aids**. This stays inside directive #1: they advise the author and never
judge — the gate (step 9) is still the only verdict, and no LLM touches the `run`/CI path.

Built-in skills, on the diff, every time:

- Invoke the **`simplify`** skill to refine the change — reuse, dead code, over-abstraction,
  altitude — and apply its fixes. It edits the working tree, so the gate must run *after* it.
- Invoke the **`code-review`** skill (review-only; there's no PR yet) for the correctness
  bugs the gate can't see.

For a non-trivial change, also launch the official **pr-review-toolkit** agents in parallel
(via the `Agent` tool; the [`session-start` hook](../../hooks/session-start.sh) installs it and
[`.claude/settings.json`](../../settings.json) enables it repo-wide — or install by hand with
`claude plugin install pr-review-toolkit@claude-plugins-official`). Pick by what the change
touches; each lens maps to a prime directive, so the review pulls the same way as the runner:

- **`silent-failure-hunter`** — swallowed errors and weak fallbacks. This *is* "determinism
  first, fail loudly": a test tool that hides failures is worse than none.
- **`type-design-analyzer`** — type invariants and encapsulation under strict `mypy`.
- **`pr-test-analyzer`** — whether the regression-net tests actually cover the new logic.

Weigh every suggestion against the prime directives and the surrounding code before taking it;
drop anything that fights the codebase grain. Prefer the built-in `simplify` / `code-review`
over pr-review-toolkit's own `code-simplifier` / `code-reviewer` — they overlap, and the
simplifier leans on JS/React idioms foreign to this Python core.

### 8. Flip the roadmap item to Implemented

The implementing PR is what ships the item, so promote it in this same change:

1. In **both** language files, set the metadata `Status` to **Implemented**. Add an `Implementing PR:
   [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN)` row right under `Status` once
   the PR number exists (fill it at step 10 if you don't have it yet).
2. Regenerate the index:
   ```bash
   make roadmap-index     # regenerate the tables in both README index pages
   ```
   The item's directory never moves (BE-0159) — every item lives at a permanent flat
   `roadmaps/BE-NNNN-<slug>/` path, and `Status` decides only the index bucket. `make test` fails
   if the committed index drifts from an item's `Status`, so this keeps the gate honest. **Never
   renumber the item**; its ID is permanent.

### 9. Verify — the gate

```bash
make check    # lock-check + format + lint + lint-sh + lint-actions + typecheck + test
```

It must be green; **never push red** (the tracked pre-push hook runs it for you). It needs
no Simulator and runs anywhere. On-device E2E (`make -C demos/showcase run-swiftui`) is a separate,
heavier path and is **not** part of this gate — don't block core work on it. But if the
item's correctness genuinely depends on a Simulator/browser run, drive that run with the
`verify` skill (launch the app, exercise the behavior, report what you saw) rather than
claiming it works untested.

### 10. Auto-open a Draft PR

Once step 9's `make check` is green and the branch is pushed, **open the PR yourself** — this
skill's output is always a self-contained, gate-green change, so there is no reason to wait for a
human to open it. This is the *one* skill that auto-opens: the BE-*authoring* skills
([`ideation`](../ideation/SKILL.md), the proposal phase of
[`propose-and-build`](../propose-and-build/SKILL.md)) never do, because a proposal PR is a human
checkpoint whose id is allocated only on merge (see [`CLAUDE.md`](../../../CLAUDE.md)).

- **Draft by default:** `gh pr create --draft`. **Title and body in English**, always — prefix the
  title with the ID (`[BE-NNNN] feat(<scope>): …`), write a thorough body from
  [the template](../../../.github/PULL_REQUEST_TEMPLATE.md), and close with the `make check`
  verification line. Commits are imperative and scoped (`feat(run): …`).
- **Doc-only exception:** if this item's change is purely documentation/prose (skills, `CLAUDE.md`,
  roadmap `*.md`/`*-ja.md` — no product code under `bajutsu/` / `BajutsuKit/` / runner / drivers),
  `CLAUDE.md`'s "documentation-only PRs open Ready for review" rule takes precedence: open it
  **Ready** (omit `--draft`) with `--reviewer bajutsu-e2e/steering-committee`. (BE-0230 itself is
  exactly this case.)
- Then **fill the `Implementing PR:` row** in both BE files with the real number and push that
  follow-up, so the shipped record points at its PR.
- The Draft + never-mark-ready-while-red rules from `CLAUDE.md` still hold: a Draft PR is only
  marked ready (`gh pr ready`) by the **human**, never automatically while CI is red.

### 11. Keep the follow-up loop lean — isolate it in subagents

The implement phase's context (the design back-and-forth, the file reads) is dead weight for the
follow-up work, and this is a long-lived, large-context session, so re-running the token-heavy
follow-up work on top of that transcript is exactly the kind of cost the project's token economy
cares about. The obvious fix — `/compact` once here — is not available: **a skill cannot issue a
slash command mid-execution**
([claude-code#68629](https://github.com/anthropics/claude-code/issues/68629)), so the loop can't
compact itself. Get the benefit structurally instead: run the token-heavy follow-up work (reading
CI logs, diffs, and review comments, making fixes) in a **fresh subagent context** each iteration
(step 12), so that expensive investigation never carries the implement transcript or the previous
iteration's logs. The loop layer left in this session does almost nothing per turn — one `gh` call,
then it reads the subagent's short summary — so its own turns stay light.

### 12. Run the hands-free pr-followup loop

Drive the pacing with the built-in **`/loop`** skill and delegate each iteration's work to a
subagent. Tell the session to run:

```
/loop run pr-followup for #NNN
```

`/loop` self-paces with `ScheduleWakeup` — a **short** interval (~270 s) while CI is actively
running, a **longer** one (~20–30 min) while waiting on human review (cache-window guidance). Each
iteration, the loop layer (this session — *not* `pr-followup`, which stays unchanged) does three
things:

1. **Check for a merge conflict**, because today's `pr-followup` does not query `mergeable`:
   ```bash
   gh pr view <PR> --json mergeable --jq .mergeable
   ```
   - `CONFLICTING` → **stop and escalate** immediately (don't spawn the subagent). `pr-followup`
     never rebases or force-pushes; the human rebases and resolves, then restarts the loop.
   - `UNKNOWN` → GitHub is still computing mergeability (e.g. right after a push); treat as "no
     conflict yet", proceed, and re-check next iteration.
   - `MERGEABLE` → proceed.
2. **Spawn a subagent** (the `Agent` tool) and give it [`pr-followup`](../pr-followup/SKILL.md)'s
   steps directly in its prompt (point it at that skill file to follow) — the `Skill` tool runs
   only in the main conversation, so a subagent cannot invoke `/pr-followup` itself. Its task for
   the PR: assess CI and review comments, make targeted fixes, self-review the fix against the CI
   review contract, run `make check`, push, reply to and resolve threads, and **return a short
   structured summary** — what it changed, whether it pushed,
   the resulting CI/review state, and whether it hit `pr-followup`'s design-change escalation. The
   fresh context is what keeps the implement transcript out of the expensive work (step 11).
3. **Read the summary and evaluate the stop conditions** below. The loop layer owns the
   conflict / `CHANGES_REQUESTED` checks and the counters; `pr-followup` itself is unchanged.

**Stop the loop only when all three hold:**

1. **CI green** — every required check passing. A required check that only a human can satisfy
   (e.g. a required approval count) never goes green from the loop; when that is the *only* thing
   left red and the review surface is quiet, treat the PR as quiet-and-green-pending-approval and
   stop, reporting what still awaits the human, rather than burning iterations until a cap.
2. **No `CHANGES_REQUESTED`** — `reviewDecision != CHANGES_REQUESTED`. A top-level "Request
   changes" review can carry no inline comments, and `pr-followup` reads only inline comments
   (`position != null`), so it can't see such a standing veto — the loop layer must. Discriminate
   by history: if `CHANGES_REQUESTED` is set and **no inline threads were ever left** to resolve,
   escalate immediately (like a conflict) — there is nothing for `pr-followup` to act on. If inline
   threads **were** left and the subagent has since resolved every one but the decision still
   stands — GitHub clears `CHANGES_REQUESTED` only when the *same reviewer* re-reviews, not when
   threads are resolved — post **one** nudge (`gh pr comment`) asking the reviewer to re-review,
   once per stale-review episode (skip re-posting while your nudge is still the latest comment), so
   an away reviewer isn't paged on every poll.
3. **Two consecutive quiet polls** — no new review comments across two polls in a row (one empty
   poll can race a reviewer mid-comment; the second confirms quiescence).

When all three hold, **report that the PR is quiet-and-green, and stop — do not call
`gh pr ready`.** Draft → Ready is a deliberate human sign-off: the human inspects the conversation,
confirms no subtle concern was left unaddressed, and marks it ready. "Hands-free" covers the
mechanical tail (CI fixes, replies), not the merge decision or a rebase.

**Escalate (stop and hand to the human)** on any of:

- a `pr-followup` comment that needs a **design or spec change** (its existing, unchanged
  escalation rule — a design call is the human's, and outranks the stop conditions above);
- a **merge conflict** (the `mergeable` check above);
- `CHANGES_REQUESTED` with **no inline threads ever left to act on** (stop condition 2's
  never-had-threads branch — the resolved-threads branch nudges instead, so this covers only the
  case where `pr-followup` has nothing to fix).

**Two backstops** bound the loop. Count the two kinds of iteration **separately**, classifying each
as **CI-wait whenever any required check is not yet green** (so the common post-open state — CI
running, no review yet — counts as CI-wait) and **review-wait otherwise**:

- **Review-wait cap — 20 iterations** (≈ 7–10 h at the 20–30 min cadence; ≤ 20 h at the 3600 s
  `ScheduleWakeup` maximum).
- **CI-wait cap — 30 iterations** without CI turning green — catches CI stuck red for a reason
  `pr-followup` can't fix (flaky infra, an unrelated external failure) that its escalation rule
  (which fires only on a design-change comment) wouldn't catch.

On hitting either cap, stop and report the current state (CI status, open comment count). The human
can interrupt or restart the loop at any time by stopping the session.

**Prime-directive check:** no LLM touches the `run`/CI verdict. `pr-followup`'s fixes are still
judged by `make check` and CI; the loop only *schedules* those deterministic checks and answers
reviewers, and every genuine decision escalates to the human.

## References

- [`CLAUDE.md`](../../../CLAUDE.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime
  directives every change must honor.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — parallel-work rules, the
  gate, and the strict BE-ID lifecycle (Status ⇒ index bucket, flat one-directory layout, permanent IDs).
- [`roadmaps/README.md`](../../../roadmaps/README.md) — the index and the per-item format.
- [`pr-followup`](../pr-followup/SKILL.md) — the skill steps 11–12 loop over: after this skill
  opens the Draft PR, a paced `/loop` drives the mechanical tail (CI fixes, review replies) to
  quiet-and-green, running each iteration's `pr-followup` in a fresh subagent, so implement → PR →
  followup is one automated flow.
- [`ideation`](../ideation/SKILL.md) — the upstream skill that authors the proposal this one builds.
- [`propose-and-build`](../propose-and-build/SKILL.md) — composes `ideation` + this skill for a
  small, settled item: author the proposal and implement it in parallel as a temporary two-PR
  stack, then hand off to this skill's steps 8–10 once the id is allocated.
- The built-in **`simplify`** / **`code-review`** / **`verify`** skills — the authoring aids
  steps 7 and 9 lean on. They advise the author; only `make check` judges.
- **pr-review-toolkit** (`@claude-plugins-official`, Anthropic-official) — the specialized
  review agents step 7 launches; enabled repo-wide in [`.claude/settings.json`](../../settings.json).
