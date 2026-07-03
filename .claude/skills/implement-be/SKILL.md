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
  flips the item to Implemented (Status + roadmap-promote + reindex), and proves the gate is
  green. The deterministic counterpart to the `ideation` skill: ideation authors proposals,
  this one ships them.
---

# Implement a BE item

Take one roadmap (BE) item from its proposal to shipped, green code. You are the
**implementer**; the deterministic gate (`make check`) is the judge — never an LLM. The
proposal's **Detailed design** is your spec. Converse in the user's language; write code,
commits, and PR text per the conventions below.

This is the counterpart to [`ideation`](../ideation/SKILL.md): that skill *authors* a BE
proposal, this one *ships* it.

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
3. **App-agnostic.** Per-app differences live in config (`apps.<name>`); the tool,
   drivers, and runner stay unchanged across apps.

## Workflow

### 1. Resolve the item

Accept any of: a full ID (`BE-0066`), a bare number (`66` / `0066`), or a slug fragment.
Locate its directory:

```bash
ls -d roadmaps/{proposals,implemented}/BE-*<id-or-slug>*/
```

Read **both** language files; the **English** `BE-NNNN-<slug>.md` is the authoritative
spec, the `-ja.md` mirror is supporting context. Then branch on where it sits:

- **Under `proposals/` with `Status: Proposal`** — the normal case. Note that implementing
  it *accepts* it: this PR flips it to `Implemented`. Say so.
- **Already under `implemented/` (`Status: Implemented`)** — it has shipped. Stop and
  confirm what the user actually wants (extend it? a follow-up item? a bug fix?) before
  doing anything.
- **`Proposal (deferred)`** — surface that it was deliberately parked; confirm the user
  wants to un-defer and build it now.

### 2. Claim the tracking issue

Every open item (`Status: Proposal` or `Status: In progress`) has a GitHub tracking issue —
opened and labeled `roadmap-tracking` by the BE-0109 sync, titled `[BE-NNNN] <title>`. Its
body says "self-assign this issue when you pick it up; leave it unassigned if it's up for
grabs." Do that now, before you branch, so ownership is visible the moment you start:

```bash
gh issue list --label roadmap-tracking --state open --search "BE-NNNN in:title" --json number --jq '.[0].number'
gh issue edit <number> --add-assignee @me
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
  item's status. A prerequisite still sitting in `proposals/` is a blocker — surface it and
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
  ambiguous selectors fail), app-agnostic (new knobs go in `apps.<name>` config), and no
  LLM anywhere on the `run`/CI path.
- **Tests are the regression net.** If you change behavior, a test changes with it. The
  Python core needs no Simulator, so cover the logic in the fast suite.
- **Docs are bilingual.** If you change a *documented* behavior, update `docs/` **and** its
  `docs/ja/` mirror. Write the Japanese side under the
  [`japanese-tech-writing`](../japanese-tech-writing/SKILL.md) skill — natural Japanese, not
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

1. In **both** language files, set `* Status: **Implemented**` and change the `Track` line
   to **Accepted** (`../../README.md#accepted`). Add an `* Implementing PR:
   [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN)` line right under `Status` once
   the PR number exists (fill it at step 10 if you don't have it yet).
2. Move the directory and regenerate the index:
   ```bash
   make roadmap-promote   # moves proposals/BE-NNNN-… → implemented/BE-NNNN-…
   make roadmap-index     # regenerate the tables in both README index pages
   ```
   `make test` fails if an item's `Status` and its directory disagree, or if the committed
   index drifts — so these two commands keep the gate honest. **Never renumber the item**;
   its ID is permanent.

### 9. Verify — the gate

```bash
make check    # lock-check + format + lint + lint-sh + lint-actions + typecheck + test
```

It must be green; **never push red** (the tracked pre-push hook runs it for you). It needs
no Simulator and runs anywhere. On-device E2E (`make -C demos/features e2e`) is a separate,
heavier path and is **not** part of this gate — don't block core work on it. But if the
item's correctness genuinely depends on a Simulator/browser run, drive that run with the
`verify` skill (launch the app, exercise the behavior, report what you saw) rather than
claiming it works untested.

### 10. PR only when asked

Push to your branch. **Don't open a PR unless the user asks** — the human usually opens it.
When you do:

- **Title and body in English**, always. Prefix the title with the ID:
  `[BE-NNNN] feat(<scope>): …`. Commits are imperative and scoped (`feat(run): …`).
- Then **fill the `* Implementing PR:` line** in both BE files with the real number and
  push that follow-up, so the shipped record points at its PR.

## References

- [`CLAUDE.md`](../../../CLAUDE.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime
  directives every change must honor.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — parallel-work rules, the
  gate, and the strict BE-ID lifecycle (Status ⇒ directory, `roadmap-promote`, permanent IDs).
- [`roadmaps/README.md`](../../../roadmaps/README.md) — the index and the per-item format.
- [`ideation`](../ideation/SKILL.md) — the upstream skill that authors the proposal this one builds.
- The built-in **`simplify`** / **`code-review`** / **`verify`** skills — the authoring aids
  steps 7 and 9 lean on. They advise the author; only `make check` judges.
- **pr-review-toolkit** (`@claude-plugins-official`, Anthropic-official) — the specialized
  review agents step 7 launches; enabled repo-wide in [`.claude/settings.json`](../../settings.json).
