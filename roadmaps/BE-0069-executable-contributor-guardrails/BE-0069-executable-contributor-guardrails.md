**English** · [日本語](BE-0069-executable-contributor-guardrails-ja.md)

# BE-0069 — Executable contributor guardrails (procedures as commands)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0069](BE-0069-executable-contributor-guardrails.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0069") |
| Implementing PR | [#243](https://github.com/bajutsu-e2e/bajutsu/pull/243) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Promote the contributor procedures that today live only as **prose** — in
[`CLAUDE.md`](../../CLAUDE.md), the `ideation` / `implement-be` skills, and
[`docs/ai-development.md`](../../docs/ai-development.md) — into **human-runnable commands**
(`make` targets backed by small scripts). The same entrypoint then serves both a human starting a
task and an AI doing it on the human's behalf, so a guardrail is no longer a recipe that only an
agent reliably follows from prose, but a command anyone can run. This is purely developer-facing
infrastructure: it adds no LLM, never runs inside `run`, and does not touch the deterministic gate,
so the Prime directives ([CLAUDE.md](../../CLAUDE.md)) hold by construction.

This continues the *Contributor workflow* line.
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
turned a hand-edited **shared ledger** (the roadmap index) into a generated artifact;
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
made **CI mirror `make check` by construction**, removing duplication that had silently drifted.
Both apply one principle — a single source of truth that is *executed*, not transcribed. This item
applies that principle to the **procedures** themselves.

## Motivation

Bajutsu's guardrails already split into two kinds, by whether a human can run them:

- **Executable** — one command a human and an AI invoke identically: [`make check`](../../Makefile)
  (the gate), [`make setup` / `make hooks`](../../Makefile) (self-healing git config),
  [`make roadmap-index` / `roadmap-promote` / `roadmap-id-repair`](../../Makefile). The command
  *is* the guardrail; it cannot be forgotten or half-remembered.
- **Prose only** — a multi-step recipe written for a reader to follow by hand:
  - **Scaffolding a new roadmap item** — create the directory and *both* bilingual files in the
    exact Swift-Evolution format, with a complete metadata block, the `BE-0069` placeholder, and the
    author's GitHub handle. The `ideation` skill spends most of its text teaching this, and it is the
    single most error-prone, highest-ceremony procedure in the repo — yet it has no command.
  - **Worktree + branch setup** — the `git fetch origin && git worktree add … -b … origin/main &&
    make setup` recipe in [`docs/ai-development.md`](../../docs/ai-development.md).
  - **Pre-push routine** — `git fetch origin && git rebase origin/main && make check`, plus the
    "definition of done" checklist (both-language docs touched? a test changed with the behavior?
    `Status` flipped if shipping?).
  - **Item well-formedness** — bilingual pair present, slug matches the directory, metadata block
    complete, author is a handle link, `Status` consistent with the subdirectory, cross-referenced
    `BE-NNNN` exist. Today this is enforced only by two narrow tests (index drift,
    `Status`↔directory) plus a reviewer's eyes.

Three problems follow from leaving these as prose:

1. **Prose drifts from reality.** BE-0067 found CI had *already* drifted from `make check`; a recipe
   that is only read, never executed, rots the same way. A command is run — and where it guards an
   invariant, tested — so it stays true.
2. **Only an AI reliably executes a long prose recipe.** A human authoring a BE item by hand, without
   the `ideation` skill loaded, is unlikely to reproduce the format, the placeholder rule, and the
   index interaction correctly. That is precisely the "AI-only" dependency this item removes.
3. **The asymmetry is upside-down.** The most ceremonious, mistake-prone procedure (item scaffolding)
   is the one with *no* command, while routine checks (`make check`) are one keystroke. The
   guardrails a human most needs help running are the ones least available to them.

The intent that motivates this item — *move from "leave it to the AI" to "the human starts, the AI
substitutes"* — is exactly this promotion. When the guardrail is a command, the **human owns the
entrypoint** and the AI becomes a substitute *within* a human-runnable workflow, not the sole keeper
of the procedure.

## Detailed design

**Principle.** Each prose procedure becomes a `make <verb>` target backed by a small script under
[`scripts/`](../../scripts/) — Python where it manipulates roadmap files (typed; `mypy` covers
`scripts/` since BE-0067), shell where it is git plumbing (added to the `SHELL_SCRIPTS` list
`make lint-sh` checks), matching the existing split. The target is the single source of truth;
`CLAUDE.md`, the skills, and the docs then **reference the command** instead of re-describing its
steps — the same split BE-0067 used when it routed CI through the Makefile. Where a procedure yields
a checkable invariant, a check folds into `make check`.

Four mechanisms, in order of leverage.

### A. Scaffold a roadmap item — `make new-roadmap-item`

`make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal]` →
`scripts/new_roadmap_item.py`:

- Creates `roadmaps/proposals/BE-0069-<slug>/` with both `BE-0069-<slug>.md` and
  `BE-0069-<slug>-ja.md`, pre-filled from a template: the bilingual header link, the metadata block
  (`Proposal` / `Author` / `Status` / `Topic`), and the five Swift-Evolution sections
  (`Introduction` / `Motivation` / `Detailed design` / `Alternatives considered` / `References`)
  seeded with `TBD`.
- **Always emits the literal `BE-0069` placeholder** — never a number. IDs are permanent and
  monotonic, and allocation is CI's atomic job
  ([BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md));
  a scaffolder that guessed a number would reintroduce the race the placeholder exists to avoid.
- Validates `TOPIC` against the known section map in
  [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) so the item lands in a
  real section. (A `Topic` matching no section makes the index builder *crash* after CI numbers the
  item — not merely drift — a sharp edge worth catching at creation.) Defaults: `Status=Proposal`,
  author resolved from `git config` (overridable via `HANDLE=`).
- **Does not add a manual index row.** The generator skips `BE-0069` items, so the committed index
  stays row-free for the placeholder and `make check` is green locally; the `roadmap-id` workflow
  regenerates the index once it numbers the item. (This is a known trap — the scaffolder encodes the
  correct flow so an author never hits it.)

This is the human entrypoint to the procedure the `ideation` skill performs by hand; the skill is
rewritten to **call this command**, then fill the drafted sections, rather than describe file
authoring from scratch.

### B. Lint the roadmap — `make lint-roadmap`

`make lint-roadmap` → `scripts/lint_roadmap.py`, **folded into `make check`**. For every item it
checks the well-formedness rules now scattered across prose and reviewer judgement:

- the bilingual pair exists (both files present);
- the slug matches the directory name, and the in-file `BE-0069`/`BE-NNNN` token matches the
  directory;
- the metadata block is complete (`Proposal`, `Author`, `Status`, `Topic` all present);
- `Author` is a GitHub-handle link (`[@handle](https://github.com/handle)`);
- `Status` is one of the known values and consistent with the subdirectory (`Implemented` ⇒
  `implemented/`, otherwise `proposals/`);
- every cross-referenced `BE-NNNN` resolves to an item that exists;
- a `BE-0069` item does not cross-reference *another* `BE-0069` item (the allocator's per-item
  rewrite cannot fix that — already a documented limitation).

Today only index drift and `Status`↔directory consistency are tested
([`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py),
[`tests/test_promote_roadmap_items.py`](../../tests/test_promote_roadmap_items.py)); the rest rely
on a reviewer noticing. This makes well-formedness a first-class, fast, author-time check with
actionable messages — runnable mid-edit without the full suite. Where it overlaps the existing tests,
the invariant consolidates here and the tests call it.

### C. Workspace & preflight helpers — `make worktree`, `make preflight`

Turn the multi-line recipes in [`docs/ai-development.md`](../../docs/ai-development.md) into
commands:

- `make worktree TOPIC=<topic>` → `git fetch origin`, then
  `git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main` (the `<user>/` prefix
  configurable), then `make setup` in the new tree. The non-optional `git fetch origin` is baked in,
  so the "branched off a stale `origin/main`" foot-gun the docs warn about cannot happen.
- `make preflight` → `git fetch origin && git rebase origin/main && make check`, then print the
  "definition of done" reminder (both-language docs? a test with the behavior change? `Status`
  flipped if shipping?). This is **advisory and human-initiated** — the pre-push hook already *gates*
  `make check`; `preflight` is the do-it-early version a human runs before they think they are done,
  not a second hard gate.

### D. Commit / PR metadata checks — `make lint-pr` (lightest)

The conventions that are prose-only and reviewer-enforced today, made checkable — strictly the
**mechanical** ones, never judgement calls:

- a roadmap-touching change carries the `[BE-NNNN]` (or `[BE-0069]`) prefix on its PR title;
- commit messages are scoped (`feat(scope): …` / `fix(scope): …` / `docs: …`);
- a reminder when a behavior change carries no test delta.

This stays advisory / opt-in where it needs PR context: it can run locally against the branch's
commits, or in CI against the PR title. It deliberately never blocks on the un-mechanizable rules
("stay in your lane", bilingual prose style) — those remain prose and human/reviewer judgement.

### Where it lives, and what the prose becomes

- Scripts under `scripts/`; new `make` targets in the [`Makefile`](../../Makefile); `lint-roadmap`
  joins the `check` target so it gates by construction.
- The prose that today *describes* these procedures is rewritten to *point at the command*:
  `CLAUDE.md`'s Conventions, the worktree / preflight / BE-ID sections of `docs/ai-development.md`
  (and its `docs/ja/` mirror), and the `ideation` / `implement-be` skills, which **invoke** the
  commands rather than narrate the steps. Documentation changes are bilingual, per the repo rule.

### Migration, in phases

1. This proposal.
2. **B (`lint-roadmap`) first** — pure validation, no behavior change, immediately useful, and it
   codifies invariants already half-enforced; wire it into `make check`.
3. **A (`new-roadmap-item`)** — the highest-leverage entrypoint; update the `ideation` skill to call
   it.
4. **C (worktree / preflight)** — convert the doc recipes; repoint `docs/ai-development.md` and
   `CLAUDE.md` at the commands.
5. **D (`lint-pr`)** — lightest; advisory first, then consider a `commit-msg` hook wired by
   `make hooks`.

Each phase is a small, independent PR (the parallel-work model, BE-0043).

## Alternatives considered

- **Keep the procedures as prose (status quo).** Rejected: prose drifts (the BE-0067 finding), and a
  long prose recipe is reliably executed only by an AI — a human cannot own the entrypoint, which is
  the asymmetry this item exists to remove.
- **Encode the procedures only inside the skills** (richer `ideation` prose, or a dedicated
  sub-agent). Rejected: that doubles down on AI-only execution, the very thing this item moves away
  from. A skill that *calls* a command is good; a skill that is the *only* way to run the procedure is
  not.
- **Make the scaffolder allocate a real BE number.** Rejected: it violates the placeholder rule — IDs
  are permanent and monotonic, and allocation is CI's atomic, race-free job
  ([BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)).
  The scaffolder must emit `BE-0069`.
- **Make `preflight` a second hard gate (a pre-commit hook running `make check`).** Rejected: the
  pre-push hook already gates `make check`; a per-commit gate slows the inner loop for no added
  safety. `preflight` is advisory and human-initiated by design.
- **A shipped `bajutsu dev …` CLI instead of `make` targets.** Rejected for now: `make <verb>`
  matches every other contributor entrypoint in the repo, needs no packaging, and keeps developer
  tooling out of the shipped `bajutsu` CLI surface. A consolidated dev CLI can come later if the
  target count grows.
- **A dedicated new roadmap topic.** Filed instead under *Contributor workflow* — the BE-0043
  topic — following the precedent
  ([BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md))
  of not splitting a topic for a single item.

## Progress

- [x] A — `make new-roadmap-item` (the `ideation` skill invokes it).
- [x] B — `make lint-roadmap`, folded into `make check`.
- [x] C — `make worktree` / `make preflight`, with the doc recipes repointed at the commands.
- [x] D — `make lint-pr` plus the `pr-title.yml` CI title gate.
- [x] Phase 5 tail — a tracked `.githooks/commit-msg` hook (wired by `core.hooksPath`) blocks a non-scoped commit subject via `lint_pr.py --commit-msg`.

## References

- [CLAUDE.md](../../CLAUDE.md) — the prose procedures these commands replace, and the Prime
  directives this respects (no LLM in the gate; developer-facing only).
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — turning a hand-edited ledger into a generated artifact; the *Contributor workflow*
  precedent.
- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — CI mirrors `make check` by construction (single source of truth); `scripts/` under `mypy`.
- [BE-0061 — Collision-proof BE-ID allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — why the scaffolder must emit `BE-0069`, not a number.
- [`docs/ai-development.md`](../../docs/ai-development.md) — the worktree / preflight / BE-ID
  recipes C and A convert.
- [`Makefile`](../../Makefile), [`scripts/`](../../scripts/) — where the targets and scripts
  land; the existing executable guardrails they extend.
- The [`ideation`](../../.claude/skills/ideation/) and
  [`implement-be`](../../.claude/skills/implement-be/) skills — the procedures A formalizes, which
  are rewritten to call the commands.
