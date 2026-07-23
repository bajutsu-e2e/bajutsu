**English** · [日本語](BE-0103-dev-model-effort-tiering-ja.md)

# BE-0103 — Right-size the model and reasoning effort per development task

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0103](BE-0103-dev-model-effort-tiering.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0103") |
| Implementing PR | [#441](https://github.com/bajutsu-e2e/bajutsu/pull/441) |
| Topic | Contributor workflow |
| Related | [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md), [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) |
<!-- /BE-METADATA -->

## Introduction

Match the **model and reasoning effort** a Claude Code session spends to the **cognitive load of
the task at hand**, so a session working *on this repository* stops paying top-tier tokens for
mechanical chores. This is a **contributor-workflow** item, not a product feature: it is about how
we develop Bajutsu with Claude Code, not about how Bajutsu itself calls Claude.

The deliverable is two-sided: (1) a documented **task → model/effort** convention in
[`docs/ai-development.md`](../../docs/ai-development.md) (bilingual), covering both work phases
(exploration / chores → light; implementation / design → heavy) and how to pass an appropriate
`model` when delegating to a subagent; and (2) **default `model:` frontmatter wired into the
repo's own skills** (`.claude/skills/*`) so the right choice is the automatic one — no session has
to remember to switch. It stays advisory (the human can always upshift for a hard instance); the
deterministic `run` / CI gate is untouched and still calls no model.

## Motivation

This repository is unusually agent-driven — `CLAUDE.md`, the skills (`ideation`, `implement-be`,
`japanese-tech-writing`), and subagent fan-outs are all part of the daily workflow, run by many
parallel sessions. Token spend is therefore a real, recurring cost, and today it is uniform:

1. **Nothing tells a session to downshift.** The harness already exposes every knob we need —
   per-skill and per-agent `model:` frontmatter, the Agent tool's `model` override, and the
   interactive `/model` / `/fast` (effort) controls — yet none of the in-repo skills declares a
   `model:`, and no documented guidance says which phase warrants which capability. So a session
   runs whatever it started on (typically the most capable model at high effort) across the board.

2. **The tasks span a wide capability range.** Implementing a BE item, a non-trivial refactor, or a
   design decision genuinely needs a capable model at high effort. Regenerating the roadmap index,
   fixing doc links, a mechanical rename, or drafting a first-pass translation does not — a smaller
   model at low effort produces the same artifact for a fraction of the tokens. Running the light
   tasks at full capability is pure waste.

3. **The failure mode is asymmetric and silent.** Over-provisioning wastes tokens invisibly (the
   output still looks fine); under-provisioning shows up loudly as a bad result. So the natural
   drift is toward *always-max*, which is exactly the waste to remove — but blindly downshifting
   everywhere risks quality on the hard tasks. The fix is a **deliberate, documented mapping** plus
   **sensible baked-in defaults**, not an ad-hoc per-session guess.

Closing this turns "use a cheaper model for the easy stuff" from folklore a diligent session might
remember into a stated convention with defaults that apply themselves.

## Detailed design

Contributor-workflow altitude; touches only `.claude/` skill metadata and docs. The work is MECE
along four pieces.

### 1. A task → capability classification (the matrix)

Define one authoritative mapping of recurring development tasks to a **capability tier**, expressed
on two axes — **model** (capable ↔ cheap) and **reasoning effort** (high ↔ low/none) — and
documented once as the single source of truth the other pieces reflect. The initial tiers:

- **Heavy** (capable model, high effort): implementing a BE item (`implement-be`), non-trivial
  refactors, architecture / design decisions, and debugging a failing gate.
- **Medium** (mid model, moderate effort): roadmap ideation / authoring (`ideation`), Japanese
  technical writing and translation review (`japanese-tech-writing`), and PR review.
- **Light** (cheap model, low or no effort): roadmap index regeneration / promote, doc formatting
  and link fixes, mechanical renames, lockfile / format chores, and drafting a first-pass
  translation before the human/medium-tier review pass.

The specific model ids are chosen at implementation time against the current Claude line-up; the
matrix pins the *tier* per task and the mapping tier → id, so re-pointing to a new model is a
one-line change in one place.

### 2. Default `model:` frontmatter on the repo's own skills

Add a `model:` field to each in-repo skill's `SKILL.md` frontmatter matching its tier from piece 1
— `implement-be` → heavy, `ideation` → medium, `japanese-tech-writing` → medium. The harness reads
this directly when the skill runs, so the default takes effect with nothing to remember, and it is
still overridable (a session can upshift for a genuinely hard instance). This is the "make the
right choice automatic" half the user explicitly wanted over docs-only guidance.

### 3. Phase & subagent guidance in `docs/ai-development.md` (bilingual)

Add a short "which model / effort for which phase" section to `docs/ai-development.md` and its
`docs/ja/` mirror, covering the interactive and delegated work the skill frontmatter can't reach:

- **Phases within a session** — downshift (or `/fast`) for exploration, research, and mechanical
  chores; upshift for implementation and design.
- **Subagent delegation** — when spawning a subagent via the Agent tool, pass the `model` that
  matches the *delegated* task, not the driver's: a broad `Explore` fan-out or an index
  regeneration can run cheaper than the session driving it. This is the only lever for the
  out-of-repo review plugins (`pr-review-toolkit`), whose files we don't own — we set their model
  at spawn time, not in their frontmatter.

Point `CLAUDE.md` at the new section so it is discoverable from the working agreement.

### 4. Keep it advisory — no gate enforcement

Which model a session used is **not recoverable from the diff**, so the gate can't verify it, and
hard-pinning would remove the human's ability to upshift for a hard instance of an otherwise-light
task. This ships as documented defaults + frontmatter, deliberately *not* a CI check. The one
machine-checkable surface worth a light test is that each skill's `model:` value is a syntactically
valid, known model id (so a typo fails locally rather than silently falling back) — the convention
itself stays guidance, consistent with how the rest of the contributor workflow (BE-0069) is
"procedures as commands", not gate-enforced policy.

### Prime-directive compliance

Purely contributor-workflow. It edits only `.claude/` skill metadata and docs — no product code,
no `bajutsu/` change. The deterministic `run` / CI gate is untouched: it calls no model regardless
of what a *development* session runs at, so this never brings an LLM near the judge role
(directive 1), and it has nothing to do with determinism (directive 2) or app-agnosticism
(directive 3).

## Alternatives considered

- **Do nothing — let each session pick manually.** Rejected: the silent-waste asymmetry means the
  default drifts to always-max, and an unstated convention is not followed. This is the status quo
  the item exists to fix.
- **Document only, no frontmatter defaults.** Considered and set aside by the author: a docs-only
  convention relies on every session (human or agent) remembering to switch. Baking the default
  into the skill frontmatter makes the economical choice the automatic one, and the docs still
  carry the phase/subagent guidance the frontmatter can't express.
- **Enforce model choice in the gate.** Rejected: the model used isn't recoverable from the diff,
  and hard-pinning fights the human's judgment to upshift when a "light" task turns out hard.
  Guidance + defaults captures most of the saving without the rigidity.
- **Build a custom model router / automatic selector.** Rejected as over-engineering: the harness
  already exposes per-skill and per-subagent model selection and interactive effort control. The
  gap is that we don't *use* those knobs, not that they're missing.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Task → capability classification (the matrix): tiers, per-task assignment, tier → model-id mapping — documented once in `docs/ai-development.md` (+ `docs/ja/` mirror) as the single source of truth.
- [x] Default `model:` frontmatter on the in-repo skills — `implement-be` → `opus`, `ideation` → `sonnet`, `japanese-tech-writing` → `sonnet`.
- [x] Phase & subagent model/effort guidance in `docs/ai-development.md` (+ `docs/ja/` mirror), linked from `CLAUDE.md`.
- [x] Light validation that each skill's `model:` is a known, valid model id — `tests/test_skill_models.py`.

All four pieces shipped together in one change: the matrix and phase/subagent guidance in
`docs/ai-development.md` (+ its `docs/ja/` mirror), the `model:` frontmatter defaults on the three
in-repo skills, the discoverability link from `CLAUDE.md`, and the `tests/test_skill_models.py`
validation.

## References

`.claude/skills/ideation/SKILL.md` · `.claude/skills/implement-be/SKILL.md` ·
`.claude/skills/japanese-tech-writing/SKILL.md` (the frontmatter that gains a `model:` default),
[`CLAUDE.md`](../../CLAUDE.md) and [`docs/ai-development.md`](../../docs/ai-development.md)
(the working-agreement and contributor-guide surfaces the convention lives in), the Claude Code
per-skill / per-subagent `model:` frontmatter, the Agent tool `model` override, and the interactive
`/model` / `/fast` (effort) controls; [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
(executable contributor guardrails — the "procedures as commands, advisory not gate-enforced"
precedent) and [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
(the conflict-resistant file-flow that keeps parallel sessions cheap).
