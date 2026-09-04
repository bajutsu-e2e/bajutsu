---
name: ideation
model: sonnet
description: Turn a rough Bajutsu feature idea into a bilingual roadmap proposal. Use for proposal authoring and review, never implementation.
---

# Ideation

A sounding board for ideating Bajutsu features and shaping them into roadmap (BE) items.
You are the author and the thinking partner — **not** the judge. Converse in the user's
language (the roadmap is bilingual; mirror their language in the chat, write the files in
both as required below).

## Scope: roadmap authoring only — never implement

This skill **only** authors and shapes roadmap (BE) items. It stops at the roadmap files
(and, when asked, the PR that carries them). **Do not write, modify, or refactor any
product code** — not `bajutsu/`, not `BajutsuKit/`, not tests, not config, not demos — even
if the discussion makes the implementation obvious or the user nudges toward "just build
it". Your deliverable is always the BE proposal, never a working feature.

If the user asks you to implement an idea, don't switch hats mid-session: point them to the
[`implement-be`](../implement-be/SKILL.md) skill (the deterministic counterpart that ships an
existing BE item from its ID) and keep this session to authoring the proposal — or, when the
item is small and its design is already settled, to
[`propose-and-build`](../propose-and-build/SKILL.md), which authors the proposal and implements
it together in one BE-creation PR. The only files *this* skill touches are under
`roadmaps/` (plus the index the generator regenerates).

## Prime directives (these bound every idea)

Read [`AGENTS.md`](../../../AGENTS.md) and [`DESIGN.md`](../../../DESIGN.md) before
proposing. Any idea must respect them, and you should say so when an idea brushes against
a boundary:

1. **AI authors and investigates, never judges.** Nothing you propose may put an LLM call
   into the Tier‑2 `run`/CI gate. AI-flavored ideas live in `record`/`triage`/draft paths.
2. **Determinism first.** No fixed `sleep`; ambiguous selectors fail rather than guess.
3. **App-agnostic.** Per-app differences belong in config, not the tool/drivers/runner.

If an idea conflicts (e.g. "auto-heal locators mid-run", "AI decides pass/fail"), don't
silently drop it — surface the conflict, then reshape it into something that fits (the
"Not adopting" and self-healing items in the roadmap are precedents).

## Workflow

Alongside the steps below, keep [`be-progress-tracker`](../be-progress-tracker/SKILL.md)
current: at minimum after step 4 (the item exists, even still as `BE-XXXX`), step 5 (the self-review
pass comes back clean), and step 6 (`make check` is green). Invoke it through the Agent tool with
`model: "haiku"` passed explicitly, since a subagent call does not inherit that skill's own
frontmatter model. Never hand over a roadmap-item path at these checkpoints: the item's real id is
allocated only when a human merges the proposal PR (BE-0089), which happens after this workflow's
own steps are done, so `roadmaps/BE-XXXX-<slug>/…` never exists on `main` during any checkpoint this
workflow makes — the tracker's roadmap-link button stays dropped for the whole proposal phase by
design, not by omission. It only turns decisions this workflow already made into a glanceable status page — never
let it gate or slow this workflow down; skip a checkpoint rather than block on it.

### 1. Ground yourself in the existing roadmap

Before ideating, pull in what already exists:

- `make roadmap-find ARGS="--grep <topic>"` — the items already on the topic, in one table.
  It matches an item's **id, title, `Topic`, and `Introduction` excerpt — never the body**, so an
  empty table means the phrase appears in no title, not that no item covers the subject. Treat it as
  a first pass and follow with `grep -ril <keyword> roadmaps/` before concluding a topic is novel;
  an item usually covers a nearby idea somewhere in its `Detailed design`.
  A **multi-word** topic needs inner quotes — `ARGS="--grep 'known failure'"`. The Makefile
  expands `$(ARGS)` unquoted, so without them the shell splits the phrase and the parser rejects
  the stray word. This bites on the most natural query for a request, so reach for the quotes
  first rather than after the error.
- `make roadmap-status STATUS="Proposal"` — the open backlog, when a keyword misses the framing.
- `make repo-map ARGS="--headings docs/architecture.md"` names the groups under **Implemented**.
  That section records what already exists. Read the group covering the area, not the whole
  section, so you never propose something shipped.
- The `BE-NNNN-*/` files those queries surface. Read the English `.md` alone: the `-ja.md`
  mirror holds nothing it lacks.
- [`roadmaps/README.md`](../../../roadmaps/README.md) — what a roadmap item *is*, and how to add
  one. It does not list the items; the queries above do.

This is what makes it a *sounding board* and not a blank page: every suggestion is
anchored to what's already planned, in progress, or deliberately not adopted.

### 2. Ideate with the user

Go back and forth. Offer concrete, bounded feature ideas; ask the questions that sharpen
scope (who's it for, which tier, what's the machine-checkable outcome). Ask one more that the
machine-checkable question does not cover: what would the user point to, once the change has
shipped, to say it worked? A machine-checkable outcome is the deterministic assertion a feature
is gated on; this question asks for the observable difference the item's Motivation has to name,
and step 4 carries the answer into that prose
([BE-0383](../../../roadmaps/BE-0383-motivation-verifiable-outcome/BE-0383-motivation-verifiable-outcome.md)).
Pull in adjacent existing items as reference points ("this is close to BE-00xx — extend it, or
is it distinct?"). `make roadmap-find ARGS="--grep <word>"` finds them. Pass `--id BE-00xx`
instead to confirm one you already have in mind. Keep proposing seeds the user can react to;
that reaction is the point.

### 3. Classify each idea that survives the discussion

For every idea the user wants to keep, decide one of three landings — and tell the user
which you're choosing and why:

- **Overlaps an existing BE item** → don't create a duplicate. Augment that item's files
  (both languages): sharpen Motivation / Detailed design, add the new angle, or record it
  as a related consideration. Note in the chat which item you extended.
  When the item it overlaps is already `Implemented`, there is usually **nothing to augment**:
  restating a shipped item's own Motivation adds noise, not information. Say so instead — name
  the item and the PR that shipped it, and show the user how to reach the behaviour that already
  exists, since a request for something shipped usually means they could not find it. If what
  they describe contradicts the shipped behaviour, that is a defect for
  [`record-issue`](../record-issue/SKILL.md), not a proposal.
  **This landing ends the session.** There is no item to draft, so steps 4 through 7 do not apply —
  the reply itself is the deliverable and the working tree stays clean. Say where the behaviour is
  documented (`docs/cli.md` and its `docs/ja/` mirror, plus the command module) rather than pointing
  only at the BE item: an item's `Detailed design` describes what was intended, and the user needs
  the flag as it actually shipped.
- **Novel and scoped enough for an item** → draft a new BE item (step 4).
- **Still unformed** → add a bullet under **Unsorted ideas** in both READMEs. Promote it
  to a numbered item later, once scope is clear. (This mirrors the roadmap's own rule.)

### 4. Draft a new BE item — leave the ID undetermined (`BE-XXXX`)

**Never invent a BE number.** Allocation is CI's job (step 7). Scaffold the item with the
command rather than authoring the files by hand — it emits the literal `BE-XXXX` placeholder,
the exact canonical format, and skips the index (so the gate stays green locally):

```
make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal] [HANDLE=<handle>]
```

This creates `roadmaps/BE-XXXX-<slug>/` with both `BE-XXXX-<slug>.md` and its `-ja.md`
mirror — the bilingual header link, the metadata block (`Proposal` / `Author` / `Status` /
`Topic`), and the five sections (`Introduction` / `Motivation` / `Detailed design` /
`Alternatives considered` / `References`) seeded with `TBD`. `TOPIC` is validated against the
index's known topics. `HANDLE` is the author's GitHub handle, resolved from `HANDLE=`, then
`$GITHUB_ACTOR`, then `git config github.user` — a **non-standard key that a fresh clone and most
Claude Code sessions leave unset**, so the command hard-exits instead of guessing. That exit is
correct behaviour, not an obstacle to route around: the `Author` row is permanent, and a guessed
handle credits a real person who did not write the item. **Ask the user for their handle and pass
it as `HANDLE=`.** Never reuse a handle you saw on another item, and never invent one.

**When you cannot ask** — an unattended run, or any turn with no user to answer — those three rules
leave no legal move, and a rule with no legal move gets broken rather than followed. Take the fourth
option instead: pass `HANDLE=handle`, the template's own literal placeholder, and state plainly in
your reply that the `Author` row still needs the real handle before the item merges. A visible
placeholder is honest and costs one edit later; a plausible handle is a misattribution that survives
review, because nothing downstream catches it — `make lint-roadmap` only checks that the link is
well-formed, not that the person exists or wrote this.

Then **fill the `TBD` sections** with what the discussion produced. Before you draft that prose,
invoke the [`document-writing`](../document-writing/SKILL.md) skill — it is the authoritative norm for BE
prose in both languages ([BE-0278](../../../roadmaps/BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md)),
and it shapes the draft rather than proofreading it, so read it *before* writing, not after. A BE
item is argued prose: an Introduction that states its contribution up front, a Motivation that moves
from the known problem to the new result, and a Detailed design that reads cleanly on both sides.
When you fill Motivation's `TBD`, carry in the verifiable outcome that step 2's conversation already
produced — the norm requires Motivation to name it, and the answer exists by the time you draft.

**Localize the Japanese** side (the title, the `トピック`, and the prose) — the scaffolder seeds both
files from the same English input, so the Japanese is a starting point to rewrite into natural
Japanese, not a finished translation. Write it under the
[`japanese-document-writing`](../japanese-document-writing/SKILL.md) skill (敬体; the Japanese layer beneath
`document-writing`), so both sides meet the same norm. Do **not** add an index row: the generator skips
`BE-XXXX` items, so the committed index stays row-free for the placeholder until CI numbers it.

> Why a placeholder and not a real number: IDs are permanent and monotonic, and several
> branches may be in flight at once. Picking a number by hand races — two PRs grab the
> same one. The `roadmap-id` workflow assigns the next free IDs deterministically at PR
> time, so authoring stays conflict-free.

### 5. Self-review against the CI review contract — before committing

Mirror the same review the "Claude review" GitHub Actions workflow runs (BE-0203), but locally,
before anything is committed — closing the gap between "the roadmap item reads fine to its own
author" and "the reviewer that sees it cold, on the PR, finds nothing to flag."

Run it as **two roles, never one agent** (BE-0347): one judges the diff, the other edits it, and
neither does the other's job. A model that fixes what it just flagged has every incentive to patch
just enough to silence its own comment, leaving something adjacent for the next cold look — its own
next round, or the live bot after the push — to raise again. This is the canonical procedure;
[`pr-followup`](../pr-followup/SKILL.md), [`propose-and-build`](../propose-and-build/SKILL.md),
and [`implement-be`](../implement-be/SKILL.md) all run it rather than restating it. The
[`claude-review`](../claude-review/SKILL.md) skill packages the same judge-only review/plan pass
as a standalone, directly-invocable skill; keep the two in sync if either one's cap, taxonomy, or
dedup rule changes.

**The review/plan pass.** Spawn a fresh Agent-tool subagent on `opus` that has **not** seen this
ideation conversation —
the CI reviewer also runs cold, with no memory of the authoring discussion, so a subagent that
inherited this session's context would not reproduce that. Give it exactly two inputs: the contract
at [`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) and the working
diff. Stage new files first with `git add -N roadmaps/` — `make new-roadmap-item`'s output starts
out untracked, so a bare `git diff` would omit it entirely, and `-N` records the path without
staging its content, so this judge-only pass never changes what a later commit picks up — then diff
against the **branch point**: `git diff $(git merge-base HEAD origin/main) -- roadmaps/`.

Diff against the branch point rather than `origin/main` itself. A worktree whose `HEAD` is not a
descendant of `origin/main` otherwise shows every item merged since as a deletion: one run reviewed
20 files and 2,068 lines in place of the 2 it had written. These are the same two commands
[`claude-review`](../claude-review/SKILL.md) gives for a working diff, which is the point — the two
are meant to stay in step. Scope both to `roadmaps/` rather than the whole tree: this skill
only ever touches that directory, and a stray file elsewhere — scratch output, unrelated
in-progress work in a parallel worktree — shouldn't get staged or reviewed along with it. There is
no PR yet, so nothing to run `gh pr diff` against. Ask it to apply every lens in the contract — the
prose-quality lenses and the functional ones alike — and skip the two parts that need a live PR:
"read the existing discussion first" (`gh pr view <PR_NUMBER> --comments`, since there is no PR
number yet) and posting findings as inline PR comments.

**When the host exposes no subagent tool.** Some sessions have no Agent tool, so the two-role
split cannot run as written. Running both roles yourself is not a substitute: what BE-0347 buys is
a reviewer with no memory of the authoring conversation, and you cannot be blind to a draft you
just wrote. Do the closest available thing — invoke [`claude-review`](../claude-review/SKILL.md)
for the judging pass and keep it strictly separate from the pass that edits — then **tell the user
the review ran without a cold reviewer**, so they know the PR's own review is the first genuinely
independent look at the item.

This pass **never edits a file**; it classifies. Every finding that clears the contract's severity
floor comes back as one of two things:

- a **fix instruction** — the file, the exact location, and the exact change to make; or
- an **escalation** — a finding that calls for a genuine design change, which is the user's call
  rather than either role's (the same valve `pr-followup` uses for a review comment asking for a
  fundamental redesign).

A false positive, or a deliberate trade-off the item already explains, is neither: it comes back
with its rationale and no instruction.

**The implement pass.** Run it on `sonnet` — this skill's fixes never leave `roadmaps/`, so the
cheaper tier is enough (BE-0347 splits the roles across models so neither inherits the other's
context). Unlike the CI workflow — which only posts comments, since prime directive 1
keeps a reviewer from also being the judge on the Tier-2 gate — this pass has no gate to stay off,
so it applies the fix instructions directly in the files. Apply them as given: don't re-judge a
finding's severity, and don't widen a change beyond what its instruction names. The judging is
already done, and scope creep here is what re-opens the diff to a fresh round of findings. If an
instruction looks unsafe or wrong, report that back rather than silently deviating from it — a
disputed instruction is the user's call, like an escalation.

**The loop.** Re-run a *fresh* review/plan pass against the updated diff after non-trivial fixes,
carrying forward this round's dismissed findings (with their rationale) into the next round's prompt
— each round's reviewer is spawned fresh with no memory of earlier dispositions, so without this a
dismissed false positive or trade-off would simply get re-flagged every round and never let the pass
come back empty. Repeat until a pass comes back empty (an empty pass is a complete review, per the
contract's own closing rule — "when nothing warrants a comment, post nothing"). "Advisory" describes
the CI workflow's relationship to the merge gate, not license to leave a real finding unfixed here.
Cap this at 3 rounds — an LLM-based reviewer is not fully deterministic and could keep surfacing a
fresh marginal finding each round, possibly one its own previous fix introduced; if the 3rd round
still returns findings, stop and let the user make the final call instead of looping further. The
cap counts **review/plan passes**, not fix attempts.

### 6. Verify

Run `make check` before finishing — roadmap changes are docs-only, but keeping the gate
green is the contract. (It needs no Simulator and runs on Linux.)

### 7. Open the PR (only when the user is happy)

Work on the session's designated branch. If the session has none — a bare worktree sitting on
`main`, say — cut `claude/<topic>` before committing; CLAUDE.md forbids committing to `main`
directly, and a detached or default checkout is not an exemption. Commit with a scoped message
(`docs(roadmap): …`), push, and — **only if the user asked for a PR** — open it. The PR
title and body are in English. In the body, state plainly that the items carry the
`BE-XXXX` placeholder and that the **roadmap-id** workflow will allocate the real BE IDs
and push the rename back onto the branch. Don't hand-edit the numbers afterward.

## What CI does with `BE-XXXX`

[`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py), run by the
[`roadmap-id`](../../../.github/workflows/roadmap-id.yml) workflow on every PR touching
`roadmaps/**`, finds each `BE-XXXX-<slug>/` placeholder, allocates the next IDs
(`max existing BE-NNNN + 1`, sorted by slug for determinism), renames the directory and
files, rewrites `BE-XXXX` → `BE-NNNN` inside them, fixes the index-table rows by slug, and
pushes the result back to the PR branch. If there are no placeholders it's a no-op.

One limitation to respect while authoring: a brand-new item should **not** cross-reference
another brand-new item by `BE-XXXX` (the in-file rewrite is per-item). Reference already
-numbered items by their real ID, and refer to a sibling new item by name/slug in prose.
