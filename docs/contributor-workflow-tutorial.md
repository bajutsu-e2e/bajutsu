**English** · [日本語](ja/contributor-workflow-tutorial.md)

# Contributor workflow tutorial

> A hands-on walkthrough of *contributing to* Bajutsu. By the end you will have taken one idea from
> a rough sentence through `/ideation` to a merged proposal, watched CI allocate its permanent
> `BE-NNNN` id, and shipped it with `/implement-be` to a merged pull request. The
> [getting-started tutorial](getting-started/index.md) does this for *running* Bajutsu; this page does the
> analogous thing for *building* it. The reference pages tell you what each rule is; this one puts
> your hands on the keyboard, in order.

Related: [roadmap-workflow](roadmap-workflow.md) · [ai-development](ai-development.md) · [`CONTRIBUTING.md`](../CONTRIBUTING.md) · [roadmaps/README](../roadmaps/README.md)

> **What this is not.** This page does not re-explain the BE-ID lifecycle, the model tiers, or the
> PR template — those are owned by [ai-development](ai-development.md) and
> [roadmap-workflow](roadmap-workflow.md), and this tutorial links to them rather than copying them.
> Read this first to *do* your first contribution; reach for those when you want the full rule.

---

## Before you start

You need a working checkout and the gate green, exactly as in
[getting-started](getting-started/index.md#step-1--install):

```bash
uv sync --group dev   # .venv (Python 3.13) + deps + dev tools
make setup            # the above, plus wiring the tracked git hooks (run once on a fresh clone)
make check            # the deterministic gate; must be green (full step list in CLAUDE.md)
```

`make check` is the **contract**: it mirrors CI exactly, needs no Simulator, and runs anywhere. You
will run it again at the end of both halves below. Nothing in this tutorial needs an API key — the
skills run in your editor session, and the deterministic gate reaches no model.

The whole journey is two halves, each with a dedicated skill:

| Half | Skill | You start with | You end with |
|---|---|---|---|
| **Author** | `/ideation` | a rough idea | a merged proposal, with a CI-allocated `BE-NNNN` |
| **Ship** | `/implement-be BE-NNNN` | the merged proposal | a merged implementation PR, item flipped to `Implemented` |

They are deliberate counterparts — one fills the [roadmap](../roadmaps/README.md), the other empties
it. The conceptual "why two skills, not one" is in [roadmap-workflow](roadmap-workflow.md#why-two-skills-not-one);
here we just walk them.

---

## Part A — Author a proposal with `/ideation`

### Step A1 — Bring an idea, however rough

You do not need a finished design — a sentence is enough. For this walkthrough, imagine you keep
hitting a step that occasionally fails on a slow simulator and you think:

> "Bajutsu should retry a step that flakes instead of failing the whole run."

That sentence is deliberately underspecified. Part of what `/ideation` does is *sharpen* it — see the
[worked example](#a-worked-example-a-vague-idea-becomes-a-scoped-proposal) below for the before/after.

### Step A2 — Run `/ideation` and let it ground itself

In your session, invoke the skill:

```
/ideation
```

It is a **sounding board**, not a blank page. Before it proposes anything it reads the
[roadmap index](../roadmaps/README.md), the
[implementation-status table](architecture.md#implementation-status) (so it never "proposes"
something already shipped), and the BE items near your topic. Then it ideates *with* you — offering
bounded shapes and asking the questions that sharpen scope: who is it for, which tier does it touch,
and above all **what is the machine-checkable outcome** that a deterministic `run` or unit test could
verify. An idea that cannot be built inside the [prime directives](../CLAUDE.md#prime-directives-do-not-violate)
is reshaped to fit, never dropped.

It classifies each surviving idea into one of three landings and tells you which it chose:

- **overlaps an existing item** → it augments that item instead of duplicating it;
- **novel and scoped** → it drafts a new item;
- **still unformed** → it parks a bullet under *Unsorted ideas* to promote later.

### Step A3 — It drafts the item with a placeholder id

When an idea lands as "novel and scoped", the skill scaffolds the files:

```bash
make new-roadmap-item SLUG=retry-flaky-step TITLE="Bounded retry for transiently-blocked steps"
```

This creates `roadmaps/BE-XXXX-retry-flaky-step/` with **both** language files in the canonical
Swift-Evolution format, and the skill fills the `TBD` sections (Introduction, Motivation, the MECE
`Detailed design`, `Alternatives considered`) and rewrites the Japanese side as natural Japanese.

The literal `BE-XXXX` is intentional — **you never pick the number by hand.** IDs are permanent and
monotonic, and many branches are in flight at once; hand-picking races two PRs onto the same number.
CI allocates it at merge time (Step A5).

### Step A4 — Self-review, verify, and open the proposal PR

Before committing, mirror the CI "Claude review" workflow locally rather than waiting for it to
catch anything on the PR: a fresh subagent, blind to the authoring conversation the same way the
actual CI reviewer is, applies the contract at
[`.github/claude-review-prompt.md`](../.github/claude-review-prompt.md) to the staged diff and
fixes every finding — except a false positive or an already-explained trade-off (noted and left
as-is), or one that calls for a genuine design change (escalated to you instead); capped at 3
rounds. See [`ideation`](../.claude/skills/ideation/SKILL.md) step 5 for the exact procedure.

The gate stays green even for a docs-only change:

```bash
make check
```

Then push your branch and **open the PR** — a proposal PR is purely documentation, so per the
[working agreement](../CLAUDE.md) it opens **Ready for review** (not Draft) with the
`steering-committee` team as reviewer:

```bash
gh pr create --reviewer bajutsu-e2e/steering-committee \
  --title "docs(roadmap): propose bounded retry for transiently-blocked steps" \
  --body "…"
```

The title carries **no** `[BE-NNNN]` prefix — the id does not exist yet. A PR that *introduces*
a roadmap item keeps the plain scoped subject.

> **`/ideation` will not auto-open this PR for you.** A proposal is a human checkpoint, so the
> authoring skills stop at pushing the branch. You (the human) open it. This is the opposite of
> `/implement-be`, which *does* auto-open its PR (Step B5) because its output is always gate-green
> code.

### Step A5 — CI allocates the real `BE-NNNN`; merge

When the PR opens, the [`roadmap-id`](../.github/workflows/roadmap-id.yml) workflow runs
[`scripts/allocate_roadmap_ids.py`](../scripts/allocate_roadmap_ids.py): it claims the next free id
atomically, renames `BE-XXXX` → `BE-NNNN` **everywhere** (the directory, both files, cross-links),
and pushes the result back to your branch. Pull that commit. Once review is happy and the proposal
merges, the item exists at its permanent path with `Status: Proposal` — and its number is now
allocated for good. That number is the input to Part B.

---

## A worked example: a vague idea becomes a scoped proposal

The single fastest way to learn "scoped enough" is to see a weak idea and its reshaped form
side by side. This side-by-side comparison is what `/ideation`'s questions do to the Step A1 sentence.

**❌ Before — an underspecified one-liner:**

> "Add retry to flaky steps."

Why a reviewer cannot act on this, and why it brushes a prime directive:

- **No bound.** "Retry" how many times? A blind retry loop hides flakiness instead of surfacing it —
  which fights **determinism first**: a step that only passes on the third attempt is a real signal,
  not noise to paper over.
- **No trigger.** Retry *what* condition? Any failure, or only a transient, self-clearing blocker?
  The two are different features.
- **No machine-checkable outcome.** What would a deterministic test assert to prove it works? Unstated.

**✅ After — the shape `/ideation` steers you toward:**

> **Bounded retry for transiently-blocked steps.** When a step fails *because a known,
> self-clearing blocker was present* (e.g. a system alert that `on_blocked` dismisses), retry the
> step **exactly once** after the blocker clears — never a general "keep trying until it passes"
> loop. **Tier:** the deterministic run loop. **Machine-checkable outcome:** a unit test with a fake
> driver that fails a step once with a clearable blocker, then succeeds, asserts the run passes in
> exactly two attempts; a step that fails for any *other* reason still fails immediately, asserted by
> a second test. **Prime-directive check:** the retry is condition-gated and bounded, so it stays
> deterministic — it does not mask flakiness, it recovers from a *named* transient state. No LLM on
> the `run` path.

The reshaped version names its scope, the tier it touches, the exact assertion that proves it, and
the tension it had with a directive and how it resolved it. That reshaped version is a spec you can hand to
`/implement-be`. (This is, in fact, roughly how the real
["retry once after `on_blocked` clears a blocker"](run-loop.md) behavior in the run loop is bounded.)

**What "good" looks like on real, merged items — click through them:**

- [BE-0214 — Web-only beginner tutorial](../roadmaps/BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md)
  is a **docs-shaped** item: read its `Detailed design` for how a documentation change is still
  broken into a MECE checklist, and follow its `Implementing PR` link to the real diff.
- [BE-0017 — MCP server](../roadmaps/BE-0017-mcp-server/BE-0017-mcp-server.md) is a **code-shaped**
  item: its `Detailed design` enumerates the surface it adds, and its PR (`[BE-0017] feat(mcp): add
  MCP server`) shows the title convention and the item→PR back-link in practice.

Reading a merged item's proposal, its `Progress` log, and the PR it links is the most reliable way
to calibrate your own before you open one.

---

## Part B — Ship it with `/implement-be`

Now the proposal is merged and carries a real id — say `BE-0300`. The proposal's **Detailed design**
is your spec; the deterministic gate is the judge, never an LLM.

### Step B1 — Start the skill

```
/implement-be BE-0300
```

Accepts a full id, a bare number (`300`), or a slug fragment. First it **explains the item back to
you** — id, title, a plain-language summary, and its current state — then it notes that implementing
a `Proposal` *accepts* it: this PR will flip it to `Implemented`.

### Step B2 — It claims the tracking issue

Every open item has a GitHub tracking issue labeled `roadmap-tracking`. The skill checks who is
assigned: if someone else already holds it, it **stops** and tells you rather than colliding with
their work; if it is free (or already yours), it self-assigns and continues. This self-assignment is
how parallel sessions signal ownership.

### Step B3 — It grounds, plans, and asks before writing code

It reads the Detailed design and *Alternatives considered* (the latter records paths already
rejected — often for directive reasons — so they are not re-proposed), opens every file the proposal
links, and checks that any prerequisite item is not itself still a proposal. Then, because a whole
roadmap item is large and hard to reverse, it **presents a concrete plan and waits for your
go-ahead**: the files it will touch, the machine-checkable outcome that proves it works (and where AI
is and is not allowed to sit), the tests, the docs that must move in both languages, and any tension
with the prime directives. You confirm before a line of code is written.

### Step B4 — It implements, reviews, flips the item, and runs the gate

Implementation matches the codebase grain — strict `mypy`, configured `ruff`, condition waits not
`sleep`, new knobs in `targets.<name>` config, and a test as the regression net for any behavior
change. It refines the diff with the built-in `simplify` and `code-review` skills (authoring aids
that advise, never judge), flips the item to `Status: Implemented` in both language files, and adds
the `Implementing PR` row — the dashboard picks up the new status straight from that metadata, with
nothing else to regenerate. Then the gate:

```bash
make check   # must be green; never push red
```

### Step B5 — It auto-opens the PR; you merge

Unlike `/ideation`, `/implement-be` **opens its own PR** — its output is always a self-contained,
gate-green change, so there is nothing to wait for. The PR is **Draft by default**, titled with the
id (`[BE-0300] feat(run): bounded retry for transiently-blocked steps`), with a thorough body from
the [template](../.github/PULL_REQUEST_TEMPLATE.md). (A *doc-only* item is the exception — it opens
Ready with the `steering-committee` reviewer, exactly like the proposal PR did.)

From there a paced follow-up loop drives the mechanical tail — fixing CI, replying to review
comments — but **only the human marks a Draft PR ready** (`gh pr ready`) and merges. That sign-off is
deliberately yours. When it merges, the item is shipped and its `Implementing PR` row points at the
PR that shipped it.

---

## When to reach for `propose-and-build` instead

Parts A and B above are the **serial path**: propose, merge, allocate, implement. It is the default
because it forces a design to clear review *before* any code is written, and it keeps the `BE-NNNN`
sequence contiguous by only spending a number on an item that ships.

For a **small, well-scoped item whose design you do not expect review to reshape**, the serial path's
latency — the dead time between "proposal opened" and "id allocated" — is pure overhead. That is what
[`propose-and-build`](../.claude/skills/propose-and-build/SKILL.md) is for:

```
/propose-and-build
```

It composes the same two skills, but runs authoring and implementation **in parallel** as a temporary
two-PR stack — the proposal PR first, the implementation PR stacked on it. From your side as the
contributor, the hand-off looks like this:

1. You author the proposal and build the implementation at the same time, on two branches.
2. The proposal PR merges and CI allocates the real `BE-NNNN`, exactly as in Step A5.
3. The skill **rebases the implementation branch**, rewrites its `BE-XXXX` references to the allocated
   `BE-NNNN`, retargets it onto `main`, and runs `/implement-be`'s promotion + gate steps — so the
   stack collapses into an ordinary `implement-be`-shaped PR.

Take our worked example: *if* "bounded retry for transiently-blocked steps" had been small and
settled enough — a design you were confident review would not reshape — you could have stacked the
implementation on the proposal instead of waiting for the merge. The cost is real: if review *does*
change the proposal, you rework the implementation branch. So the rule of thumb is simple —

> **Serial by default. Reach for `propose-and-build` only when you are confident the design is
> settled.** When a design is genuinely uncertain, the serial path's "review before code" is a
> feature, not overhead.

The skill's own mechanics are documented in
[BE-0216](../roadmaps/BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md);
this section is the *when*, from a contributor's seat.

---

## Where to go next

You now have the full contribution loop: idea → `/ideation` → merged proposal → `/implement-be` →
merged PR, and you know when to collapse it with `/propose-and-build`. The reference pages cover each
piece in depth:

- [roadmap-workflow](roadmap-workflow.md) — the conceptual overview of the two-skill loop and *why*
  authoring and shipping are kept separate.
- [ai-development](ai-development.md) — the full operational rules: the gate, one-topic branches, the
  pre-push hook, worktrees, the strict BE-ID lifecycle, model tiers, and the PR title/body template.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — the human-contributor entry point and the environment
  setup.
- [roadmaps/README](../roadmaps/README.md) — the index of every BE item and the per-item proposal
  format you will fill in.
- [`CLAUDE.md`](../CLAUDE.md) — the working agreement and the three prime directives every change
  honors.
