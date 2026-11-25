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

This item promotes the contributor procedures that today live only as **prose** into **human-runnable commands**. The prose sits in [`CLAUDE.md`](../../CLAUDE.md), in the `ideation` and `implement-be` skills, and in [`docs/ai-development.md`](../../docs/ai-development.md). The commands are `make` targets backed by small scripts.

One entrypoint then serves both a human who starts a task and an AI that does it on behalf of that human. A guardrail stops being a recipe that only an agent follows reliably from prose. It becomes a command that anyone can run.

The change is purely developer-facing infrastructure. It adds no LLM, never runs inside `run`, and does not touch the deterministic gate, so the prime directives ([CLAUDE.md](../../CLAUDE.md)) hold by construction.

This item continues the *Contributor workflow* line:

- [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) turned a hand-edited **shared ledger** (the roadmap index) into a generated artifact.
- [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) made **CI mirror `make check` by construction**. It removed a duplication that had silently drifted.

Both apply one principle: a single source of truth that is *executed*, not transcribed. This item applies that principle to the **procedures** themselves.

## Motivation

The guardrails of Bajutsu already split into two kinds, by whether a human can run them:

- **Executable.** One command that a human and an AI invoke identically. Examples are [`make check`](../../Makefile) (the gate), [`make setup` / `make hooks`](../../Makefile) (self-healing git config), and [`make roadmap-index` / `roadmap-promote` / `roadmap-id-repair`](../../Makefile). The command *is* the guardrail, so nobody can forget it or half-remember it.
- **Prose only.** A multi-step recipe written for a reader to follow by hand:
  - **Scaffolding a new roadmap item.** The author creates the directory and *both* bilingual files in the exact Swift-Evolution format. The files need a complete metadata block, the `BE-0069` placeholder, and the GitHub handle of the author. The `ideation` skill spends most of its text on this procedure. It is the most error-prone, highest-ceremony procedure in the repo, yet it has no command.
  - **Worktree and branch setup.** The recipe `git fetch origin && git worktree add … -b … origin/main && make setup` lives in [`docs/ai-development.md`](../../docs/ai-development.md).
  - **Pre-push routine.** The routine is `git fetch origin && git rebase origin/main && make check`, plus the "definition of done" checklist. The checklist asks whether the docs of both languages were touched, whether a test changed with the behavior, and whether `Status` flipped if the item ships.
  - **Item well-formedness.** The bilingual pair is present, the slug matches the directory, and the metadata block is complete. The author is a handle link, `Status` is consistent with the subdirectory, and every cross-referenced `BE-NNNN` exists. Today only two narrow tests (index drift and the `Status`-to-directory match) enforce this, plus the eyes of a reviewer.

Leaving these procedures as prose causes three problems:

1. **Prose drifts from reality.** BE-0067 found that CI had *already* drifted from `make check`. A recipe that people read and never execute rots the same way. A command is run, and the tests cover it where it guards an invariant, so it stays true.
2. **Only an AI reliably executes a long prose recipe.** A human who authors a BE item by hand, without the `ideation` skill loaded, will likely get the format, the placeholder rule, and the index interaction wrong. That is the "AI-only" dependency this item removes.
3. **The asymmetry is upside-down.** The most ceremonious, mistake-prone procedure (item scaffolding) has *no* command, while a routine check (`make check`) takes one keystroke. The guardrails that a human most needs help running are the ones least available.

The intent behind this item is to *move from "leave it to the AI" to "the human starts, the AI substitutes"*. This promotion achieves exactly that. When the guardrail is a command, the **human owns the entrypoint**. The AI substitutes *within* a human-runnable workflow and no longer serves as the sole keeper of the procedure.

## Detailed design

**Principle.** Each prose procedure becomes a `make <verb>` target backed by a small script under [`scripts/`](../../scripts/). A script that manipulates roadmap files is Python and typed, because `mypy` covers `scripts/` since BE-0067. A script that does git plumbing is shell, and joins the `SHELL_SCRIPTS` list that `make lint-sh` checks. The split matches the existing one.

The target is the single source of truth. `CLAUDE.md`, the skills, and the docs **reference the command** instead of re-describing its steps. BE-0067 used the same split when it routed CI through the Makefile. Where a procedure yields a checkable invariant, a check folds into `make check`.

Four mechanisms follow, in order of leverage.

### A. Scaffold a roadmap item — `make new-roadmap-item`

`make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Approved]` runs `scripts/new_roadmap_item.py`:

- It creates `roadmaps/proposals/BE-0069-<slug>/` with both `BE-0069-<slug>.md` and `BE-0069-<slug>-ja.md`, pre-filled from a template. The template holds the bilingual header link, the metadata block (`Proposal` / `Author` / `Status` / `Topic`), and the five Swift-Evolution sections seeded with `TBD`. The sections are `Introduction`, `Motivation`, `Detailed design`, `Alternatives considered`, and `References`.
- It **always emits the literal `BE-0069` placeholder** and never a number. IDs are permanent and monotonic, and allocation is the atomic job of CI ([BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)). A scaffolder that guessed a number would reintroduce the race that the placeholder exists to avoid.
- It validates `TOPIC` against the known section map in [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py), so the item lands in a real section. A `Topic` that matches no section makes the index builder *crash* after CI numbers the item. That is a sharp edge, worth catching at creation, and not merely a drift. The defaults are `Status=Approved` and an author resolved from `git config` (overridable via `HANDLE=`).
- It **does not add a manual index row.** The generator skips `BE-0069` items. The committed index thus stays row-free for the placeholder, and `make check` is green locally. The `roadmap-id` workflow regenerates the index once it numbers the item. This is a known trap, and the scaffolder encodes the correct flow so that an author never hits it.

The command is the human entrypoint to the procedure that the `ideation` skill performs by hand. The skill is rewritten to **call this command** and then fill the drafted sections. It no longer describes file authoring from scratch.

### B. Lint the roadmap — `make lint-roadmap`

`make lint-roadmap` runs `scripts/lint_roadmap.py` and is **folded into `make check`**. For every item, it checks the well-formedness rules that prose and reviewer judgement scatter today:

- the bilingual pair exists (both files present);
- the slug matches the directory name, and the in-file `BE-0069` or `BE-NNNN` token matches the directory;
- the metadata block is complete (`Proposal`, `Author`, `Status`, and `Topic` all present);
- `Author` is a GitHub-handle link (`[@handle](https://github.com/handle)`);
- `Status` is one of the known values and consistent with the subdirectory (`Implemented` implies `implemented/`, and every other value implies `proposals/`);
- every cross-referenced `BE-NNNN` resolves to an item that exists;
- a `BE-0069` item does not cross-reference *another* `BE-0069` item, because the per-item rewrite of the allocator cannot fix that (a documented limitation).

Today tests cover only index drift and `Status`-to-directory consistency ([`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py), [`tests/test_promote_roadmap_items.py`](../../tests/test_promote_roadmap_items.py)). The rest relies on a reviewer noticing.

The lint makes well-formedness a first-class, fast, author-time check with actionable messages. An author can run it mid-edit without the full suite. Where the lint overlaps the existing tests, the invariant consolidates here, and the tests call it.

### C. Workspace & preflight helpers — `make worktree`, `make preflight`

These two targets turn the multi-line recipes in [`docs/ai-development.md`](../../docs/ai-development.md) into commands:

- `make worktree TOPIC=<topic>` runs `git fetch origin`. It then runs `git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main`, with a configurable `<user>/` prefix. Last, it runs `make setup` in the new tree. The mandatory `git fetch origin` is baked in. The "branched off a stale `origin/main`" foot-gun that the docs warn about cannot happen.
- `make preflight` runs `git fetch origin && git rebase origin/main && make check`. It then prints the "definition of done" reminder: docs in both languages, a test with the behavior change, and `Status` flipped if shipping. The target is **advisory and human-initiated**. The pre-push hook already *gates* `make check`. `preflight` is the do-it-early version that a human runs before they think they are done, and it is not a second hard gate.

### D. Commit / PR metadata checks — `make lint-pr` (lightest)

This mechanism makes the prose-only conventions that reviewers enforce today checkable. It covers strictly the **mechanical** conventions and never the judgement calls:

- a roadmap-touching change carries the `[BE-NNNN]` (or `[BE-0069]`) prefix on its PR title;
- commit messages are scoped (`feat(scope): …`, `fix(scope): …`, or `docs: …`);
- a reminder appears when a behavior change carries no test delta.

The check stays advisory and opt-in where it needs PR context. It can run locally against the commits of the branch, or in CI against the PR title. It deliberately never blocks on the rules that no tool can mechanize, such as "stay in your lane" and the bilingual prose style. Those rules remain prose and the judgement of humans and reviewers.

### Where it lives, and what the prose becomes

- The scripts live under `scripts/`, and the new `make` targets live in the [`Makefile`](../../Makefile). `lint-roadmap` joins the `check` target, so it gates by construction.
- The prose that today *describes* these procedures is rewritten to *point at the command*. That covers the Conventions of `CLAUDE.md` and the worktree, preflight, and BE-ID sections of `docs/ai-development.md` (and its `docs/ja/` mirror). It also covers the `ideation` and `implement-be` skills, which **invoke** the commands rather than narrate the steps. Documentation changes are bilingual, per the repo rule.

### Migration, in phases

1. This proposal.
2. **B (`lint-roadmap`) first.** It is pure validation with no behavior change, and it is immediately useful. It codifies invariants that are already half enforced. Wire it into `make check`.
3. **A (`new-roadmap-item`).** It is the highest-leverage entrypoint. Update the `ideation` skill to call it.
4. **C (worktree / preflight).** Convert the doc recipes, and repoint `docs/ai-development.md` and `CLAUDE.md` at the commands.
5. **D (`lint-pr`).** It is the lightest. Start advisory, then consider a `commit-msg` hook wired by `make hooks`.

Each phase is a small, independent PR, following the parallel-work model of BE-0043.

## Alternatives considered

- **Keep the procedures as prose (status quo).** Rejected. Prose drifts (the BE-0067 finding), and only an AI reliably executes a long prose recipe. A human cannot own the entrypoint, and that is the asymmetry this item exists to remove.
- **Encode the procedures only inside the skills** (richer `ideation` prose, or a dedicated sub-agent). Rejected. That doubles down on AI-only execution, which is the thing this item moves away from. A skill that *calls* a command is good. A skill that is the *only* way to run the procedure is not.
- **Make the scaffolder allocate a real BE number.** Rejected. It violates the placeholder rule: IDs are permanent and monotonic, and allocation is the atomic, race-free job of CI ([BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)). The scaffolder must emit `BE-0069`.
- **Make `preflight` a second hard gate (a pre-commit hook running `make check`).** Rejected. The pre-push hook already gates `make check`. A per-commit gate slows the inner loop and adds no safety. `preflight` is advisory and human-initiated by design.
- **A shipped `bajutsu dev …` CLI instead of `make` targets.** Rejected for now. `make <verb>` matches every other contributor entrypoint in the repo and needs no packaging. It also keeps developer tooling out of the shipped `bajutsu` CLI surface. A consolidated dev CLI can come later if the target count grows.
- **A dedicated new roadmap topic.** The item is filed under *Contributor workflow*, the BE-0043 topic, instead. It follows the precedent of not splitting a topic for a single item ([BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)).

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
